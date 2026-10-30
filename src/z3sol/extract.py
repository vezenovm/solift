"""The extractor: reads the solc AST of one function and builds its model.

A target names a function as `path:Contract.function`. The read map names each value that the function reads from
outside itself, and gives it an input name. A key is one of:

- a storage variable or immutable by its name (`$floor`, `TOKEN`), or a field of a storage struct (`$config.cap`),
  whether the function reads it directly or through a memory copy (`Config memory cfg = $config;` then `cfg.cap`);
- a field of a storage struct in a mapping, at a key that is a parameter of the modeled function, through a memory copy
  (`FlowPricing memory pricing = $flowPricing[_flow];` then `pricing.maxSubsidy`, as `$flowPricing[_flow].maxSubsidy`);
- a field of a memory struct parameter of the modeled function (`_meter.frameGas`);
- a field of `tx`, `block` or `msg` (`tx.gasprice`, `block.basefee`);
- any other expression, such as an external view call, by its source text without whitespace
  (`TOKEN.balanceOf(address(this))`). An internal call in the read map is a cut point: its result is an input.

A read map entry that the function does not read is an error, except a field of `tx`, `block` or `msg`: every
transaction has it, so it is an input even where the function ignores it, and a theorem can name it.

Internal calls are inlined: each callee becomes a function of the model. OpenZeppelin's `Math.min` and `Math.mulDiv`
become operators of the table in ops.py. A constant becomes its value, with its name and declared type, and an enum
member its index. A contract or interface value is its address, and `address(x)` of one is the same value. Locals can
be assigned, also in branches, and a named return starts at zero. Anything else stops the extraction with an error that
names the construct and its place in the source.

The model assumes that each read succeeds: an external view call that reverts has no input in the model.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from dataclasses import dataclass
from fractions import Fraction

from z3sol import ops
from z3sol.ir import (
    ADDRESS,
    BOOL,
    LITERAL,
    UINT256,
    Assign,
    Call,
    Const,
    Expr,
    Function,
    If,
    Let,
    Model,
    Read,
    Return,
    Src,
    Stmt,
    Type,
    Var,
    enum,
    uint,
    walk,
)

MATH_PATH = "utils/math/Math.sol"
MATH_OPERATORS = {"min": ("min", 2), "mulDiv": ("mulDiv", 3)}
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
SUBDENOMINATIONS = {
    "wei": 1,
    "gwei": 10**9,
    "ether": 10**18,
    "seconds": 1,
    "minutes": 60,
    "hours": 3600,
    "days": 86400,
    "weeks": 604800,
}
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
ENVIRONMENT = {
    **{
        name: UINT256
        for name in (
            "tx.gasprice",
            "block.basefee",
            "block.blobbasefee",
            "block.chainid",
            "block.gaslimit",
            "block.number",
            "block.prevrandao",
            "block.timestamp",
            "msg.value",
        )
    },
    **{name: ADDRESS for name in ("tx.origin", "msg.sender", "block.coinbase")},
}
ASSIGNMENTS = {"=": None, "+=": "add", "-=": "sub", "*=": "mul", "/=": "div", "%=": "mod"}


class ExtractionError(Exception):
    pass


@dataclass(frozen=True)
class Extraction:
    """The model, and each source file it comes from, with the sha256 of its content."""

    model: Model
    sources: dict[str, str]


def extract(build_infos: list[dict], target: str, reads: dict[str, str]) -> Extraction:
    path = target.rpartition(":")[0]
    infos = [info for info in build_infos if path in info["output"]["sources"]]
    if len(infos) != 1:
        raise ExtractionError(f"{len(infos)} compile jobs hold {path}, not one")
    return _Extractor(infos[0], reads).extract(target)


def _normal(text: str) -> str:
    return re.sub(r"\s+", "", text)


class _Extractor:
    def __init__(self, info: dict, reads: dict[str, str]):
        self.sources = info["output"]["sources"]
        self.contents = {path: s["content"].encode() for path, s in info["input"]["sources"].items()}
        self.paths = {s["id"]: path for path, s in self.sources.items()}
        self.nodes: dict[int, dict] = {}
        self.enums: dict[str, dict] = {}
        self.enum_members: dict[int, tuple[dict, int]] = {}
        for s in self.sources.values():
            self._index(s["ast"])
        self.read_map = {_normal(key): (key, name) for key, name in reads.items()}
        if len(self.read_map) != len(reads):
            raise ExtractionError("two read map keys differ only in whitespace")
        self.reads: dict[str, Read] = {}
        self.callees: dict[int, Function] = {}
        self.stack: list[int] = []
        self.used: set[str] = set()
        self.math_path: str | None = None
        # The symbol and source path that the emitted Solidity imports for each enum type it names.
        self.enum_imports: dict[str, str] = {}

    def _index(self, node) -> None:
        if isinstance(node, dict):
            if "id" in node and "nodeType" in node:
                self.nodes[node["id"]] = node
                if node["nodeType"] == "EnumDefinition":
                    for index, member in enumerate(node["members"]):
                        self.enum_members[member["id"]] = (node, index)
                    self.enums[node["canonicalName"]] = node
            for value in node.values():
                self._index(value)
        elif isinstance(node, list):
            for value in node:
                self._index(value)

    # Places and text.

    def _place(self, node) -> tuple[str, int, int]:
        start, length, file_id = (int(x) for x in node["src"].split(":"))
        return self.paths[file_id], start, length

    def src(self, node) -> Src:
        path, start, _ = self._place(node)
        before = self.contents[path][:start]
        return Src(path, before.count(b"\n") + 1, start - (before.rfind(b"\n") + 1) + 1)

    def text(self, node) -> str:
        path, start, length = self._place(node)
        return self.contents[path][start : start + length].decode()

    def fail(self, node, message: str):
        raise ExtractionError(f"{self.src(node)}: {message}")

    # The target.

    def extract(self, target: str) -> Extraction:
        path, _, qualified = target.rpartition(":")
        contract_name, _, function_name = qualified.partition(".")
        unit = self.sources[path]["ast"]
        contracts = [n for n in unit["nodes"] if n["nodeType"] == "ContractDefinition" and n["name"] == contract_name]
        if len(contracts) != 1:
            raise ExtractionError(f"{path} has no contract {contract_name}")
        candidates = []
        for base_id in contracts[0]["linearizedBaseContracts"]:
            base = self.nodes[base_id]
            candidates = [
                n for n in base["nodes"] if n["nodeType"] == "FunctionDefinition" and n["name"] == function_name
            ]
            if candidates:
                break
        if len(candidates) != 1:
            raise ExtractionError(f"{contract_name} has {len(candidates)} functions named {function_name}, not one")
        node = candidates[0]
        function = self._function(node, top=True)
        for key, (written, name) in self.read_map.items():
            if key in self.reads:
                continue
            if key not in ENVIRONMENT:
                raise ExtractionError(f"the read map names {written}, which {qualified} does not read")
            self.reads[key] = Read(Var(name, ENVIRONMENT[key], input=True), written)
        reads = tuple(self.reads[key] for key in self.read_map)
        if not function.params and not reads:
            raise ExtractionError(f"{qualified} has no inputs: its result is a constant")
        self._check_names(node, function, reads)
        _check_constants((function, *self.callees.values()))
        imports = []
        if self.math_path is not None:
            imports.append(f'import {{Math}} from "{self._import_of(unit, self.math_path)}";')
        for symbol, source in sorted(self.enum_imports.items()):
            imports.append(f'import {{{symbol}}} from "{self._import_of(unit, source)}";')
        model = Model(
            contract=contract_name,
            function=function,
            reads=reads,
            callees=tuple(self.callees.values()),
            imports=tuple(imports),
            pragma=self._pragma(unit),
            spdx=unit.get("license") or "UNLICENSED",
        )
        self.used.add(path)
        sources = {p: hashlib.sha256(self.contents[p]).hexdigest() for p in sorted(self.used)}
        return Extraction(model, sources)

    def _check_names(self, node, function: Function, reads: tuple[Read, ...]) -> None:
        inputs = [v.name for v in function.params] + [r.var.name for r in reads]
        for name in inputs:
            if not IDENTIFIER.fullmatch(name) or name == "i":
                self.fail(node, f"{name} is not a valid input name (and i names the inputs struct)")
        if len(set(inputs)) != len(inputs):
            self.fail(node, f"two inputs have the same name: {', '.join(inputs)}")
        locals_ = [stmt.var.name for stmt in _lets(function.body)]
        if function.result is not None:
            locals_.append(function.result.name)
        for name in locals_:
            if name in inputs:
                self.fail(node, f"the local {name} has the name of an input")
            if locals_.count(name) > 1:
                self.fail(node, f"two locals are named {name}")

    def _import_of(self, unit, path: str) -> str:
        """How the emitted Solidity imports `path`: as the target's file or another source imports it, if one does
        without a relative path, so that the import keeps the project's remapping. Otherwise by its source unit name,
        which forge resolves from the project root."""
        for s in [unit, *(s["ast"] for s in self.sources.values())]:
            for n in s["nodes"]:
                if n["nodeType"] == "ImportDirective" and n["absolutePath"] == path and not n["file"].startswith("."):
                    return n["file"]
        return path

    def _pragma(self, unit) -> str:
        for n in unit["nodes"]:
            if n["nodeType"] == "PragmaDirective" and n["literals"][0] == "solidity":
                return "".join(n["literals"][1:])
        return "^0.8.0"

    # Functions and statements.

    def _function(self, node, top: bool) -> Function:
        name = node["name"]
        if node["kind"] != "function" or not node["implemented"]:
            self.fail(node, f"{name} is not an implemented function")
        if node["modifiers"]:
            self.fail(node["modifiers"][0], f"{name} has a modifier")
        returns = node["returnParameters"]["parameters"]
        if len(returns) != 1:
            self.fail(node["returnParameters"], f"{name} returns {len(returns)} values, not one")
        return_type = self._type(returns[0])
        for p in (*node["parameters"]["parameters"], *returns):
            self._not_inputs_name(p)
        scope = {}
        result = None
        if returns[0]["name"]:
            result = Var(returns[0]["name"], return_type, src=self.src(returns[0]))
            scope[returns[0]["id"]] = result
            scope["return"] = result
        params = []
        for p in node["parameters"]["parameters"]:
            if p["typeDescriptions"]["typeString"].startswith("struct ") and p.get("storageLocation") == "memory":
                if not top:
                    self.fail(p, f"the struct parameter {p['name']} of a called function")
                scope[p["id"]] = ("param", p["name"])
                continue
            var = Var(p["name"], self._type(p), input=top, src=self.src(p))
            scope[p["id"]] = var
            params.append(var)
        body = self._block(node["body"]["statements"], scope, return_type)
        if node["stateMutability"] not in ("pure", "view"):
            self.fail(node, f"{name} is {node['stateMutability']}: it can change state")
        return Function(name, tuple(params), return_type, body, self.src(node), result)

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
                if node.get("expression") is None:
                    if "return" not in scope:
                        self.fail(node, "a return without a value")
                    out.append(Return(scope["return"], self.src(node)))
                    continue
                value = self._expr(node["expression"], scope)
                self._assignable(node["expression"], value.type, return_type)
                out.append(Return(value, self.src(node)))
            elif kind == "ExpressionStatement":
                out.append(self._expression_statement(node["expression"], scope))
            else:
                self.fail(node, _STATEMENT_ERRORS.get(kind, f"a {kind} statement is not supported"))
        return tuple(out)

    def _declaration(self, node, scope: dict) -> Let | None:
        declarations = node["declarations"]
        if len(declarations) != 1 or declarations[0] is None:
            self.fail(node, "a tuple declaration")
        decl = declarations[0]
        init = node.get("initialValue")
        type_string = decl["typeDescriptions"]["typeString"]
        if type_string.startswith("struct ") and decl.get("storageLocation") == "memory":
            prefix = self._struct_copy(init, scope) if init is not None else None
            if prefix is None:
                self.fail(init or node, "a memory struct that is not a copy of a storage struct")
            scope[decl["id"]] = ("copy", prefix)
            return None
        self._not_inputs_name(decl)
        var = Var(decl["name"], self._type(decl), src=self.src(decl))
        if any(isinstance(v, Var) and v.name == var.name for v in scope.values()):
            self.fail(decl, f"the local {var.name} shadows a parameter, local or return of the same name")
        if init is None:
            if var.type.kind not in ("uint", "bool"):
                self.fail(node, f"a local of type {var.type.solidity} declared without a value")
            value = Const(False, BOOL, text="false") if var.type == BOOL else Const(0, var.type, text="0")
        else:
            value = self._expr(init, scope)
            self._assignable(init, value.type, var.type)
        scope[decl["id"]] = var
        return Let(var, value, self.src(node))

    def _not_inputs_name(self, decl) -> None:
        if decl["name"] == "i":
            self.fail(decl, "i names the inputs struct of the emitted Solidity")

    def _struct_copy(self, init, scope: dict) -> str | None:
        """The read map prefix of a memory copy of a storage struct: `$config`, or `$pricing[_flow]` for an entry of a
        mapping at a parameter of the modeled function."""
        state = self._state_variable(init)
        if state is not None:
            return state["name"]
        if init["nodeType"] == "IndexAccess":
            state = self._state_variable(init["baseExpression"])
            index = init.get("indexExpression")
            key = scope.get(index.get("referencedDeclaration")) if index else None
            if state is not None and isinstance(key, Var) and key.input:
                return f"{state['name']}[{key.name}]"
        return None

    def _expression_statement(self, expression, scope: dict) -> Assign:
        kind = expression["nodeType"]
        if kind == "Assignment":
            return self._assignment(expression, scope)
        if kind == "FunctionCall" and expression["expression"].get("name") in ("require", "assert", "revert"):
            self.fail(expression, f"{expression['expression']['name']} is not supported")
        if kind == "UnaryOperation" and expression["operator"] in ("++", "--", "delete"):
            self.fail(expression, f"{expression['operator']} is not supported")
        self.fail(expression, "an expression statement")

    def _assignment(self, expression, scope: dict) -> Assign:
        lhs = expression["leftHandSide"]
        root = _root(lhs)
        target = self.nodes.get(root.get("referencedDeclaration")) if root else None
        if target is not None and target.get("stateVariable"):
            self.fail(expression, "a storage write")
        if root and isinstance(scope.get(root.get("referencedDeclaration")), tuple):
            self.fail(expression, "a write to a memory struct")
        var = scope.get(lhs.get("referencedDeclaration")) if lhs["nodeType"] == "Identifier" else None
        if not isinstance(var, Var):
            self.fail(expression, f"an assignment to {self.text(lhs)}")
        if var.input:
            self.fail(expression, f"an assignment to the input {var.name}")
        operator = expression["operator"]
        if operator not in ASSIGNMENTS:
            self.fail(expression, f"the assignment operator {operator}")
        rhs = expression["rightHandSide"]
        value = self._expr(rhs, scope)
        if ASSIGNMENTS[operator] is not None:
            value = self._op(expression, ASSIGNMENTS[operator], var, value)
        self._assignable(rhs, value.type, var.type)
        return Assign(var, value, self.src(expression))

    def _assignable(self, node, source: Type, target: Type) -> None:
        if source == target or (source.kind == "literal" and target.kind == "uint"):
            return
        if source.kind == "uint" and target.kind == "uint" and source.bits <= target.bits:
            return
        given = "literal" if source.kind == "literal" else source.solidity
        self.fail(node, f"a {given} value where {target.solidity} is expected")

    # Expressions.

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
            if isinstance(target, tuple):
                self.fail(node, f"the struct copy {node['name']} is used other than by field")
            return self._named(node)
        if kind == "MemberAccess":
            return self._named(node)
        if kind == "TupleExpression":
            if node.get("isInlineArray") or len(node["components"]) != 1:
                self.fail(node, "a tuple")
            return self._expr(node["components"][0], scope)
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

    def _read(self, node, scope: dict) -> Var | None:
        """The input for a read, or None if the expression is not a read. A read that the read map lacks is an error."""
        kind = node["nodeType"]
        key = None
        what = None
        if kind == "MemberAccess" and node["expression"]["nodeType"] == "Identifier":
            base = node["expression"]
            target = scope.get(base.get("referencedDeclaration"))
            state = self.nodes.get(base.get("referencedDeclaration"))
            if isinstance(target, tuple):
                what = "a storage field" if target[0] == "copy" else "a field of the struct parameter"
                key = f"{target[1]}.{node['memberName']}"
            elif state is not None and state.get("stateVariable") and state.get("mutability") != "constant":
                key, what = f"{state['name']}.{node['memberName']}", "a storage field"
            elif base["name"] in ("tx", "block", "msg") and base.get("referencedDeclaration", 0) < 0:
                key, what = self.text(node), "an environment field"
        elif kind == "Identifier" and node.get("referencedDeclaration") not in scope:
            state = self.nodes.get(node.get("referencedDeclaration"))
            if state is not None and state.get("stateVariable") and state.get("mutability") != "constant":
                if state["typeDescriptions"]["typeString"].startswith("struct "):
                    self.fail(node, f"the storage struct {state['name']} is read other than by field")
                key, what = state["name"], f"the {state['mutability']} variable"
        if key is None:
            key = self.text(node)
            if _normal(key) not in self.read_map:
                return None
        entry = self.read_map.get(_normal(key))
        if entry is None:
            self.fail(node, f"reads {what} {key}, which the read map does not name")
        if _normal(key) not in self.reads:
            var = Var(entry[1], self._type(node), input=True, src=self.src(node))
            self.reads[_normal(key)] = Read(var, entry[0])
        return self.reads[_normal(key)].var

    def _named(self, node) -> Const:
        """A constant, named by an identifier or a member access such as `OxideConstants.MAX_PRIORITY_FEE_WEI`, or an
        enum member such as `SIPABase.Intent.Deposit`, which the emitted Solidity names by its full name."""
        decl = self.nodes.get(node.get("referencedDeclaration"))
        if decl is not None and decl["nodeType"] == "EnumValue":
            definition, index = self.enum_members[decl["id"]]
            type_ = self._enum(definition)
            return Const(index, type_, text=f"{type_.name}.{decl['name']}", src=self.src(node))
        if decl is None or decl["nodeType"] != "VariableDeclaration" or decl.get("mutability") != "constant":
            self.fail(node, f"{self.text(node)} is not a constant, a local, a parameter or a read")
        return self._constant(decl)

    def _constant(self, decl) -> Const:
        self.used.add(self._place(decl)[0])
        value = decl.get("value")
        if value is None:
            self.fail(decl, f"the constant {decl['name']} has no value")
        if value["nodeType"] == "Literal" and value["kind"] == "bool":
            number, text = value["value"] == "true", value["value"]
        elif value["nodeType"] == "Literal" and value["kind"] == "number":
            number, text = self._number(value), self.text(value)
        elif value["nodeType"] in ("Identifier", "MemberAccess"):
            other = self._named(value)
            number, text = other.value, other.text
        elif (
            value["nodeType"] == "FunctionCall"
            and value["kind"] == "typeConversion"
            and self._type(decl) == ADDRESS
            and len(value["arguments"]) == 1
            and value["arguments"][0]["nodeType"] == "Literal"
            and value["arguments"][0]["kind"] == "number"
        ):
            # A checksummed address literal stands alone; any other number needs its conversion, as `address(0)`.
            literal = value["arguments"][0]
            number, text = self._number(literal), self.text(literal)
            if not re.fullmatch(r"0x[0-9a-fA-F]{40}", text):
                text = f"address({text})"
        else:
            self.fail(value, f"the constant {decl['name']} is not a literal, an address or another constant")
        return Const(number, self._type(decl), text=text, name=decl["name"], src=self.src(decl))

    def _number(self, node) -> int:
        written = node["value"].replace("_", "").lower()
        if written.startswith("0x"):
            number = Fraction(int(written, 16))
        elif "e" in written:
            mantissa, exponent = written.split("e")
            number = Fraction(mantissa) * Fraction(10) ** int(exponent)
        else:
            number = Fraction(written)
        number *= SUBDENOMINATIONS[node["subdenomination"]] if node.get("subdenomination") else 1
        if number.denominator != 1 or number < 0:
            self.fail(node, f"{self.text(node)} is not an unsigned integer")
        return int(number)

    def _conversion(self, node, scope: dict) -> Expr:
        """`address(x)` of a contract, an interface or an address is x, and of a number literal is that address. Any
        other type conversion is an error."""
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
        arguments = self._arguments(node, decl)
        if owner.get("contractKind") == "library" and owner["name"] == "Math":
            path = self._place(owner)[0]
            if path.endswith(MATH_PATH):
                op = MATH_OPERATORS.get(decl["name"])
                if op is None or len(arguments) != op[1]:
                    self.fail(node, f"Math.{decl['name']} with {len(arguments)} arguments has no lemma")
                self.math_path = path
                self.used.add(path)
                return self._make(node, op[0], *arguments, scope=scope)
        internal = (callee["nodeType"] == "Identifier" and decl["visibility"] != "external") or (
            callee["nodeType"] == "MemberAccess"
            and owner.get("contractKind") == "library"
            and decl["visibility"] in ("internal", "private")
        )
        if not internal:
            self.fail(node, f"an external call that the read map does not name: {self.text(node)}")
        function = self._callee(decl, node)
        args = tuple(self._expr(arg, scope) for arg in arguments)
        for arg, param, arg_node in zip(args, function.params, arguments, strict=True):
            self._assignable(arg_node, arg.type, param.type)
        return Call(function.name, args, function.returns, self.src(node))

    def _arguments(self, node, decl) -> list:
        """The argument nodes of a call in the order of the callee's parameters, also for named arguments such as
        `f({b: x, a: y})`."""
        if not node.get("names"):
            return node["arguments"]
        params = [p["name"] for p in decl["parameters"]["parameters"]]
        by_name = dict(zip(node["names"], node["arguments"], strict=True))
        if sorted(by_name) != sorted(params):
            self.fail(node, f"the named arguments {', '.join(node['names'])} are not the parameters of {decl['name']}")
        return [by_name[name] for name in params]

    def _callee(self, decl, call) -> Function:
        if decl["id"] in self.stack:
            self.fail(call, f"{decl['name']} calls itself")
        if decl["id"] not in self.callees:
            if any(f.name == decl["name"] for f in self.callees.values()):
                self.fail(call, f"two callees are named {decl['name']}")
            self.stack.append(decl["id"])
            self.used.add(self._place(decl)[0])
            function = self._function(decl, top=False)
            self.stack.pop()
            self.callees[decl["id"]] = function
        return self.callees[decl["id"]]

    def _state_variable(self, node):
        if node["nodeType"] != "Identifier":
            return None
        decl = self.nodes.get(node.get("referencedDeclaration"))
        return decl if decl is not None and decl.get("stateVariable") else None

    def _type(self, node) -> Type:
        type_string = node["typeDescriptions"]["typeString"]
        if type_string == "bool":
            return BOOL
        if type_string.startswith("int_const "):
            return LITERAL
        match = re.fullmatch(r"uint(\d+)", type_string)
        if match:
            return uint(int(match.group(1)))
        if re.fullmatch(r"int\d*", type_string):
            self.fail(node, "a signed integer")
        if type_string in ("address", "address payable") or re.fullmatch(r"contract \w+", type_string):
            return ADDRESS
        match = re.fullmatch(r"enum ([\w.]+)", type_string)
        if match and match.group(1) in self.enums:
            return self._enum(self.enums[match.group(1)])
        self.fail(node, f"the type {type_string} is not supported")

    def _enum(self, definition) -> Type:
        """The enum type, and the import of the contract or file-level enum that declares it, for the emitted
        Solidity."""
        name = definition["canonicalName"]
        symbol = name.partition(".")[0]
        path = self._place(definition)[0]
        if self.enum_imports.setdefault(symbol, path) != path:
            raise ExtractionError(f"two enums are declared under the name {symbol}")
        self.used.add(path)
        return enum(name, len(definition["members"]))


_STATEMENT_ERRORS = {
    "ForStatement": "a loop",
    "WhileStatement": "a loop",
    "DoWhileStatement": "a loop",
    "UncheckedBlock": "an unchecked block",
    "InlineAssembly": "inline assembly",
    "Block": "a nested block",
    "EmitStatement": "an event",
    "RevertStatement": "a revert",
    "TryStatement": "a try statement",
}


def _statements(node) -> list:
    return node["statements"] if node["nodeType"] == "Block" else [node]


def _root(node):
    while node["nodeType"] in ("MemberAccess", "IndexAccess"):
        node = node["expression" if node["nodeType"] == "MemberAccess" else "baseExpression"]
    return node if node["nodeType"] == "Identifier" else None


def _check_constants(functions) -> None:
    """The emitted Solidity names a constant by its name alone, so a constant must not have the name of the inputs
    struct, or of a parameter, a local or a return, which would shadow it there."""
    names = {"i"}
    for function in functions:
        names.update(p.name for p in function.params)
        names.update(stmt.var.name for stmt in _lets(function.body))
        if function.result is not None:
            names.add(function.result.name)
    for function in functions:
        for e in walk(function):
            if isinstance(e, Const) and e.name in names:
                if e.name == "i":
                    raise ExtractionError(f"{e.src}: the constant i names the inputs struct of the emitted Solidity")
                raise ExtractionError(
                    f"{e.src}: the constant {e.name} has the name of a parameter, local or return of the emitted "
                    "Solidity, which would shadow it there"
                )


def _lets(stmts):
    for stmt in stmts:
        if isinstance(stmt, Let):
            yield stmt
        elif isinstance(stmt, If):
            yield from _lets(stmt.then)
            yield from _lets(stmt.orelse)
