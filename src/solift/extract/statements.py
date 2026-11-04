"""Functions and their statements: declarations, assignments, tuples, `require`, and returns."""

from __future__ import annotations

import dataclasses
import re

from solift import ops
from solift.extract.common import _STORAGE, ASSIGNMENTS, _normal, _root, _storage_struct
from solift.extract.reads import _assigned
from solift.ir import (
    BOOL,
    Assign,
    Const,
    Expr,
    Function,
    If,
    Let,
    Return,
    Revert,
    Stmt,
    Tuple,
    Type,
    Unpack,
    Var,
    parts,
    tuple_of,
)

_STATEMENT_ERRORS = {
    "ForStatement": "a loop",
    "WhileStatement": "a loop",
    "DoWhileStatement": "a loop",
    "UncheckedBlock": "an unchecked block",
    "InlineAssembly": "inline assembly",
    "Block": "a nested block",
    "TryStatement": "a try statement",
}


def _statements(node) -> list:
    return node["statements"] if node["nodeType"] == "Block" else [node]


_SIGNED = re.compile(r"int\d*")


def _written(type_: Type) -> str:
    return f"({type_.solidity})" if type_.kind == "tuple" else type_.solidity


class Statements:
    def _function(self, node, top: bool, bindings: dict | None = None) -> Function:
        """The function. `bindings` gives each storage struct parameter of a callee the storage of its argument."""
        name = node["name"]
        if node["kind"] != "function" or not node["implemented"]:
            self.fail(node, f"{name} is not an implemented function")
        if node["modifiers"]:
            self.fail(node["modifiers"][0], f"{name} has a modifier")
        returns = node["returnParameters"]["parameters"]
        if not returns and not top:
            self.fail(node["returnParameters"], f"{name} returns no value")
        # A modeled function without a value has its outputs only, which come after its own values (_with_outputs).
        return_type = tuple_of(tuple(self._type(r) for r in returns)) if returns else Type("tuple")
        for p in (*node["parameters"]["parameters"], *returns):
            self._not_inputs_name(p)
        scope = {}
        results = ()
        if any(r["name"] for r in returns):
            results = tuple(Var(r["name"], self._type(r), src=self.src(r)) if r["name"] else None for r in returns)
            scope.update({r["id"]: v for r, v in zip(returns, results, strict=True) if v is not None})
            scope["return"] = results
        params, copies = [], []
        # A parameter of the modeled function that the body assigns is an input, its value at the call, and a local of
        # the same name that starts at it. The body reads and assigns the local.
        assigned = _assigned(node["body"]) if top else set()
        for p in node["parameters"]["parameters"]:
            if _storage_struct(p):
                scope[p["id"]] = ("pointer", p["name"], None, (p["id"],)) if top else ("pointer", *bindings[p["id"]])
                continue
            if p["typeDescriptions"]["typeString"].startswith("struct ") and p.get("storageLocation") == "memory":
                if not top:
                    self.fail(p, f"the struct parameter {p['name']} of a called function")
                scope[p["id"]] = ("param", p["name"])
                continue
            if top and (self._user_value_type(p) or _SIGNED.fullmatch(p["typeDescriptions"]["typeString"])):
                # A parameter of a type that the model has no value of: only a read map key or an argument of an
                # effect may name it.
                scope[p["id"]] = ("opaque", p["name"])
                continue
            var = Var(p["name"], self._type(p), input=top, src=self.src(p))
            scope[p["id"]] = var
            params.append(var)
            if p["id"] in assigned:
                local = Var(var.name, var.type, src=var.src)
                copies.append(Let(local, var, var.src))
                scope[p["id"]] = local
        if top:
            self.parameter_locals = {let.var.name for let in copies}
        body = (*copies, *self._block(node["body"]["statements"], scope, return_type))
        return Function(name, tuple(params), return_type, body, self.src(node), results)

    def _user_value_type(self, param) -> bool:
        name = param.get("typeName") or {}
        if name.get("nodeType") != "UserDefinedTypeName":
            return False
        decl = self.nodes.get(name.get("referencedDeclaration"))
        return decl is not None and decl["nodeType"] == "UserDefinedValueTypeDefinition"

    def _block(self, statements, scope: dict, return_type: Type) -> tuple[Stmt, ...]:
        scope = dict(scope)
        out = []
        for node in statements:
            kind = node["nodeType"]
            if kind == "VariableDeclarationStatement":
                stmt = self._declaration(node, scope)
                if stmt is not None:
                    out.append(stmt)
            elif kind == "IfStatement":
                cond = self._expr(node["condition"], scope)
                if cond.type != BOOL:
                    self.fail(node["condition"], "the condition is not a bool")
                then = self._block(_statements(node["trueBody"]), scope, return_type)
                orelse = ()
                if node.get("falseBody") is not None:
                    orelse = self._block(_statements(node["falseBody"]), scope, return_type)
                out.append(If(cond, then, orelse, self.src(node)))
            elif kind == "Return":
                if node.get("expression") is None and not parts(return_type):
                    out.append(Return(Tuple((), return_type), self.src(node)))
                    continue
                if node.get("expression") is None:
                    if "return" not in scope:
                        self.fail(node, "a return without a value")
                    out.append(Return(self._named_results(scope["return"], return_type), self.src(node)))
                    continue
                self.statement_value = node["expression"]["id"]
                value = self._expr(node["expression"], scope)
                self._assignable(node["expression"], value.type, return_type)
                out.append(Return(value, self.src(node)))
            elif kind == "ExpressionStatement":
                stmt = self._expression_statement(node["expression"], scope)
                if isinstance(stmt, tuple):
                    out += stmt
                elif stmt is not None:
                    out.append(stmt)
            elif kind == "RevertStatement":
                out.append(Revert(self.src(node)))
            elif kind == "EmitStatement":
                for arg in node["eventCall"]["arguments"]:
                    self._cannot_revert(arg, scope)
                self._effect(node, "event", self._event(node["eventCall"]))
            else:
                self.fail(node, _STATEMENT_ERRORS.get(kind, f"a {kind} statement is not supported"))
        return tuple(out)

    def _named_results(self, results: tuple, return_type: Type) -> Expr:
        """The value of a bare `return;`: the named return variables, and zero for a value without a name."""
        values = tuple(
            v if v is not None else Const(False, BOOL, text="false") if t == BOOL else Const(0, t, text="0")
            for v, t in zip(results, parts(return_type), strict=True)
        )
        return values[0] if len(values) == 1 else Tuple(values, return_type)

    def _declaration(self, node, scope: dict) -> Let | Unpack | None:
        declarations = node["declarations"]
        if len(declarations) != 1:
            return self._unpack(node, scope)
        decl = declarations[0]
        init = node.get("initialValue")
        type_string = decl["typeDescriptions"]["typeString"]
        if type_string.startswith("struct ") and decl.get("storageLocation") == "memory":
            path = self._storage_path(init, scope) if init is not None else None
            if path is None:
                self.fail(init or node, "a memory struct that is not a copy of a storage struct")
            self._storage_read(path[1], init)
            scope[decl["id"]] = ("copy", *path, self._identity(init, scope))
            return None
        if _storage_struct(decl):
            path = self._storage_path(init, scope) if init is not None else None
            if path is None:
                self.fail(init or node, f"a storage pointer to {self.text(init or node)}: {_STORAGE}")
            scope[decl["id"]] = ("pointer", *path, self._identity(init, scope))
            return None
        var = self._local(decl, scope)
        if init is None:
            if var.type.kind not in ("uint", "bool"):
                self.fail(node, f"a local of type {var.type.solidity} declared without a value")
            value = Const(False, BOOL, text="false") if var.type == BOOL else Const(0, var.type, text="0")
        else:
            self.statement_value = init["id"]
            value = self._expr(init, scope)
            self._assignable(init, value.type, var.type)
        scope[decl["id"]] = var
        return Let(var, value, self.src(node))

    def _local(self, decl, scope: dict) -> Var:
        self._not_inputs_name(decl)
        var = Var(decl["name"], self._type(decl), src=self.src(decl))
        if any(isinstance(v, Var) and v.name == var.name for v in scope.values()):
            self.fail(decl, f"the local {var.name} shadows a parameter, local or return of the same name")
        return var

    def _unpack(self, node, scope: dict) -> Unpack:
        """A tuple declaration, as `(, uint256 y) = limits(a, b);`."""
        init = node.get("initialValue")
        if init is None:
            self.fail(node, "a tuple declaration without a value")
        self.statement_value = init["id"]
        value = self._expr(init, scope)
        declarations = node["declarations"]
        self._parts(init, value, len(declarations))
        vars_ = tuple(None if decl is None else self._local(decl, scope) for decl in declarations)
        for var, part in zip(vars_, parts(value.type), strict=True):
            if var is not None:
                self._assignable(init, part, var.type)
        for decl, var in zip(declarations, vars_, strict=True):
            if var is not None:
                scope[decl["id"]] = var
        names = [v.name for v in vars_ if v is not None]
        if len(set(names)) != len(names):
            self.fail(node, "a tuple declaration declares one name twice")
        return Unpack(vars_, value, True, self.src(node))

    def _parts(self, node, value: Expr, count: int) -> None:
        if value.type.kind != "tuple" or len(value.type.parts) != count:
            self.fail(node, f"{self.text(node)} is not a tuple of {count} values")

    def _not_inputs_name(self, decl) -> None:
        if decl["name"] == "i":
            self.fail(decl, "i names the inputs struct of the emitted Solidity")

    def _expression_statement(self, expression, scope: dict) -> Stmt | None:
        kind = expression["nodeType"]
        if kind == "Assignment":
            return self._assignment(expression, scope)
        callee = expression.get("expression") or {}
        if kind == "FunctionCall" and callee.get("name") in ("require", "assert", "revert"):
            if callee.get("referencedDeclaration", 0) < 0:
                return self._require(expression, scope)
        if kind == "UnaryOperation" and expression["operator"] in ("++", "--", "delete"):
            self.fail(expression, f"{expression['operator']} is not supported")
        if kind == "FunctionCall" and expression["kind"] == "functionCall":
            if self._external(expression):
                return self._external_call(expression, scope)
            name = callee.get("name") or callee.get("memberName")
            self.fail(expression, f"a call to {name} as a statement: an internal call is taken only as a value")
        self.fail(expression, "an expression statement")

    def _require(self, call, scope: dict) -> Stmt:
        """`revert(...)` reverts, and `require(c, ...)` and `assert(c)` revert where c is false. A message of require
        must be a string literal or an error whose arguments cannot revert, so that it reverts only where c is false."""
        name = call["expression"]["name"]
        if name == "revert":
            return Revert(self.src(call))
        args = call["arguments"]
        cond = self._expr(args[0], scope)
        if cond.type != BOOL:
            self.fail(args[0], "the condition is not a bool")
        if len(args) == 2:
            message = args[1]
            error = self.nodes.get((message.get("expression") or {}).get("referencedDeclaration"))
            if message["nodeType"] == "FunctionCall" and error is not None and error["nodeType"] == "ErrorDefinition":
                for arg in message["arguments"]:
                    self._cannot_revert(arg, scope)
            elif message["nodeType"] != "Literal" or message["kind"] not in ("string", "unicodeString"):
                self.fail(message, f"the message of {name} is not a string literal or an error")
        negated = dataclasses.replace(ops.make("not", cond), src=self.src(call))
        return If(negated, (Revert(self.src(call)),), (), self.src(call))

    def _assignment(self, expression, scope: dict) -> Assign | Unpack | None:
        lhs = expression["leftHandSide"]
        if lhs["nodeType"] == "TupleExpression" and len(lhs["components"]) != 1:
            return self._tuple_assignment(expression, scope)
        root = _root(lhs)
        target = self.nodes.get(root.get("referencedDeclaration")) if root else None
        if target is not None and target.get("stateVariable"):
            return self._storage_write(expression, scope, _normal(self.text(lhs)), target)
        local = scope.get(root.get("referencedDeclaration")) if root else None
        if isinstance(local, tuple) and local[0] == "pointer" and root is not lhs:
            if local[2] is None:
                self.fail(expression, f"a write through the storage parameter {root['name']}: it could be any storage")
            return self._storage_write(expression, scope, self._through(local, root, lhs), local[2])
        if isinstance(local, tuple):
            if local[0] != "copy" or root is lhs:
                self.fail(expression, f"a write to {root['name']}, which is not a field of a memory copy or storage")
            self._write(expression, scope, "copy")
            self.written_copies.add(root["referencedDeclaration"])
            return None
        var = scope.get(lhs.get("referencedDeclaration")) if lhs["nodeType"] == "Identifier" else None
        if not isinstance(var, Var):
            self.fail(expression, f"an assignment to {self.text(lhs)}")
        operator = expression["operator"]
        if operator not in ASSIGNMENTS:
            self.fail(expression, f"the assignment operator {operator}")
        rhs = expression["rightHandSide"]
        value = self._expr(rhs, scope)
        if ASSIGNMENTS[operator] is not None:
            value = self._op(expression, ASSIGNMENTS[operator], var, value)
        self._assignable(rhs, value.type, var.type)
        return Assign(var, value, self.src(expression))

    def _tuple_assignment(self, expression, scope: dict) -> Unpack:
        """An assignment of a tuple to declared locals, as `(x, y) = (y, x);`. A part of storage is refused: the model
        leaves storage writes out, and the order of the writes of one tuple is solc's."""
        if expression["operator"] != "=":
            self.fail(expression, f"a tuple assignment with {expression['operator']}")
        vars_ = []
        for component in expression["leftHandSide"]["components"]:
            if component is None:
                vars_.append(None)
                continue
            var = scope.get(component.get("referencedDeclaration")) if component["nodeType"] == "Identifier" else None
            if not isinstance(var, Var):
                self.fail(component, f"a tuple assignment to {self.text(component)}, which is not a local")
            if var in vars_:
                self.fail(component, f"a tuple assignment to {var.name} twice: solc does not fix which value it keeps")
            vars_.append(var)
        rhs = expression["rightHandSide"]
        self.statement_value = rhs["id"]
        value = self._expr(rhs, scope)
        self._parts(rhs, value, len(vars_))
        for var, part in zip(vars_, parts(value.type), strict=True):
            if var is not None:
                self._assignable(rhs, part, var.type)
        return Unpack(tuple(vars_), value, False, self.src(expression))

    def _assignable(self, node, source: Type, target: Type) -> None:
        if source.kind == "tuple" and target.kind == "tuple" and len(source.parts) == len(target.parts):
            for a, b in zip(source.parts, target.parts, strict=True):
                self._assignable(node, a, b)
            return
        if source == target or (source.kind == "literal" and target.kind == "uint"):
            return
        if source.kind == "uint" and target.kind == "uint" and source.bits <= target.bits:
            return
        given = "literal" if source.kind == "literal" else _written(source)
        self.fail(node, f"a {given} value where {_written(target)} is expected")
