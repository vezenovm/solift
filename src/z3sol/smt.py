"""The z3 back end: a model as integer terms, with the exact condition under which its Solidity reverts.

`terms` runs the function's statements in order. `reached` is the condition under which a statement runs. An expression
gives (value, reverts, outside), and a statement's reverts and outside count only where it is reached. Each return adds
(reached, value). A function that ends without a return returns 0 or false, as Solidity does. An internal call runs the
callee's statements on the argument values, and gives the callee's value, reverts and outside.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import z3

from z3sol.ir import BOOL, Call, Const, Expr, Function, If, Let, Model, Op, Return, Stmt, Var
from z3sol.ops import TABLE

FALSE = z3.BoolVal(False)
TRUE = z3.BoolVal(True)


@dataclass
class Terms:
    """`locals` holds each local of the modeled function, where it is declared. `comparisons` holds the two operand
    values of each comparison and each min, for the boundary vectors."""

    value: z3.ExprRef
    reverts: z3.BoolRef
    outside: z3.BoolRef
    locals: dict[str, z3.ExprRef]
    comparisons: list[tuple[z3.ExprRef, z3.ExprRef]] = field(default_factory=list)


def symbols(model: Model) -> dict[str, z3.ExprRef]:
    """A z3 constant for each input, named after it."""
    return {v.name: z3.Bool(v.name) if v.type == BOOL else z3.Int(v.name) for v in model.inputs}


def domain(model: Model, inputs: dict[str, z3.ExprRef]) -> z3.BoolRef:
    """Each unsigned input lies in the range of its type."""
    bounds = [z3.And(inputs[v.name] >= 0, inputs[v.name] < v.type.bound) for v in model.inputs if v.type != BOOL]
    return z3.And(*bounds)


def terms(model: Model, inputs: dict[str, z3.ExprRef]) -> Terms:
    run = _Run(model, inputs)
    value, reverts, outside = run.call(model.function, {}, top=True)
    return Terms(value, reverts, outside, run.locals, run.comparisons)


def _or(a, b):
    return b if z3.is_false(a) else a if z3.is_false(b) else z3.Or(a, b)


def _and(a, b):
    return b if z3.is_true(a) else a if z3.is_true(b) else z3.And(a, b)


@dataclass
class _Body:
    reverts: z3.BoolRef = FALSE
    outside: z3.BoolRef = FALSE
    returns: list = field(default_factory=list)

    def runs(self, reached, reverts, outside) -> None:
        self.reverts = _or(self.reverts, _and(reached, reverts))
        self.outside = _or(self.outside, _and(reached, outside))


class _Run:
    def __init__(self, model: Model, inputs: dict[str, z3.ExprRef]):
        self.inputs = inputs
        self.functions = {f.name: f for f in model.callees}
        self.locals: dict[str, z3.ExprRef] = {}
        self.comparisons: list[tuple[z3.ExprRef, z3.ExprRef]] = []
        self.stack: list[str] = []

    def call(self, function: Function, env: dict, top: bool = False):
        if function.name in self.stack:
            raise ValueError(f"{function.name} calls itself")
        self.stack.append(function.name)
        body = _Body()
        reached = self.block(function.body, env, TRUE, body, top)
        self.stack.pop()
        returns = body.returns
        if not z3.is_false(reached):
            returns = returns + [(reached, FALSE if function.returns == BOOL else z3.IntVal(0))]
        value = returns[-1][1]
        for cond, v in reversed(returns[:-1]):
            value = z3.If(cond, v, value)
        return value, body.reverts, body.outside

    def block(self, stmts: tuple[Stmt, ...], env: dict, reached, body: _Body, top: bool):
        """Runs the statements from `reached`, and returns the condition under which the block ends without a return."""
        env = dict(env)
        for stmt in stmts:
            if z3.is_false(reached):
                break
            if isinstance(stmt, Let):
                v, r, o = self.expr(stmt.expr, env)
                body.runs(reached, r, o)
                env[stmt.var.name] = v
                if top:
                    if stmt.var.name in self.locals:
                        raise ValueError(f"local {stmt.var.name} is declared twice")
                    self.locals[stmt.var.name] = v
            elif isinstance(stmt, Return):
                v, r, o = self.expr(stmt.expr, env)
                body.runs(reached, r, o)
                body.returns.append((reached, v))
                reached = FALSE
            elif isinstance(stmt, If):
                c, r, o = self.expr(stmt.cond, env)
                body.runs(reached, r, o)
                after_then = self.block(stmt.then, env, _and(reached, c), body, top)
                after_else = self.block(stmt.orelse, env, _and(reached, z3.Not(c)), body, top)
                reached = _or(after_then, after_else)
            else:
                raise TypeError(f"no statement {stmt!r}")
        return reached

    def expr(self, e: Expr, env: dict):
        if isinstance(e, Var):
            return (self.inputs if e.input else env)[e.name], FALSE, FALSE
        if isinstance(e, Const):
            return (z3.BoolVal(e.value) if e.type == BOOL else z3.IntVal(e.value)), FALSE, FALSE
        if isinstance(e, Call):
            args = [self.expr(arg, env) for arg in e.args]
            callee = self.functions[e.function]
            v, r, o = self.call(callee, {p.name: a[0] for p, a in zip(callee.params, args, strict=True)})
            return v, _any([a[1] for a in args] + [r]), _any([a[2] for a in args] + [o])
        if not isinstance(e, Op):
            raise TypeError(f"no expression {e!r}")
        if e.op == "ite":
            (c, rc, oc), (a, ra, oa), (b, rb, ob) = (self.expr(arg, env) for arg in e.args)
            return z3.If(c, a, b), _or(rc, z3.If(c, ra, rb)), _or(oc, z3.If(c, oa, ob))
        if e.op in ("and", "or"):
            (a, ra, oa), (b, rb, ob) = (self.expr(arg, env) for arg in e.args)
            runs_b = a if e.op == "and" else z3.Not(a)
            value = z3.And(a, b) if e.op == "and" else z3.Or(a, b)
            return value, _or(ra, _and(runs_b, rb)), _or(oa, _and(runs_b, ob))
        parts = [self.expr(arg, env) for arg in e.args]
        values = [p[0] for p in parts]
        operator = TABLE[e.op]
        if (operator.kind == "compare" or e.op == "min") and all(arg.type != BOOL for arg in e.args):
            self.comparisons.append((values[0], values[1]))
        reverts = [p[1] for p in parts] + ([operator.reverts(*values)] if operator.reverts else [])
        outside = [p[2] for p in parts] + ([operator.outside(*values)] if operator.outside else [])
        return operator.value(*values), _any(reverts), _any(outside)


def _any(terms: list) -> z3.BoolRef:
    terms = [t for t in terms if not z3.is_false(t)]
    if not terms:
        return FALSE
    return terms[0] if len(terms) == 1 else z3.Or(*terms)
