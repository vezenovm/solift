"""The extractor: reads the solc AST of one function and builds its model.

A target names a function as `path:Contract.function`, or as `path:Contract.function(MarketParams,Id,address)` with the
types of its parameters, for one of several functions of one name. The read map names each value that the function
reads from outside itself, and gives it an input name. A key is one of:

- a storage variable or immutable by its name (`$floor`, `TOKEN`), or a field of a storage struct (`$config.cap`),
  whether the function reads it directly or through a memory copy (`Config memory cfg = $config;` then `cfg.cap`);
- a field of a storage struct in a mapping, at a key that is a parameter of the modeled function or a field of `tx`,
  `block` or `msg`, through a memory copy or a storage pointer (`Rate storage rate = $rates[_kind];` then `rate.cap`,
  as `$rates[_kind].cap`), also a nested field (`rate.config.cap`, as `$rates[_kind].config.cap`) and a pointer passed
  to a callee;
- a field of a storage struct parameter of the modeled function (`_spender.left`);
- a field of a memory struct parameter of the modeled function (`_meter.used`);
- a field of `tx`, `block` or `msg` (`tx.gasprice`, `block.basefee`);
- any other expression, such as an external view call, by its source text without whitespace
  (`TOKEN.balanceOf(address(this))`). An internal call in the read map is a cut point: its result is an input.

A read map entry that the function does not read is an error, except a field of `tx`, `block` or `msg`: every
transaction has it, so it is an input even where the function ignores it, and a theorem can name it. A key names one
value for the whole call, so a local in it, also below a pointer, must be one that only its declaration assigns, the
same one at each read of the key, in the modeled function or in a callee that runs once.

Internal calls are inlined, also a library call on a value (`x.mulDivDown(y, d)`): each callee becomes a function of
the model. OpenZeppelin's `Math.min` and `Math.mulDiv` become operators of the table in ops.py. A constant becomes its
value, with its name and declared type, and an enum member its index. A contract or interface value is its address, and
`address(x)` of one is the same value. `uint256(x)` of a narrower unsigned x is x. Locals can be assigned, also in
branches, and a named return starts at zero. A function can return several values, and a tuple declaration or
assignment (`(, uint256 y) = limits(a, b);`, `(x, y) = (y, x);`) takes them apart into locals. `require`, `assert` and
`revert` revert. A parameter of a user-defined value type, such as Morpho's `Id`, that the function uses only inside
read map keys is not an input. Anything else stops the extraction with an error that names the construct and its place
in the source.

The model is the function's value and whether it reverts, which is what the link compares. It leaves out the
function's storage writes and events (`Model.effects`), so the extractor refuses one that could change either:

- a written value, a written place or an event argument must not revert: it is a local, a parameter, a constant, a
  field of `tx`, `block` or `msg`, storage, a memory copy, a mapping entry, a field of one of these, or a conversion of
  one of these to an unsigned integer or an address. A compound assignment such as `+=` to storage is refused.
- after a write, the function must not read what the write changed: the storage variable it wrote or the memory copy it
  wrote a field of, also through a callee, and it must not make a call in the read map that is not to an internal pure
  function, which could read the changed storage.
- a call to a function that writes storage must be the whole value of its statement, as solc does not fix the order of
  the parts of an expression.
- a call in the read map must be to a view or pure function.

The model assumes that each read succeeds: an external view call that reverts has no input in the model.
"""

from __future__ import annotations

import dataclasses
import hashlib
import re
from dataclasses import dataclass
from fractions import Fraction

from solift import ops
from solift.ir import (
    ADDRESS,
    BOOL,
    LITERAL,
    UINT256,
    Assign,
    Call,
    Const,
    Effect,
    Expr,
    Function,
    If,
    Let,
    Model,
    Read,
    Return,
    Revert,
    Src,
    Stmt,
    Tuple,
    Type,
    Unpack,
    Var,
    declared_locals,
    enum,
    parts,
    reserved,
    tuple_of,
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


@dataclass(frozen=True)
class Missing:
    """A read that the read map does not name: its key as the read map would name it, what it is, and its place."""

    key: str
    what: str
    src: Src


class ExtractionError(Exception):
    """An error with the place in the source. `missing` is the read, when the error is a read that the read map does not
    name: discover.py adds it to the read map and extracts again."""

    def __init__(self, message: str, missing: Missing | None = None):
        super().__init__(message)
        self.missing = missing


@dataclass(frozen=True)
class Extraction:
    """The model, and each source file it comes from, with the sha256 of its content."""

    model: Model
    sources: dict[str, str]


def extract(build_infos: list[dict], target: str, reads: dict[str, str]) -> Extraction:
    return _Extractor(_job(build_infos, target), reads).extract(target)


def declared(build_infos: list[dict], target: str) -> set[str]:
    """The names that the target function declares: its parameters, its returns and its locals."""
    node = _Extractor(_job(build_infos, target), {}).target(target)
    return {n["name"] for n in _subtree(node) if n.get("nodeType") == "VariableDeclaration" and n.get("name")}


def _job(build_infos: list[dict], target: str) -> dict:
    """The build info of the one compile job that holds the target's file."""
    path = target.rpartition(":")[0]
    if not path:
        raise ExtractionError(f"{target} names no function as path:Contract.function")
    infos = [info for info in build_infos if path in info["output"]["sources"]]
    if not infos:
        raise ExtractionError(
            f"the build has no source {path}: check the path, and that the profile compiles the file or a file that "
            "imports it"
        )
    if len(infos) != 1:
        raise ExtractionError(f"{len(infos)} compile jobs hold {path}, not one")
    return infos[0]


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
        # The storage variables, by declaration id, and the memory copies, by the id of their local, that a write has
        # changed so far; the writes and events that the model leaves out; for each callee the storage variables it
        # reads ("call" for a call in the read map that could read any) and those it writes; the open sets of the
        # callees being extracted; and the expression that is the whole value of the statement being extracted.
        self.written: set[int] = set()
        self.written_copies: set[int] = set()
        self.effects: list[Effect] = []
        self.callee_reads: dict[int, set] = {}
        self.callee_writes: dict[int, set] = {}
        # The storage that each storage struct parameter of a callee stands for, by the callee's declaration id: its
        # place, and what it names (_identity).
        self.bindings: dict[int, dict[str, tuple[str, tuple]]] = {}
        # The locals and parameters that an assignment changes after their declaration; what each read map key names;
        # the callees that name their own locals in a read map key; and each call of a callee, by its caller (None for
        # the modeled function), which counts how many times a callee runs.
        self.assigned: set[int] = set()
        self.key_identities: dict[str, tuple] = {}
        self.keyed: set[int] = set()
        self.calls: list[tuple[int | None, int]] = []
        self.reading: list[set] = []
        self.writing: list[set] = []
        self.statement_value: int | None = None

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

    def fail(self, node, message: str, missing: Missing | None = None):
        raise ExtractionError(f"{self.src(node)}: {message}", missing)

    # The target.

    def extract(self, target: str) -> Extraction:
        node = self.target(target)
        path, _, qualified = target.rpartition(":")
        contract_name = qualified.partition(".")[0]
        unit = self.sources[path]["ast"]
        self.assigned = _assigned(node)
        function = self._function(node, top=True)
        self._run_once()
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
            effects=tuple(self.effects),
        )
        self.used.update((path, self._place(node)[0]))
        sources = {p: hashlib.sha256(self.contents[p]).hexdigest() for p in sorted(self.used)}
        return Extraction(model, sources)

    def target(self, target: str) -> dict:
        """The FunctionDefinition node that the target names."""
        path, _, qualified = target.rpartition(":")
        contract_name, _, function_part = qualified.partition(".")
        signature = re.fullmatch(r"(\w+)(?:\(([^()]*)\))?", function_part)
        if signature is None:
            raise ExtractionError(f"{target} names no function as Contract.function or Contract.function(types)")
        function_name, types = signature.groups()
        wanted = None if types is None else [t.strip() for t in types.split(",") if t.strip()]
        unit = self.sources[path]["ast"]
        contracts = [n for n in unit["nodes"] if n["nodeType"] == "ContractDefinition" and n["name"] == contract_name]
        if len(contracts) != 1:
            raise ExtractionError(f"{path} has no contract {contract_name}")
        candidates = []
        for base_id in contracts[0]["linearizedBaseContracts"]:
            base = self.nodes[base_id]
            candidates = [
                n
                for n in base["nodes"]
                if n["nodeType"] == "FunctionDefinition"
                and n["name"] == function_name
                and (wanted is None or [_parameter_type(p) for p in n["parameters"]["parameters"]] == wanted)
            ]
            if candidates:
                break
        if len(candidates) != 1:
            raise ExtractionError(f"{contract_name} has {len(candidates)} functions named {function_part}, not one")
        return candidates[0]

    def _check_names(self, node, function: Function, reads: tuple[Read, ...]) -> None:
        inputs = [v.name for v in function.params] + [r.var.name for r in reads]
        for name in inputs:
            if not IDENTIFIER.fullmatch(name) or name == "i":
                self.fail(node, f"{name} is not a valid input name (and i names the inputs struct)")
        for read in reads:
            if reserved(read.var.name):
                self.fail(node, f"the input name {read.var.name} of {read.source} is a word that Solidity reserves")
        if len(set(inputs)) != len(inputs):
            self.fail(node, f"two inputs have the same name: {', '.join(inputs)}")
        locals_ = [v.name for v in declared_locals(function.body)] + [v.name for v in function.named]
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

    def _function(self, node, top: bool, bindings: dict | None = None) -> Function:
        """The function. `bindings` gives each storage struct parameter of a callee the storage of its argument."""
        name = node["name"]
        if node["kind"] != "function" or not node["implemented"]:
            self.fail(node, f"{name} is not an implemented function")
        if node["modifiers"]:
            self.fail(node["modifiers"][0], f"{name} has a modifier")
        returns = node["returnParameters"]["parameters"]
        if not returns:
            self.fail(node["returnParameters"], f"{name} returns no value")
        return_type = tuple_of(tuple(self._type(r) for r in returns))
        for p in (*node["parameters"]["parameters"], *returns):
            self._not_inputs_name(p)
        scope = {}
        results = ()
        if any(r["name"] for r in returns):
            results = tuple(Var(r["name"], self._type(r), src=self.src(r)) if r["name"] else None for r in returns)
            scope.update({r["id"]: v for r, v in zip(returns, results, strict=True) if v is not None})
            scope["return"] = results
        params = []
        for p in node["parameters"]["parameters"]:
            if _storage_struct(p):
                scope[p["id"]] = ("pointer", p["name"], None, (p["id"],)) if top else ("pointer", *bindings[p["id"]])
                continue
            if p["typeDescriptions"]["typeString"].startswith("struct ") and p.get("storageLocation") == "memory":
                if not top:
                    self.fail(p, f"the struct parameter {p['name']} of a called function")
                scope[p["id"]] = ("param", p["name"])
                continue
            if top and self._user_value_type(p):
                scope[p["id"]] = ("opaque", p["name"])
                continue
            var = Var(p["name"], self._type(p), input=top, src=self.src(p))
            scope[p["id"]] = var
            params.append(var)
        body = self._block(node["body"]["statements"], scope, return_type)
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
                if stmt is not None:
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

    def _storage_path(self, node, scope: dict) -> tuple[str, dict | None] | None:
        """The read map prefix of the storage that `node` names, and the storage variable that holds it, or None for a
        storage parameter of the modeled function: a storage variable (`$config`), an entry of a mapping at a key that
        names one entry for the whole call (`$spenders[_who]`, `$spenders[msg.sender]`), a storage pointer, or a field
        of one of these (`spender.config`)."""
        kind = node["nodeType"]
        if kind == "Identifier":
            entry = scope.get(node.get("referencedDeclaration"))
            if isinstance(entry, tuple) and entry[0] == "pointer":
                return entry[1], entry[2]
            state = self._state_variable(node)
            return (state["name"], state) if state is not None and state.get("mutability") == "mutable" else None
        if kind == "IndexAccess" and _mapping(node) and node.get("indexExpression") is not None:
            base, key = self._storage_path(node["baseExpression"], scope), self._key(node["indexExpression"], scope)
            return (f"{base[0]}[{key}]", base[1]) if base is not None and key is not None else None
        if kind == "MemberAccess":
            base = self._storage_path(node["expression"], scope)
            return (f"{base[0]}.{node['memberName']}", base[1]) if base is not None else None
        return None

    def _key(self, node, scope: dict) -> str | None:
        """A mapping key that names one entry for the whole call: an input of the modeled function, also of a
        user-defined value type, or a field of `tx`, `block` or `msg`."""
        if node["nodeType"] == "Identifier":
            entry = scope.get(node.get("referencedDeclaration"))
            if isinstance(entry, Var) and entry.input:
                return entry.name
            return entry[1] if isinstance(entry, tuple) and entry[0] == "opaque" else None
        if node["nodeType"] == "MemberAccess" and node["expression"]["nodeType"] == "Identifier":
            base = node["expression"]
            if base["name"] in ("tx", "block", "msg") and base.get("referencedDeclaration", 0) < 0:
                return _normal(self.text(node))
        return None

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
            self._write(expression, scope, "write")
            self._wrote(target)
            return None
        local = scope.get(root.get("referencedDeclaration")) if root else None
        if isinstance(local, tuple) and local[0] == "pointer" and root is not lhs:
            if local[2] is None:
                self.fail(expression, f"a write through the storage parameter {root['name']}: it could be any storage")
            self._write(expression, scope, "write", self._through(local, root, lhs))
            self._wrote(local[2])
            return None
        if isinstance(local, tuple):
            if local[0] != "copy" or root is lhs:
                self.fail(expression, f"a write to {root['name']}, which is not a field of a memory copy or storage")
            self._write(expression, scope, "copy")
            self.written_copies.add(root["referencedDeclaration"])
            return None
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
            if var.input:
                self.fail(component, f"an assignment to the input {var.name}")
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

    def _write(self, expression, scope: dict, kind: str, place: str | None = None) -> None:
        if expression["operator"] != "=":
            self.fail(expression, f"a write with {expression['operator']}: its value can revert")
        self._cannot_revert(expression["leftHandSide"], scope)
        self._cannot_revert(expression["rightHandSide"], scope)
        self._effect(expression, kind, place or _normal(self.text(expression["leftHandSide"])))

    def _wrote(self, state) -> None:
        """Records a write to the storage variable `state`, here and in each callee being extracted."""
        self.written.add(state["id"])
        for writes in self.writing:
            writes.add(state["id"])

    def _through(self, entry: tuple, root, node) -> str:
        """The read map key of `node`, a place that a copy, a struct parameter or a pointer `root` leads to:
        `$spenders[_who].config.cap` for `spender.config.cap`."""
        return entry[1] + _normal(self.text(node))[len(root["name"]) :]

    def _effect(self, node, kind: str, place: str) -> None:
        self.effects.append(Effect(kind, place, " ".join(self.text(node).split()), self.src(node)))

    def _event(self, call) -> str:
        """The signature of the event that an emit calls, such as `Updated(uint256)`."""
        decl = self.nodes.get(call["expression"].get("referencedDeclaration"))
        if decl is None or decl["nodeType"] != "EventDefinition":
            return _normal(self.text(call["expression"]))
        types = [p["typeDescriptions"]["typeString"] for p in decl["parameters"]["parameters"]]
        return f"{decl['name']}({','.join(types)})"

    def _cannot_revert(self, node, scope: dict) -> None:
        """Refuses a part of a write or an event that could revert: the model leaves it out."""
        kind = node["nodeType"]
        type_string = node.get("typeDescriptions", {}).get("typeString") or ""
        if type_string.startswith("function"):
            pass
        elif kind in ("Identifier", "Literal"):
            return
        elif kind == "MemberAccess":
            return self._cannot_revert(node["expression"], scope)
        elif kind == "IndexAccess":
            base = node["baseExpression"]
            if base["typeDescriptions"]["typeString"].startswith("mapping(") and node.get("indexExpression"):
                self._cannot_revert(base, scope)
                return self._cannot_revert(node["indexExpression"], scope)
        elif kind == "TupleExpression" and len(node["components"]) == 1 and not node.get("isInlineArray"):
            return self._cannot_revert(node["components"][0], scope)
        elif kind == "FunctionCall" and node["kind"] == "typeConversion" and len(node["arguments"]) == 1:
            if re.fullmatch(r"uint\d+|address", type_string):
                return self._cannot_revert(node["arguments"][0], scope)
        self.fail(node, f"{self.text(node)} in a write or an event: the model leaves it out, so it must not revert")

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
                self.fail(node, f"the struct {node['name']} is used other than by field")
            return self._named(node)
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

    def _unmapped_storage(self, node) -> None:
        """Refuses a read of storage that the read map does not name and that has no other form, such as a mapping
        entry `$balances[_who]` or `position[id][borrower].collateral`, with the read as missing."""
        root = _root(node)
        decl = self.nodes.get(root.get("referencedDeclaration")) if root else None
        if decl is not None and decl.get("stateVariable") and decl.get("mutability") != "constant":
            key = _normal(self.text(node))
            missing = Missing(key, "a storage read", self.src(node))
            self.fail(node, f"reads the storage {key}, which the read map does not name", missing)

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
        copy = state = pointer = None
        root = _root(node) if kind in ("MemberAccess", "IndexAccess") else None
        through = scope.get(root.get("referencedDeclaration")) if root is not None else None
        below = isinstance(through, tuple) and through[0] in ("copy", "param", "pointer")
        if below:
            if node["typeDescriptions"]["typeString"].startswith(("struct ", "mapping(")):
                self.fail(node, f"{self.text(node)} is read other than by a field or an entry")
            key = self._through(through, root, node)
            what = "a field of the struct parameter" if through[0] == "param" else "a storage field"
            if through[0] == "pointer" and through[2] is None:
                what = "a field of the storage parameter"
            copy = root["referencedDeclaration"] if through[0] == "copy" else None
            pointer = through if through[0] == "pointer" else None
        elif kind == "MemberAccess" and node["expression"]["nodeType"] == "Identifier":
            base = node["expression"]
            declared = self.nodes.get(base.get("referencedDeclaration"))
            if declared is not None and declared.get("stateVariable") and declared.get("mutability") != "constant":
                key, what, state = f"{declared['name']}.{node['memberName']}", "a storage field", declared
            elif base["name"] in ("tx", "block", "msg") and base.get("referencedDeclaration", 0) < 0:
                key, what = self.text(node), "an environment field"
        elif kind == "Identifier" and node.get("referencedDeclaration") not in scope:
            declared = self.nodes.get(node.get("referencedDeclaration"))
            if declared is not None and declared.get("stateVariable") and declared.get("mutability") != "constant":
                if declared["typeDescriptions"]["typeString"].startswith("struct "):
                    self.fail(node, f"the storage struct {declared['name']} is read other than by field")
                key, what, state = declared["name"], f"the {declared['mutability']} variable", declared
        fallback = key is None
        if fallback:
            if kind == "Identifier" and node.get("referencedDeclaration") in scope:
                # A local or a parameter, though the read map may name a storage variable of the same name.
                return None
            key = self.text(node)
            if _normal(key) not in self.read_map:
                return None
        entry = self.read_map.get(_normal(key))
        if entry is None:
            self.fail(node, f"reads {what} {key}, which the read map does not name", Missing(key, what, self.src(node)))
        if node.get("typeDescriptions", {}).get("typeString", "").startswith("tuple("):
            self.fail(node, f"the read {key} gives several values, and a read map entry names one")
        self._stable(key, node, scope)
        if copy in self.written_copies:
            self.fail(node, f"reads {key} after a write to its memory copy: the model does not see the write")
        if state is not None:
            self._state_read(state, node)
        if pointer is not None:
            self._storage_read(pointer[2], node)
        if fallback or below:
            # The indexes below a copy, a parameter or a pointer are any expressions, as in other read map keys.
            self._fallback_read(node, scope)
        if _normal(key) not in self.reads:
            var = Var(entry[1], self._type(node), input=True, src=self.src(node))
            self.reads[_normal(key)] = Read(var, entry[0])
        return self.reads[_normal(key)].var

    def _stable(self, key: str, node, scope: dict) -> None:
        """A read map key names one value for the whole call. So it changes nothing, a local that it names, or a
        parameter of a callee, is one that only its declaration assigns, in a callee that runs once (_run_once), and
        each read of the key names the same things (_identity)."""
        for n in _subtree(node):
            kind = n.get("nodeType")
            if kind == "Assignment" or (kind == "UnaryOperation" and n["operator"] in ("++", "--", "delete")):
                self.fail(n, f"the read {key} changes {self.text(n)}: {_LOCALS}")
            var = scope.get(n.get("referencedDeclaration")) if kind == "Identifier" else None
            if isinstance(var, Var) and not var.input:
                if n["referencedDeclaration"] in self.assigned:
                    self.fail(n, f"the read {key} names {n['name']}, which an assignment changes: {_LOCALS}")
                if self.stack:
                    self.keyed.add(self.stack[-1])
        identity = self._identity(node, scope)
        if self.key_identities.setdefault(_normal(key), identity) != identity:
            self.fail(node, f"the read {key} names other things than at its first read: {_LOCALS}")

    def _identity(self, node, scope: dict) -> tuple:
        """What an expression names, beyond its text: the declaration of each identifier and of each member, a copy or
        a pointer as the identity of its place, so that a read through it is the read of that place, and each literal,
        whose text loses its spaces in a read map key."""
        names = []
        for n in _subtree(node):
            if n.get("nodeType") == "Literal":
                names.append(("literal", n.get("hexValue")))
            elif n.get("nodeType") == "MemberAccess":
                names.append(("member", n["memberName"], n.get("referencedDeclaration")))
            elif n.get("nodeType") == "Identifier":
                entry = scope.get(n.get("referencedDeclaration"))
                if isinstance(entry, tuple) and entry[0] in ("copy", "pointer"):
                    names.extend(entry[3])
                else:
                    names.append(n.get("referencedDeclaration"))
        return tuple(names)

    def _run_once(self) -> None:
        """Refuses a callee that names its own locals or parameters in a read map key and runs more than once in the
        call, as one input would stand for the value at each run."""

        def runs(callee: int) -> int:
            return sum(1 if caller is None else runs(caller) for caller, c in self.calls if c == callee)

        for callee in sorted(self.keyed):
            if runs(callee) > 1:
                name = self.nodes[callee]["name"]
                self.fail(self.nodes[callee], f"{name} runs {runs(callee)} times in the call: {_LOCALS}")

    def _state_read(self, decl, node) -> None:
        """Records a read of the storage variable `decl`, which must not follow a write to it."""
        if decl["id"] in self.written:
            self.fail(node, f"reads {decl['name']} after a write to it: the model does not see the write")
        for reads in self.reading:
            reads.add(decl["id"])

    def _storage_read(self, state, node) -> None:
        """Records a read through a memory copy or a storage pointer of the storage variable `state`, or of a storage
        parameter of the modeled function (None), which could be any storage, so it must not follow any write."""
        if state is not None:
            return self._state_read(state, node)
        if self.written:
            self.fail(node, "reads a storage parameter after a write, which could have changed it")
        for reads in self.reading:
            reads.add("any")

    def _fallback_read(self, node, scope: dict) -> None:
        """Records the reads of a read map key that is any other expression: each storage variable in it. Each call in
        it must be to a view or pure function, and after a write to an internal pure one, which reads no storage. No
        call may take a memory value of the function, which an internal call could change, nor call `gasleft`."""
        if self._calls_gasleft(node, set()):
            self.fail(node, f"the read {self.text(node)} calls gasleft, whose value changes during the call")
        for n in _subtree(node):
            ref = n.get("referencedDeclaration")
            if n.get("nodeType") == "Identifier":
                entry = scope.get(ref)
                if isinstance(entry, tuple) and entry[0] == "pointer":
                    self._storage_read(entry[2], n)
                declared = self.nodes.get(ref)
                if declared is not None and declared.get("stateVariable") and declared.get("mutability") != "constant":
                    self._state_read(declared, n)
                if ref in self.written_copies:
                    self.fail(n, f"reads {n['name']} after a write to it: the model does not see the write")
            elif n.get("nodeType") == "FunctionCall" and n["kind"] == "functionCall":
                callee = n["expression"]
                decl = self.nodes.get(callee.get("referencedDeclaration"))
                if decl is None and callee.get("referencedDeclaration", 0) < 0:
                    continue
                if decl is None or decl["nodeType"] != "FunctionDefinition":
                    self.fail(n, f"the read {self.text(node)} makes a call that the extractor cannot resolve")
                if decl["stateMutability"] not in ("view", "pure"):
                    self.fail(n, f"the read {self.text(node)} calls {decl['name']}, which can change state")
                internal = decl["visibility"] in ("internal", "private") or callee["nodeType"] == "Identifier"
                given = [*n["arguments"], *([callee["expression"]] if callee["nodeType"] == "MemberAccess" else [])]
                if internal and any(" memory" in a["typeDescriptions"].get("typeString", "") for a in given):
                    self.fail(n, f"the read {self.text(node)} passes memory to {decl['name']}, which can change it")
                if decl["stateMutability"] != "pure" or decl["visibility"] not in ("internal", "private"):
                    if self.written:
                        self.fail(n, f"the read {self.text(node)} follows a write, and its call could read the change")
                    for reads in self.reading:
                        reads.add("call")

    def _calls_gasleft(self, node, seen: set) -> bool:
        """Whether an expression or a function calls `gasleft`, also through the functions that it calls."""
        for n in _subtree(node):
            ref = n.get("referencedDeclaration")
            if n.get("nodeType") == "Identifier" and n["name"] == "gasleft" and (ref or 0) < 0:
                return True
            decl = self.nodes.get(ref) if n.get("nodeType") in ("Identifier", "MemberAccess") else None
            if decl is not None and decl["nodeType"] == "FunctionDefinition" and decl["id"] not in seen:
                seen.add(decl["id"])
                if self._calls_gasleft(decl, seen):
                    return True
        return False

    def _named(self, node) -> Const:
        """A constant, named by an identifier or a member access such as `Fees.TIP`, or an enum member such as
        `Kinds.Kind.Small`, which the emitted Solidity names by its full name."""
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
        """`address(x)` of a contract, an interface or an address is x, and of a number literal is that address.
        `uint256(x)` of an unsigned x or a number literal widens it. Any other type conversion is an error."""
        if node["typeDescriptions"]["typeString"] == "uint256" and len(node["arguments"]) == 1:
            value = self._expr(node["arguments"][0], scope)
            if value.type.is_number:
                return self._op(node, "widen", value)
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
        arguments = self._arguments(node, decl, bound)
        if bound:
            arguments = [callee["expression"], *arguments]
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
        pairs = zip(decl["parameters"]["parameters"], arguments, strict=True)
        arguments = [arg for param, arg in pairs if param["id"] not in bindings]
        written, value = set(self.written), self.statement_value
        function = self._callee(decl, node, bindings)
        # The arguments run before the call, so they see the writes made before it only.
        self.written = set(written)
        args = tuple(self._expr(arg, scope) for arg in arguments)
        reads, writes = self.callee_reads[decl["id"]], self.callee_writes[decl["id"]]
        stale = sorted(self.nodes[r]["name"] for r in reads - {"call", "any"} if r in written)
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
    "TryStatement": "a try statement",
}


def _statements(node) -> list:
    return node["statements"] if node["nodeType"] == "Block" else [node]


def _subtree(node):
    """Each AST node in `node`, `node` first."""
    if isinstance(node, dict):
        if "nodeType" in node:
            yield node
        for value in node.values():
            yield from _subtree(value)
    elif isinstance(node, list):
        for value in node:
            yield from _subtree(value)


_STORAGE = (
    "a storage pointer must be to a storage struct, at mapping keys that name one entry for the whole call (inputs, or "
    "fields of tx, block or msg), or to a field of one"
)


def _mapping(node) -> bool:
    """Whether an index access is to an entry of a mapping, which no key makes revert, unlike an array index."""
    return node["baseExpression"]["typeDescriptions"]["typeString"].startswith("mapping(")


def _storage_struct(decl) -> bool:
    """Whether a declaration is a storage pointer to a struct, as `Spender storage spender`."""
    return decl["typeDescriptions"]["typeString"].startswith("struct ") and decl.get("storageLocation") == "storage"


def _written(type_: Type) -> str:
    return f"({type_.solidity})" if type_.kind == "tuple" else type_.solidity


def _parameter_type(param) -> str:
    """A parameter's type as a target names it: `uint256`, `address`, or the last part of the name of a struct, an enum,
    a contract or a user-defined value type, such as `MarketParams`."""
    text = re.sub(r" (memory|storage|calldata)( pointer| ref)?$", "", param["typeDescriptions"]["typeString"])
    return re.sub(r"^(struct|enum|contract) ", "", text).rpartition(".")[2]


_LOCALS = (
    "a read map key names one value for the whole call, so it must change nothing, and each local in it must be one "
    "that only its declaration assigns, in a function that runs once"
)


def _assigned(node) -> set[int]:
    """The declarations that an assignment in `node` changes: an identifier on its left, also in a tuple."""
    targets = [n["leftHandSide"] for n in _subtree(node) if n.get("nodeType") == "Assignment"]
    assigned = set()
    while targets:
        target = targets.pop()
        if target is not None and target["nodeType"] == "TupleExpression":
            targets.extend(target["components"])
        elif target is not None and target["nodeType"] == "Identifier":
            assigned.add(target["referencedDeclaration"])
    return assigned


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
        names.update(v.name for v in declared_locals(function.body))
        names.update(v.name for v in function.named)
    for function in functions:
        for e in walk(function):
            if isinstance(e, Const) and e.name in names:
                if e.name == "i":
                    raise ExtractionError(f"{e.src}: the constant i names the inputs struct of the emitted Solidity")
                raise ExtractionError(
                    f"{e.src}: the constant {e.name} has the name of a parameter, local or return of the emitted "
                    "Solidity, which would shadow it there"
                )
