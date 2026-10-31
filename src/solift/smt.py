"""The z3 back end: a model as integer terms, with the exact condition under which its Solidity reverts.

`terms` runs the function's statements in order. `reached` is the condition under which a statement runs. An expression
gives (value, reverts, outside), and a statement's reverts and outside count only where it is reached. Each return adds
(reached, value), and a revert statement reverts where it is reached. After an `if`, each local that a branch assigned
takes the value of the branch that ran. A function that ends without a return returns its named return variables, or 0
or false, as Solidity does. An internal call runs the callee's statements on the argument values, and gives the callee's
value, reverts and outside. An address or an enum member is its integer value. The value of a function of several
returns, and of a tuple, is a Python tuple of terms, one for each part.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import z3

from solift.ir import (
    BOOL,
    Assign,
    Call,
    Const,
    Expr,
    Function,
    If,
    Let,
    Model,
    Op,
    Return,
    Revert,
    Stmt,
    Tuple,
    Type,
    Unpack,
    Var,
    parts,
)
from solift.ops import TABLE, U256

FALSE = z3.BoolVal(False)
TRUE = z3.BoolVal(True)


@dataclass
class Terms:
    """`locals` holds the value of each local of the modeled function, and of its named return variable, where the
    function returns. `declared` holds each local's value where it is declared. A local that a return does not reach
    takes there the value of a return that does. `comparisons` holds the condition under which each comparison and each
    min or max runs and its two operand values, for the boundary vectors.

    Three terms say where an operation leaves its lemma's domain. `outside` is where an operation that runs reaches the
    table's outside, which `inside` assumes away. `lemma` is where an operation that runs reaches it or takes an operand
    out of the uint256 range, which an operand after a revert on its path can be: the obligation of a theorem that
    reads the result. `anywhere` is the same whether or not the operation runs: a local's value is a term also where its
    statement does not run, so a theorem that reads a local rests on the lemma there too."""

    value: z3.ExprRef | tuple
    reverts: z3.BoolRef
    outside: z3.BoolRef
    locals: dict[str, z3.ExprRef]
    declared: dict[str, z3.ExprRef] = field(default_factory=dict)
    comparisons: list[tuple[z3.BoolRef, z3.ExprRef, z3.ExprRef]] = field(default_factory=list)
    anywhere: z3.BoolRef = FALSE
    lemma: z3.BoolRef = FALSE


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
    return Terms(
        value, reverts, outside, run.locals, run.declared, run.comparisons, _any(run.anywhere), _any(run.lemmas)
    )


def terms_of_call(model: Model, function: Function, inputs: dict[str, z3.ExprRef], args) -> Terms:
    """The value, reverts and outside of a called function of the model, on argument terms or numbers."""
    if len(args) != len(function.params):
        raise TypeError(f"{function.name} takes {len(function.params)} arguments, not {len(args)}")
    env = {}
    for param, arg in zip(function.params, args, strict=True):
        env[param.name] = arg if z3.is_expr(arg) else z3.BoolVal(arg) if param.type == BOOL else z3.IntVal(arg)
    run = _Run(model, inputs)
    value, reverts, outside = run.call(function, env)
    return Terms(value, reverts, outside, {}, anywhere=_any(run.anywhere), lemma=_any(run.lemmas))


def _or(a, b):
    return b if z3.is_false(a) else a if z3.is_false(b) else z3.Or(a, b)


def _and(a, b):
    return b if z3.is_true(a) else a if z3.is_true(b) else z3.And(a, b)


def _zero(type_: Type) -> z3.ExprRef | tuple:
    if type_.kind == "tuple":
        return tuple(_zero(part) for part in type_.parts)
    return FALSE if type_ == BOOL else z3.IntVal(0)


def _select(cases: list) -> z3.ExprRef | tuple:
    """The value of the case whose condition holds, of (condition, value) cases whose conditions are disjoint, and of
    each part for tuple values. The last case is the default."""
    if isinstance(cases[-1][1], tuple):
        return tuple(_select([(cond, v[n]) for cond, v in cases]) for n in range(len(cases[-1][1])))
    value = cases[-1][1]
    for cond, v in reversed(cases[:-1]):
        value = value if v.eq(value) else z3.If(cond, v, value)
    return value


def _end(function: Function, env: dict) -> z3.ExprRef | tuple:
    """The value of a function that ends without a return: its named return variables, and zero for a value without a
    name."""
    if not function.results:
        return _zero(function.returns)
    pairs = zip(function.results, parts(function.returns), strict=True)
    values = tuple(_zero(t) if v is None else env[v.name] for v, t in pairs)
    return values[0] if len(values) == 1 else values


@dataclass
class _Body:
    """`returns` holds (reached, value, locals) for each return that runs."""

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
        self.declared: dict[str, z3.ExprRef] = {}
        self.comparisons: list[tuple[z3.BoolRef, z3.ExprRef, z3.ExprRef]] = []
        self.anywhere: list[z3.BoolRef] = []
        self.lemmas: list[z3.BoolRef] = []
        self.stack: list[str] = []
        # The condition under which the expression being evaluated runs, from the modeled function's start, and the
        # condition at the start of each function on the stack.
        self.guard: z3.BoolRef = TRUE
        self.entries: list[z3.BoolRef] = []

    def call(self, function: Function, env: dict, top: bool = False):
        if function.name in self.stack:
            raise ValueError(f"{function.name} calls itself")
        self.stack.append(function.name)
        body = _Body()
        env = dict(env)
        for result in function.named:
            env[result.name] = _zero(result.type)
        self.entries.append(self.guard)
        reached, env = self.block(function.body, env, TRUE, body, top)
        self.guard = self.entries.pop()
        self.stack.pop()
        returns = body.returns
        if not z3.is_false(reached):
            returns = returns + [(reached, _end(function, env), env)]
        if not returns:
            # Every path reverts.
            return _zero(function.returns), body.reverts, body.outside
        if top:
            names = {name for _, _, scope in returns for name in scope}
            for name in sorted(names):
                self.locals[name] = _select([(cond, scope[name]) for cond, _, scope in returns if name in scope])
        return _select([(cond, value) for cond, value, _ in returns]), body.reverts, body.outside

    def block(self, stmts: tuple[Stmt, ...], env: dict, reached, body: _Body, top: bool):
        """Runs the statements from `reached`. Returns the condition under which the block ends without a return, and
        the values of the locals there."""
        env = dict(env)
        for stmt in stmts:
            if z3.is_false(reached):
                break
            self.guard = _and(self.entries[-1], reached)
            if isinstance(stmt, Let | Assign):
                if isinstance(stmt, Let) and stmt.var.name in env:
                    raise ValueError(f"local {stmt.var.name} shadows a variable of the same name")
                v, r, o = self.expr(stmt.expr, env)
                body.runs(reached, r, o)
                env[stmt.var.name] = v
                if top and isinstance(stmt, Let):
                    self._declare(stmt.var, v)
            elif isinstance(stmt, Unpack):
                v, r, o = self.expr(stmt.expr, env)
                body.runs(reached, r, o)
                for var, part in zip(stmt.vars, v, strict=True):
                    if var is None:
                        continue
                    if stmt.declare and var.name in env:
                        raise ValueError(f"local {var.name} shadows a variable of the same name")
                    env[var.name] = part
                    if top and stmt.declare:
                        self._declare(var, part)
            elif isinstance(stmt, Return):
                v, r, o = self.expr(stmt.expr, env)
                body.runs(reached, r, o)
                body.returns.append((reached, v, env))
                reached = FALSE
            elif isinstance(stmt, Revert):
                body.runs(reached, TRUE, FALSE)
                reached = FALSE
            elif isinstance(stmt, If):
                c, r, o = self.expr(stmt.cond, env)
                body.runs(reached, r, o)
                after_then, then_env = self.block(stmt.then, env, _and(reached, c), body, top)
                after_else, else_env = self.block(stmt.orelse, env, _and(reached, z3.Not(c)), body, top)
                merged = {}
                for name in env:
                    a, b = then_env[name], else_env[name]
                    if z3.is_false(after_then) or a.eq(b):
                        merged[name] = b
                    elif z3.is_false(after_else):
                        merged[name] = a
                    else:
                        merged[name] = z3.If(after_then, a, b)
                env = merged
                reached = _or(after_then, after_else)
            else:
                raise TypeError(f"no statement {stmt!r}")
        return reached, env

    def _declare(self, var: Var, value: z3.ExprRef) -> None:
        if var.name in self.declared:
            raise ValueError(f"local {var.name} is declared twice")
        self.declared[var.name] = value

    def guarded(self, condition: z3.BoolRef, e: Expr, env: dict):
        """The expression, evaluated where it runs only under `condition`, such as a branch of a ternary."""
        outer = self.guard
        self.guard = _and(outer, condition)
        try:
            return self.expr(e, env)
        finally:
            self.guard = outer

    def expr(self, e: Expr, env: dict):
        if isinstance(e, Var):
            return (self.inputs if e.input else env)[e.name], FALSE, FALSE
        if isinstance(e, Const):
            return (z3.BoolVal(e.value) if e.type == BOOL else z3.IntVal(e.value)), FALSE, FALSE
        if isinstance(e, Tuple):
            args = [self.expr(arg, env) for arg in e.args]
            return tuple(a[0] for a in args), _any([a[1] for a in args]), _any([a[2] for a in args])
        if isinstance(e, Call):
            args = [self.expr(arg, env) for arg in e.args]
            callee = self.functions[e.function]
            v, r, o = self.call(callee, {p.name: a[0] for p, a in zip(callee.params, args, strict=True)})
            return v, _any([a[1] for a in args] + [r]), _any([a[2] for a in args] + [o])
        if not isinstance(e, Op):
            raise TypeError(f"no expression {e!r}")
        if e.op == "ite":
            c, rc, oc = self.expr(e.args[0], env)
            a, ra, oa = self.guarded(c, e.args[1], env)
            b, rb, ob = self.guarded(z3.Not(c), e.args[2], env)
            return z3.If(c, a, b), _or(rc, z3.If(c, ra, rb)), _or(oc, z3.If(c, oa, ob))
        if e.op in ("and", "or"):
            a, ra, oa = self.expr(e.args[0], env)
            runs_b = a if e.op == "and" else z3.Not(a)
            b, rb, ob = self.guarded(runs_b, e.args[1], env)
            value = z3.And(a, b) if e.op == "and" else z3.Or(a, b)
            return value, _or(ra, _and(runs_b, rb)), _or(oa, _and(runs_b, ob))
        parts = [self.expr(arg, env) for arg in e.args]
        values = [p[0] for p in parts]
        operator = TABLE[e.op]
        if e.op == "narrow":
            return values[0] % e.type.bound, parts[0][1], parts[0][2]
        if (operator.kind == "compare" or e.op in ("min", "max")) and all(arg.type != BOOL for arg in e.args):
            self.comparisons.append((self.guard, values[0], values[1]))
        if operator.outside:
            leaves = _anywhere(operator, values)
            self.anywhere.append(leaves)
            self.lemmas.append(_and(self.guard, leaves))
        reverts = [p[1] for p in parts] + ([operator.reverts(*values)] if operator.reverts else [])
        outside = [p[2] for p in parts] + ([operator.outside(*values)] if operator.outside else [])
        return operator.value(*values), _any(reverts), _any(outside)


def _anywhere(operator, values: list) -> z3.BoolRef:
    """Where an operation leaves its lemma's domain on any path: the table's outside, or an operand out of the uint256
    range, which an operand on a path that does not run, or that already reverted, can be. The model's `outside`, which
    `inside` assumes away, is the table's outside where the operation runs, and nothing more."""
    return z3.Or(operator.outside(*values), *(z3.Not(z3.And(v >= 0, v < U256)) for v in values))


def _any(terms: list) -> z3.BoolRef:
    terms = [t for t in terms if not z3.is_false(t)]
    if not terms:
        return FALSE
    return terms[0] if len(terms) == 1 else z3.Or(*terms)
