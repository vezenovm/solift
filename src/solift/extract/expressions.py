"""Expressions: operators, conversions, and internal, library and Math calls."""

from __future__ import annotations

import dataclasses
import re

from solift import ops
from solift.extract.common import (
    _STORAGE,
    EXTERNAL,
    _normal,
    _storage_struct,
    _super,
    _this,
    _through_contract,
    memory_struct,
)
from solift.extract.errors import Missing
from solift.extract.reads import MARKERS, _assigned
from solift.ir import ADDRESS, BOOL, LITERAL, Call, Const, Expr, Function, Tuple, Type, Var, uint

MATH_PATH = "utils/math/Math.sol"


MATH_OPERATORS = {"min": ("min", 2), "max": ("max", 2), "mulDiv": ("mulDiv", 3)}


# The operator of Math.mulDiv with a Rounding: Floor is the floor, as without one. Ceil adds one where
# mulmod(x, y, d) > 0, which Halmos cannot prove equal to x * y % d at a symbolic d, so it has no lemma.
ROUNDINGS = {"Floor": "mulDiv"}


BINARY = {
    "+": "add",
    "-": "sub",
    "*": "mul",
    "/": "div",
    "%": "mod",
    "<": "lt",
    "<=": "le",
    ">": "gt",
    ">=": "ge",
    "==": "eq",
    "!=": "ne",
    "&&": "and",
    "||": "or",
}


class Expressions:
    def _expr(self, node, scope: dict) -> Expr:
        self._signed(node)
        read = self._read(node, scope)
        if read is not None:
            return read
        kind = node["nodeType"]
        src = self.src(node)
        if kind == "Literal":
            if node["kind"] == "bool":
                return Const(node["value"] == "true", BOOL, src=src)
            if node["kind"] != "number":
                self.fail(node, f"a {node['kind']} literal")
            if node["typeDescriptions"]["typeString"] == "address":
                return Const(self._number(node), ADDRESS, text=self.text(node), src=src)
            return Const(self._number(node), LITERAL, text=self.text(node), src=src)
        if kind == "Identifier":
            target = scope.get(node.get("referencedDeclaration"))
            if isinstance(target, Var):
                return target
            if isinstance(target, tuple) and target[0] == "opaque":
                self.fail(node, f"the parameter {node['name']} is a value here, and the model has no value of its type")
            if isinstance(target, tuple):
                self.fail(node, f"the struct {node['name']} is used other than by field")
            return self._named(node)
        if (
            kind == "MemberAccess"
            and node["expression"]["nodeType"] == "FunctionCall"
            and memory_struct(node["expression"])
        ):
            # A field of the struct that an internal call returns or a constructor builds, as `window().end`: every
            # field is computed, as each can revert, into temporaries before the statement (structs.py).
            fields = self._struct_fields(self._struct_definition(node["expression"]), node)
            values = self._hoisted(node["expression"], fields, scope)
            return values[[f for f, _ in fields].index(node["memberName"])]
        if kind in ("MemberAccess", "IndexAccess"):
            self._unmapped_storage(node)
        if kind == "MemberAccess":
            return self._named(node)
        if kind == "TupleExpression":
            components = node["components"]
            if node.get("isInlineArray"):
                self.fail(node, "an inline array")
            if len(components) == 1:
                return self._expr(components[0], scope)
            if any(c is None for c in components):
                self.fail(node, "a tuple with an empty part outside the left of an assignment")
            args = tuple(self._expr(c, scope) for c in components)
            return Tuple(args, Type("tuple", parts=tuple(a.type for a in args)), src)
        if kind == "BinaryOperation":
            operator = node["operator"]
            if operator == "**":
                self.fail(node, "exponentiation")
            if operator not in BINARY:
                self.fail(node, f"the operator {operator}")
            return self._make(node, BINARY[operator], node["leftExpression"], node["rightExpression"], scope=scope)
        if kind == "UnaryOperation":
            if node["operator"] == "!":
                return self._make(node, "not", node["subExpression"], scope=scope)
            self.fail(node, f"the operator {node['operator']}")
        if kind == "Conditional":
            parts = (node["condition"], node["trueExpression"], node["falseExpression"])
            return self._make(node, "ite", *parts, scope=scope)
        if kind == "FunctionCall":
            return self._call(node, scope)
        if kind == "Assignment":
            self.fail(node, "an assignment inside an expression")
        self.fail(node, f"a {kind} expression is not supported")

    def _make(self, node, op: str, *operands, scope: dict) -> Expr:
        return self._op(node, op, *(self._expr(operand, scope) for operand in operands))

    def _op(self, node, op: str, *args: Expr) -> Expr:
        """The operation, with the type that ops.make gives it, which must be the type that solc gives it."""
        try:
            made = dataclasses.replace(ops.make(op, *args), src=self.src(node))
        except TypeError as err:
            self.fail(node, str(err))
        solc = self._type(node)
        if solc.kind != "literal" and solc != made.type:
            self.fail(node, f"{op} has the type {made.type.solidity} here and {solc.solidity} in solc")
        return made

    def _signed(self, node) -> None:
        type_string = node.get("typeDescriptions", {}).get("typeString") or ""
        if re.fullmatch(r"int\d*", type_string) or type_string.startswith("int_const -"):
            self.fail(node, "a signed integer")

    def _conversion(self, node, scope: dict) -> Expr:
        """`address(x)` of a contract, an interface or an address is x, and of a number literal is that address.
        `uint<n>(x)` of an unsigned x or a number literal widens it, or narrows it to its low n bits, as Solidity does.
        Any other type conversion is an error."""
        match = re.fullmatch(r"uint(\d+)", node["typeDescriptions"]["typeString"])
        if match and len(node["arguments"]) == 1:
            value = self._expr(node["arguments"][0], scope)
            if value.type.is_number:
                to = uint(int(match.group(1)))
                op = "widen" if value.type.kind == "literal" or value.type.bits <= to.bits else "narrow"
                try:
                    made = ops.make(op, value, src=self.src(node), to=to)
                except TypeError as err:
                    self.fail(node, str(err))
                return made
        if self._type(node) == ADDRESS and len(node["arguments"]) == 1:
            arg = node["arguments"][0]
            if arg["nodeType"] == "Literal" and arg["kind"] == "number":
                return Const(self._number(arg), ADDRESS, text=self.text(node), src=self.src(node))
            if not arg.get("typeDescriptions", {}).get("typeString", "").startswith("int_const"):
                value = self._expr(arg, scope)
                if value.type == ADDRESS:
                    return value
        self.fail(node, f"a type conversion: {self.text(node)}")

    def _call(self, node, scope: dict) -> Expr:
        if node["kind"] == "typeConversion":
            return self._conversion(node, scope)
        if node["kind"] != "functionCall":
            self.fail(node, f"a {node['kind']}")
        callee = node["expression"]
        decl = self.nodes.get(callee.get("referencedDeclaration"))
        if decl is None:
            name = callee.get("name") or callee.get("memberName")
            self.fail(node, f"the builtin {name}" if callee.get("referencedDeclaration", 0) < 0 else "a call")
        if decl["nodeType"] != "FunctionDefinition":
            self.fail(node, f"a call to {self.text(callee)}")
        owner = self.nodes[decl["scope"]]
        # A library call on a value, as `x.mulDivDown(y, d)` under `using MathLib for uint256`, passes the value first.
        bound = (
            callee["nodeType"] == "MemberAccess"
            and owner.get("contractKind") == "library"
            and not callee["expression"]["typeDescriptions"]["typeString"].startswith("type(library ")
        )
        # Named arguments name the parameters of the declaration that solc resolved. An override can name its own
        # parameters otherwise, and takes the arguments in their order.
        arguments = self._arguments(node, decl, bound)
        if bound:
            arguments = [callee["expression"], *arguments]
        decl = self._runs(callee, decl, self._within())
        owner = self.nodes[decl["scope"]]
        if owner.get("contractKind") == "library" and owner["name"] == "Math":
            path = self._place(owner)[0]
            if path.endswith(MATH_PATH):
                op = MATH_OPERATORS.get(decl["name"])
                if decl["name"] == "mulDiv" and len(arguments) == 4:
                    op, arguments = (self._rounding(arguments[3]), 3), arguments[:3]
                if op is None or len(arguments) != op[1]:
                    self.fail(node, f"Math.{decl['name']} with {len(arguments)} arguments has no lemma")
                self.math_path = path
                self.used.add(path)
                return self._make(node, op[0], *arguments, scope=scope)
        internal = (
            (callee["nodeType"] == "Identifier" and decl["visibility"] != "external")
            or (
                callee["nodeType"] == "MemberAccess"
                and owner.get("contractKind") == "library"
                and decl["visibility"] in ("internal", "private")
            )
            or _through_contract(callee)
        )
        if not internal:
            text = self.text(node)
            missing = Missing(_normal(text), "an external call", self.src(node))
            self.fail(node, f"an external call that the read map does not name: {text}", missing)
        # A storage struct argument is no value of the model: the callee reads the fields of its argument's storage.
        bindings = {}
        for param, arg in zip(decl["parameters"]["parameters"], arguments, strict=True):
            if _storage_struct(param):
                path = self._storage_path(arg, scope)
                if path is None:
                    self.fail(arg, f"the storage argument {self.text(arg)}: {_STORAGE}")
                bindings[param["id"]] = (*path, self._identity(arg, scope))
        pairs = list(zip(decl["parameters"]["parameters"], arguments, strict=True))
        written, value = set(self.written), self.statement_value
        function = self._callee(decl, node, bindings)
        # The arguments run before the call, so they see the writes made before it only. A memory struct argument is
        # its fields, as the callee takes them (structs.py).
        self.written = set(written)
        args, arguments = [], []
        for param, arg in pairs:
            if param["id"] in bindings:
                continue
            values = self._struct_values(arg, scope) if memory_struct(param) else [self._expr(arg, scope)]
            args += values
            arguments += [arg] * len(values)
        args = tuple(args)
        reads, writes = self.callee_reads[decl["id"]], self.callee_writes[decl["id"]]
        # An external call can change the contract's mutable storage only, not an immutable.
        stale = sorted(
            self.nodes[r]["name"]
            for r in reads - MARKERS
            if r in written or (EXTERNAL in written and self.nodes[r].get("mutability") == "mutable")
        )
        if "balance" in reads and EXTERNAL in written:
            self.fail(node, f"calls {decl['name']}, which reads a balance, after an external call that could change it")
        if stale or (reads & {"call", "any"} and written):
            what = ", ".join(stale) if stale else "storage through a call" if "call" in reads else "a storage parameter"
            self.fail(node, f"calls {decl['name']}, which reads {what}, after a write: the model misses the write")
        if writes and node["id"] != value:
            self.fail(node, f"{decl['name']} writes storage, so its call must be the whole value of a statement")
        self.written |= writes
        for found in self.reading:
            found |= reads
        for found in self.writing:
            found |= writes
        for arg, param, arg_node in zip(args, function.params, arguments, strict=True):
            self._assignable(arg_node, arg.type, param.type)
        return Call(function.name, args, function.returns, self.src(node))

    def _runs(self, callee, decl, within: int, contract: dict | None = None) -> dict:
        """The function that a call of `decl` through the expression `callee` runs, in a function of the contract
        `within`, in an instance of `contract` (the target's contract by default). A call of a contract's function or
        modifier by its name alone, or through `this`, is virtual: it runs the first implemented one of `decl`'s family
        (_family) in the linearization of `contract`, from the most derived contract. `super.f` runs the first one after
        `within`, which can be a sibling's function that `decl` does not override. solc resolves each in the calling
        contract instead, which can be a base of the target's contract. A call through a contract's name (`Base.f()`,
        `Base.gate`), a library function, a free function and a function of a contract outside the linearization run
        `decl`. Where a public state variable overrides an external function, its getter runs: the variable."""
        order = (contract or self.contract)["linearizedBaseContracts"]
        owner = self.owners.get(decl["id"])
        if owner not in order or self.nodes[owner]["contractKind"] == "library":
            return decl
        path = callee["nodeType"] == "IdentifierPath" and "." not in callee["name"]
        if callee["nodeType"] == "Identifier" or path or _this(callee):
            start = 0
        elif _super(callee) and within in order:
            start = order.index(within) + 1
        else:
            return decl
        return self._first(order[start:], decl) or decl

    def _first(self, order, decl) -> dict | None:
        """The first implemented member of `decl`'s family in the contracts `order`, or the first member if none is
        implemented, as in an abstract contract."""
        family = self._family(decl)
        members = [n for base in order for n in self.nodes[base]["nodes"] if n.get("id") in family]
        implemented = [
            n for n in members if n["nodeType"] == "VariableDeclaration" or n.get("implemented") or n.get("body")
        ]
        return (implemented or members or [None])[0]

    def _family(self, decl) -> set[int]:
        """The functions, or modifiers, that overrides connect to `decl`, in either direction: `decl`, those that it
        overrides, those that override them, and so on. They have its name and parameter types, and in one contract's
        linearization they are the functions that a call of `decl` by name or through super can run. A private function
        is alone in its family."""
        if self.overriders is None:
            self.overriders = {}
            for n in self.nodes.values():
                for base in (*(n.get("baseFunctions") or ()), *(n.get("baseModifiers") or ())):
                    self.overriders.setdefault(base, set()).add(n["id"])
        found, todo = {decl["id"]}, [decl["id"]]
        while todo:
            node = self.nodes.get(todo.pop(), {})
            linked = (*(node.get("baseFunctions") or ()), *(node.get("baseModifiers") or ()))
            for other in (*linked, *self.overriders.get(node.get("id"), ())):
                if other not in found:
                    found.add(other)
                    todo.append(other)
        return found

    def _within(self) -> int:
        """The contract of the function being extracted."""
        return self.nodes[self.stack[-1]]["scope"] if self.stack else self.top["scope"]

    def _rounding(self, node) -> str:
        """The operator of Math.mulDiv with a rounding, which must be Math.Rounding.Floor, as written."""
        decl = self.nodes.get(node.get("referencedDeclaration"))
        if decl is None or decl["nodeType"] != "EnumValue" or decl["name"] not in ROUNDINGS:
            self.fail(
                node, f"Math.mulDiv with the rounding {self.text(node)} has no lemma: it takes Math.Rounding.Floor"
            )
        return ROUNDINGS[decl["name"]]

    def _arguments(self, node, decl, bound: bool = False) -> list:
        """The argument nodes of a call in the order of the callee's parameters, also for named arguments such as
        `f({b: x, a: y})`. A bound call's value is not among them."""
        if not node.get("names"):
            return node["arguments"]
        params = [p["name"] for p in decl["parameters"]["parameters"]][1 if bound else 0 :]
        by_name = dict(zip(node["names"], node["arguments"], strict=True))
        if sorted(by_name) != sorted(params):
            self.fail(node, f"the named arguments {', '.join(node['names'])} are not the parameters of {decl['name']}")
        return [by_name[name] for name in params]

    def _callee(self, decl, call, bindings: dict) -> Function:
        """The model function of a callee. A callee with storage struct parameters is one function for the storage
        that its first call binds them to, so a call that binds other storage is refused."""
        if decl["id"] in self.stack:
            self.fail(call, f"{decl['name']} calls itself")
        self.calls.append((self.stack[-1] if self.stack else None, decl["id"]))
        bound = {self.nodes[param]["name"]: (path[0], path[2]) for param, path in bindings.items()}
        if decl["id"] in self.bindings and self.bindings[decl["id"]] != bound:
            first = ", ".join(f"{k} = {v[0]}" for k, v in self.bindings[decl["id"]].items())
            self.fail(call, f"calls {decl['name']} on other storage than its first call ({first})")
        if decl["id"] not in self.callees:
            if any(f.name == decl["name"] for f in self.callees.values()):
                self.fail(call, f"two callees are named {decl['name']}")
            self.bindings[decl["id"]] = bound
            self.assigned |= _assigned(decl)
            self.stack.append(decl["id"])
            self.used.add(self._place(decl)[0])
            self.reading.append(set())
            self.writing.append(set())
            function = self._function(decl, top=False, bindings=bindings)
            self.callee_reads[decl["id"]] = self.reading.pop()
            self.callee_writes[decl["id"]] = self.writing.pop()
            self.stack.pop()
            self.callees[decl["id"]] = function
        return self.callees[decl["id"]]
