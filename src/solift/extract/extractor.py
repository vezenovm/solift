"""The extractor: the state of one extraction, and the target function's model with its reads and outputs."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from solift.extract.common import IDENTIFIER, _normal, _subtree
from solift.extract.effects import Effects
from solift.extract.errors import ExtractionError, Missing, Wanted
from solift.extract.expressions import Expressions
from solift.extract.reads import Reads, _assigned
from solift.extract.statements import Statements
from solift.extract.values import Values
from solift.ir import (
    ADDRESS,
    UINT256,
    Call,
    Const,
    Effect,
    Function,
    Model,
    Output,
    Read,
    Src,
    Var,
    declared_locals,
    reserved,
    walk,
)

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


@dataclass(frozen=True)
class Extraction:
    """The model, and each source file it comes from, with the sha256 of its content."""

    model: Model
    sources: dict[str, str]


def extract(build_infos: list[dict], target: str, reads: dict[str, str], outputs: dict | None = None) -> Extraction:
    return _Extractor(_job(build_infos, target), reads, outputs or {}).extract(target)


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


class _Extractor(Statements, Effects, Reads, Expressions, Values):
    def __init__(self, info: dict, reads: dict[str, str], outputs: dict | None = None):
        self.sources = info["output"]["sources"]
        self.contents = {path: s["content"].encode() for path, s in info["input"]["sources"].items()}
        self.paths = {s["id"]: path for path, s in self.sources.items()}
        self.nodes: dict[int, dict] = {}
        self.enum_members: dict[int, tuple[dict, int]] = {}
        # The contract of each function and modifier, by its id: solc gives a modifier no scope.
        self.owners: dict[int, int] = {}
        for s in self.sources.values():
            self._index(s["ast"])
        self.read_map = {_normal(key): (key, name) for key, name in reads.items()}
        if len(self.read_map) != len(reads):
            raise ExtractionError("two read map keys differ only in whitespace")
        self.reads: dict[str, Read] = {}
        self.callees: dict[int, Function] = {}
        # The contract that the target names, whose linearization each internal call runs in (_runs), the target
        # function, and the callees being extracted, by declaration id.
        self.contract: dict | None = None
        self.top: dict | None = None
        # Each function or modifier by the id of each one that it overrides (_family), once a call needs them.
        self.overriders: dict[int, set[int]] | None = None
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
        # The parameters of the modeled function that the body assigns, each also a local of its name (_function).
        self.parameter_locals: set[str] = set()
        self.key_identities: dict[str, tuple] = {}
        self.keyed: set[int] = set()
        self.calls: list[tuple[int | None, int]] = []
        self.reading: list[set] = []
        self.writing: list[set] = []
        self.statement_value: int | None = None
        # The output map by normalized key; the output of each place and of each argument of each call, as it is met;
        # the places of the outputs that each storage variable holds; and each other write, with its storage variable.
        self.output_map = {_normal(key): (key, value) for key, value in (outputs or {}).items()}
        if len(self.output_map) != len(outputs or {}):
            raise ExtractionError("two output map keys differ only in whitespace")
        self.outputs: dict[tuple[str, int | None], Output] = {}
        self.output_states: dict[int, list[str]] = {}
        self.other_writes: list[tuple[int, str, dict]] = []

    def _index(self, node) -> None:
        if isinstance(node, dict):
            if "id" in node and "nodeType" in node:
                self.nodes[node["id"]] = node
                if node["nodeType"] == "EnumDefinition":
                    for index, member in enumerate(node["members"]):
                        self.enum_members[member["id"]] = (node, index)
                if node["nodeType"] == "ContractDefinition":
                    for member in node["nodes"]:
                        self.owners[member["id"]] = node["id"]
            for value in node.values():
                self._index(value)
        elif isinstance(node, list):
            for value in node:
                self._index(value)

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

    def fail(self, node, message: str, missing: Missing | None = None, output: Wanted | None = None):
        raise ExtractionError(f"{self.src(node)}: {message}", missing, output=output)

    def extract(self, target: str) -> Extraction:
        node = self.top = self.target(target)
        path, _, qualified = target.rpartition(":")
        contract_name = qualified.partition(".")[0]
        unit = self.sources[path]["ast"]
        self.assigned = _assigned(node)
        function = self._with_outputs(self._function(node, top=True), qualified)
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
        _check_shadowing((function, *self.callees.values()))
        imports = tuple(
            f'import {{{symbol}}} from "{self._import_of(unit, source)}";'
            for symbol, source in sorted(self.enum_imports.items())
        )
        model = Model(
            contract=contract_name,
            function=function,
            reads=reads,
            callees=tuple(self.callees.values()),
            math="" if self.math_path is None else self._import_of(unit, self.math_path),
            imports=imports,
            pragma=self._pragma(unit),
            spdx=unit.get("license") or "UNLICENSED",
            effects=tuple(self.effects),
            outputs=tuple(self.outputs.values()),
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
        self.contract = contracts[0]
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
            if name in inputs and name not in self.parameter_locals:
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


def _parameter_type(param) -> str:
    """A parameter's type as a target names it: `uint256`, `address`, or the last part of the name of a struct, an enum,
    a contract or a user-defined value type, such as `MarketParams`."""
    text = re.sub(r" (memory|storage|calldata)( pointer| ref)?$", "", param["typeDescriptions"]["typeString"])
    return re.sub(r"^(struct|enum|contract) ", "", text).rpartition(".")[2]


def _check_shadowing(functions) -> None:
    """The emitted Solidity names a constant and a called function by its name alone. A constant must not have the
    name of the inputs struct, or of a parameter, a local or a return, which would shadow it there. A function of the
    model must not have the name of a parameter, a local or a return of a function that calls it, as a parameter of the
    modeled function that its body assigns, which is a local there."""
    names = {"i"}
    for function in functions:
        names.update(p.name for p in function.params)
        names.update(v.name for v in declared_locals(function.body))
        names.update(v.name for v in function.named)
    for function in functions:
        # The modeled function reads its inputs as `i.<name>`: only its locals and returns are Solidity names there.
        own = {p.name for p in function.params if not p.input}
        own.update(v.name for v in declared_locals(function.body))
        own.update(v.name for v in function.named)
        for e in walk(function):
            if isinstance(e, Call) and e.function in own:
                raise ExtractionError(
                    f"{e.src}: the call of {e.function} names a function of the emitted Solidity that a parameter, "
                    f"local or return of {function.name} of the same name would shadow there"
                )
    for function in functions:
        for e in walk(function):
            if isinstance(e, Const) and e.name in names:
                if e.name == "i":
                    raise ExtractionError(f"{e.src}: the constant i names the inputs struct of the emitted Solidity")
                raise ExtractionError(
                    f"{e.src}: the constant {e.name} has the name of a parameter, local or return of the emitted "
                    "Solidity, which would shadow it there"
                )
