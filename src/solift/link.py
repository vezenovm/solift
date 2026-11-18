"""The link: a generated Halmos test that the contract and the emitted Solidity agree on every input.

The test inherits a hand-written setup contract, the harness, which deploys the contract. Each input of the model is a
parameter of the check, so Halmos makes it symbolic over its type, except an input in `observed`: a value that the test
reads from the environment, such as `tx.gasprice`, which Halmos already makes symbolic and no cheatcode sets. The model
never gets an input from the environment otherwise. An adapter is the harness code that makes the contract read its
inputs, such as `vm.fee(baseFee);` for `block.basefee`. The parameters of the modeled function go to the contract
through `call`.

"Agree" means: both succeed with the same result, each of its values for a function of several returns, and the same
outputs, or both revert. An output is a write or an external call argument that the model has as a value: the test
reads it after the call through its `post` expression, such as a getter of the harness or what a stub recorded. Revert
data, the other storage writes and calls, and events are not compared. The test reads the observed inputs before it
calls the contract, so that each is the value that the contract reads, also when the call writes storage: the model
reads nothing that the function wrote (the extract package).

The generated files are the model library and host (solidity.py), the link test, and a test of each operator-table
row and lemma that the model uses, which this build proves on its own bytecode and OpenZeppelin version.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
import sys
from dataclasses import dataclass, field

from solift.ir import Model, Op, parts, walk
from solift.ops import PROOFS, TABLE, constant_proof
from solift.solidity import encoded_call, encoded_calls, imports, names, oldest, version
from solift.solidity import source as model_source

RESERVED = {"i", "host", "codeOk", "codeOut", "modelOk", "modelOut", "codeResults", "modelResults"}


class LinkError(Exception):
    pass


WORD = r"\b[A-Za-z_]\w*\b"


@dataclass(frozen=True)
class Harness:
    """The setup of the links: a Solidity contract that deploys the contract under test, and how the link test sets each
    input through it. Specs share a harness, and each spec that uses it takes all of it (`combine`).

    `setup` is `path:Contract`. A relative path is a file next to the module that makes the Harness (a factory function
    makes it next to the factory's module), and the run adds it to the overlay at `at`, by default
    `<generated>/harness/<file name>`. Its imports resolve from that place, so it imports the project's files by their
    path from the root. `Harness.from_project` names a file of the project instead, from its root, which the overlay
    must copy.

    `adapters` maps an input name, or a tuple of names, to the Solidity statements that set it, such as a struct
    argument built from the inputs for its fields. `observed` maps an input name to the expression that the test reads
    it from, before the call. `parameters` maps more symbolic parameters of the check, which are not inputs, to their
    types: an adapter can use them to set what the contract reads, and an observed expression reads the input that
    results, as for a cut point such as a price that the contract computes from a feed. `imports` are more import
    directives that the adapters need. `instance` names the harness variable that holds the deployed contract, so that a
    spec of a public or external target can leave out its call."""

    setup: str
    adapters: dict = field(default_factory=dict)
    observed: dict = field(default_factory=dict)
    parameters: dict = field(default_factory=dict)
    imports: tuple[str, ...] = ()
    at: str | None = None
    instance: str | None = None
    in_project: bool = False

    def __post_init__(self):
        path, colon, contract = self.setup.rpartition(":")
        if not colon or not path or not re.fullmatch(r"[A-Za-z_]\w*", contract):
            raise LinkError(f"the harness {self.setup!r} is not path:Contract")
        if self.in_project:
            pure = pathlib.PurePosixPath(path)
            if pure.is_absolute() or ".." in pure.parts or str(pure) != path:
                raise LinkError(f"the harness {path} of the project is not a normalized path from the project's root")
            if self.at is not None:
                raise LinkError(f"the harness {path} of the project stays at its path, so it takes no at")
        elif not pathlib.Path(path).is_absolute():
            object.__setattr__(self, "setup", f"{(_caller_directory() / path).resolve()}:{contract}")
        for key, code in self.adapters.items():
            for name in key if isinstance(key, tuple) else (key,):
                if not re.search(rf"\b{re.escape(name)}\b", code):
                    raise LinkError(f"the harness adapter for {name} does not use it: {code}")

    @classmethod
    def from_project(cls, setup: str, **options) -> Harness:
        """A harness that is a file of the project, as `path:Contract` with the path from the project's root."""
        return cls(setup, in_project=True, **options)

    @property
    def path(self) -> str:
        return self.setup.rpartition(":")[0]

    @property
    def contract(self) -> str:
        return self.setup.rpartition(":")[2]

    def destination(self, generated: str) -> str:
        return self.at or f"{generated}/harness/{pathlib.Path(self.path).name}"


def _caller_directory() -> pathlib.Path:
    """The directory of the module that makes a Harness: the first frame outside this module and dataclasses, or the
    current directory for code without a file, as in a REPL."""
    frame = sys._getframe(1)
    while frame is not None and frame.f_globals.get("__name__") in (__name__, "dataclasses"):
        frame = frame.f_back
    file = frame.f_globals.get("__file__") if frame is not None else None
    return pathlib.Path(file).resolve().parent if file else pathlib.Path.cwd()


@dataclass(frozen=True)
class Link:
    """The link of one model, which `combine` makes from its spec and harness: `setup` is the harness as `path:Contract`
    from the overlay's root, `call` calls the contract under test from the harness, such as `quoter.exposedQuote(_gas,
    _price)`, and `post` maps each output of the model to the expression that the test reads it from after the call,
    such as `pool.leftOf(address(this))` for a write or `psm.lastAmount()` for an argument that a stub records. The
    other fields are those of `Harness`."""

    setup: str
    call: str
    adapters: dict = field(default_factory=dict)
    observed: dict = field(default_factory=dict)
    test_import: str = "forge-std/Test.sol"
    imports: tuple[str, ...] = ()
    parameters: dict = field(default_factory=dict)
    post: dict = field(default_factory=dict)


def combine(
    model: Model,
    harness: Harness,
    setup: str,
    call: str | None = None,
    post: dict | None = None,
    adapters: dict | None = None,
    observed: dict | None = None,
    parameters: dict | None = None,
    imports: tuple[str, ...] = (),
    test_import: str = "forge-std/Test.sol",
) -> Link:
    """The link of a model from its harness and the parts that its spec gives itself. The whole harness applies to
    every spec that uses it, and `generate` checks its parts as strictly as the spec's own: each name of an adapter's
    key is an input of the model or a parameter of the check, and each observed value is of an input. So a harness
    holds what its specs share, and a spec takes what it alone reads itself. An input that both set is refused."""
    post, adapters, observed, parameters = post or {}, adapters or {}, observed or {}, parameters or {}
    inputs = {v.name for v in model.inputs}
    for name in harness.parameters:
        if name in parameters:
            raise LinkError(f"the harness and the spec both declare the parameter {name}")
    checked = {**harness.parameters, **parameters}
    for key in harness.adapters:
        for name in key if isinstance(key, tuple) else (key,):
            if name not in inputs and name not in checked:
                raise LinkError(
                    f"the harness adapter for {_label(key)} sets {name}, which {model.name} does not read: a harness's "
                    f"adapters apply to every spec that uses it, so give this one to the specs that read {name}"
                )
    for name in harness.observed:
        if name not in inputs:
            raise LinkError(
                f"the harness observes {name}, which {model.name} does not read: a harness's observed values apply to "
                f"every spec that uses it, so give this one to the specs that read {name}"
            )
    for key in adapters:
        if key in harness.adapters:
            raise LinkError(f"the harness and the spec both have an adapter for {_label(key)}")
    for name in observed:
        if name in harness.observed:
            raise LinkError(f"the harness and the spec both observe {name}")
    return Link(
        setup=setup,
        call=call if call is not None else _derived_call(model, harness),
        adapters={**harness.adapters, **adapters},
        observed={**harness.observed, **observed},
        test_import=test_import,
        imports=tuple(dict.fromkeys((*harness.imports, *imports))),
        parameters=checked,
        post=post,
    )


def _label(key) -> str:
    return f"({', '.join(key)})" if isinstance(key, tuple) else key


def _derived_call(model: Model, harness: Harness) -> str:
    """The call of a public or external target on the harness's instance, with the target's parameters, when each of
    them is a parameter of the model: a struct, signed or user-defined value type parameter is not, and needs a call
    that builds it."""
    if harness.instance is None:
        raise LinkError(f"the spec of {model.name} has no call, and its harness names no instance to call it on")
    if model.visibility not in ("public", "external"):
        raise LinkError(
            f"{model.name} is {model.visibility or 'not external'}: give its spec a call, through the harness"
        )
    names = tuple(p.name for p in model.function.params)
    if any(not name for name in model.signature) or model.signature != names:
        missing = [name or "an unnamed parameter" for name in model.signature if not name or name not in names]
        raise LinkError(
            f"{model.name} takes {', '.join(missing)}, which no input is: give its spec a call that builds it"
        )
    return f"{harness.instance}.{model.function.name}({', '.join(names)})"


@dataclass(frozen=True)
class Check:
    """A Halmos check that the run declares, by test contract and function name, and by the path of its source file
    when the run knows it: a result from another file does not count for it."""

    contract: str
    function: str
    path: str = ""


def placed(checks, directory: str) -> tuple[Check, ...]:
    """The checks with the path of their file under `directory`, the generated directory relative to the root that
    Halmos runs in. The generator names each check's file, relative to that directory."""
    return tuple(dataclasses.replace(c, path=f"{directory}/{c.path}") for c in checks)


@dataclass(frozen=True)
class Generated:
    files: dict[str, str]
    checks: tuple[Check, ...]


def generate(model: Model, link: Link, operators: bool = True) -> Generated:
    """The model, its link test, and with `operators` the test of each row and lemma it uses. A run of several models
    writes the operators tests of all of them instead (`operators_test`)."""
    stem = names(model)[0].removesuffix("Model")
    match = re.fullmatch(r"\s*(\w+)\.(\w+)\((.*)\)\s*", link.call, re.S)
    if match is None:
        raise LinkError(f"call {link.call!r} is not target.function(arguments)")
    target, function, arguments = match.groups()
    covered = _covered(model, link, arguments)
    files = {
        f"{stem}.sol": model_source(model),
        f"{stem}Link.t.sol": _link_test(model, link, stem, target, function, arguments, covered),
    }
    generated = Generated(files, (Check(f"{stem}Link", "check_link", f"{stem}Link.t.sol"),))
    if not operators:
        return generated
    shared = operators_test((model,), stem, link.test_import)
    return Generated({**generated.files, **shared.files}, generated.checks + shared.checks)


def _covered(model: Model, link: Link, arguments: str) -> dict[str, str]:
    """How each input reaches the contract: "call", "adapter" or "observed". Refuses a link that leaves out an input,
    covers one twice, or names an input that the model lacks."""
    inputs = [v.name for v in model.inputs]
    covered: dict[str, str] = {}

    def cover(name: str, how: str) -> None:
        if name not in inputs:
            raise LinkError(f"the link names {name}, which is not an input of {model.name}")
        if name in covered:
            raise LinkError(f"the link sets {name} twice")
        covered[name] = how

    for name in re.findall(WORD, arguments):
        if name in inputs:
            cover(name, "call")
    for name in link.parameters:
        if name in inputs:
            raise LinkError(f"the link parameter {name} has the name of an input of {model.name}")
    for key, code in link.adapters.items():
        for name in key if isinstance(key, tuple) else (key,):
            if not re.search(rf"\b{re.escape(name)}\b", code):
                raise LinkError(f"the adapter for {name} does not use it: {code}")
            if name not in link.parameters:
                cover(name, "adapter")
    for name in link.observed:
        cover(name, "observed")
    for name in inputs:
        if name not in covered:
            raise LinkError(f"no call argument, adapter or observed value sets the input {name}")
    for name in [*inputs, *link.parameters]:
        if name in RESERVED:
            raise LinkError(f"{name} has a name that the generated test uses")
    for param in model.function.params:
        if covered[param.name] != "call":
            raise LinkError(f"the parameter {param.name} must reach the contract through the call")
    outputs = [o.var.name for o in model.outputs]
    for name in link.post:
        if name not in outputs:
            raise LinkError(f"the link reads {name} after the call, which is not an output of {model.name}")
    for name in outputs:
        if name not in link.post:
            raise LinkError(f"no post expression reads the output {name} after the call")
    return covered


def _link_test(model, link, stem, target, function, arguments, covered) -> str:
    library, host = names(model)
    setup_path, _, setup = link.setup.rpartition(":")
    params = [f"    {v.declared or v.type.solidity} {v.name}" for v in model.inputs if covered[v.name] != "observed"]
    params += [f"    {type_} {name}" for name, type_ in link.parameters.items()]
    fields = [f"      {v.name}: {link.observed.get(v.name, v.name)}" for v in model.inputs]
    adapters = [f"    {code.strip()}" for code in link.adapters.values()]
    lines = [
        f"// SPDX-License-Identifier: {model.spdx}",
        f"// Generated by solift: the link of {model.name} to its model. Do not edit.",
        f"pragma solidity {model.pragma};",
        "",
        f'import {{{setup}}} from "{setup_path}";',
        *imports(model),
        *link.imports,
        f'import {{{library}, {host}}} from "./{stem}.sol";',
        "",
        f"contract {stem}Link is {setup} {{",
        *_results(model),
        "  function check_link(",
        ",\n".join(params),
        "  ) public {",
        *adapters,
        # The inputs struct, the calls and their flags are in a block of their own, and several values decode into a
        # memory struct, so that the comparisons, which can read any parameter, fit the legacy code generator's stack.
        "    bytes memory codeOut;",
        "    bytes memory modelOut;",
        "    {",
        f"      {library}.Inputs memory i = {library}.Inputs({{",
        ",\n".join(f"  {field}" for field in fields),
        "      });",
        "      bool codeOk;",
        f"      (codeOk, codeOut) = address({target}).call(",
        f"        {encoded_call(f'{target}.{function}', arguments, model.solc)}",
        "      );",
        f"      {host} host = new {host}();",
        "      bool modelOk;",
        f"      (modelOk, modelOut) = address(host).call({encoded_call('host.run', 'i', model.solc)});",
        "      assert(codeOk == modelOk);",
        "      if (!codeOk) {",
        "        return;",
        "      }",
        "    }",
        *_compare(model, link),
        "  }",
        "}",
        "",
    ]
    return "\n".join(lines)


def _results(model: Model) -> list[str]:
    """The structs that the link decodes several values into: the contract's own values, and the model's, which are
    those and then the outputs."""
    returns = model.function.returns
    if returns.kind != "tuple" and not model.outputs:
        return []
    lines = []
    for name, types in (("CodeResults", model.results), ("ModelResults", parts(returns))):
        if types:
            lines += [f"  struct {name} {{", *(f"    {t.solidity} r{n};" for n, t in enumerate(types)), "  }", ""]
    return lines


def _compare(model: Model, link: Link) -> list[str]:
    """The asserts that the contract's result is the model's, each value of several returns, and that each output
    that the test reads after the call is the model's value of it, which the model returns after its own values."""
    returns = model.function.returns
    if returns.kind != "tuple" and not model.outputs:
        type_ = returns.solidity
        return [f"    assert(abi.decode(codeOut, ({type_})) == abi.decode(modelOut, ({type_})));"]
    lines = []
    if model.results:
        lines.append("    CodeResults memory codeResults = abi.decode(codeOut, (CodeResults));")
    lines.append("    ModelResults memory modelResults = abi.decode(modelOut, (ModelResults));")
    lines += [f"    assert(codeResults.r{n} == modelResults.r{n});" for n in range(len(model.results))]
    for n, output in enumerate(model.outputs, start=len(model.results)):
        lines.append(f"    assert(({link.post[output.var.name].strip()}) == modelResults.r{n});")
    return lines


def _proofs(model: Model):
    """Each row and lemma that the model uses, with whether it is of a Math call."""
    for function in (model.function, *model.callees):
        for e in walk(function):
            if isinstance(e, Op):
                operator = TABLE[e.op]
                for name in operator.proofs:
                    yield PROOFS[name], operator.kind == "library"
                if operator.kind == "bits":
                    yield constant_proof(e), False


def operators_test(models, stem: str, test_import: str = "forge-std/Test.sol") -> Generated:
    """The test of each operator-table row and lemma that the models use. A row or lemma of a Math call is proved on
    each copy of OpenZeppelin's Math that a model calls it from, as two copies can be two versions. The test of the
    first copy is `{stem}Operators.t.sol`, and it also holds the rows of the other operators. The test of the second
    copy is `{stem}2Operators.t.sol`, and so on. A test that has no row or lemma has no checks and no file."""
    copies = list(dict.fromkeys(model.math for model in models if model.math)) or [""]
    # The proofs of each copy's test, and the models that use them.
    tests: dict[str, tuple[list, list]] = {math: ([], []) for math in copies}
    for model in models:
        for proof, library in _proofs(model):
            proofs, users = tests[model.math if library and model.math else copies[0]]
            if proof not in proofs:
                proofs.append(proof)
            if model not in users:
                users.append(model)
    files, checks = {}, ()
    for n, (math, (proofs, users)) in enumerate(tests.items()):
        if proofs:
            test = _operators_test(users, f"{stem}{n + 1 if n else ''}", test_import, math, proofs)
            files.update(test.files)
            checks += test.checks
    return Generated(files, checks)


def _operators_test(models, stem: str, test_import: str, math: str, proofs: list) -> Generated:
    """The test `{stem}Operators.t.sol` of the proofs, with Math imported from `math`, for the models that use
    them."""
    first = models[0]
    used = ", ".join(model.name for model in models)
    wrappers = [p.wrapper for p in proofs if p.wrapper]
    lines = [
        f"// SPDX-License-Identifier: {first.spdx}",
        f"// Generated by solift: the operator-table rows and lemmas that {used} uses. Do not edit.",
        f"pragma solidity {first.pragma};",
        "",
        f'import {{Test}} from "{test_import}";',
        *([f'import {{Math}} from "{math}";'] if math else []),
        "",
        f"contract {stem}CheckedOperators {{",
        "\n".join(wrappers).rstrip("\n"),
        "}",
        "",
        f"contract {stem}Operators is Test {{",
        f"  {stem}CheckedOperators internal ops;",
        "",
        "  function setUp() public {",
        f"    ops = new {stem}CheckedOperators();",
        "  }",
        "",
        encoded_calls("\n".join(p.text(version(oldest(models))) for p in proofs).rstrip("\n"), oldest(models)),
        "}",
        "",
    ]
    checks = tuple(Check(f"{stem}Operators", proof.name, f"{stem}Operators.t.sol") for proof in proofs)
    return Generated({f"{stem}Operators.t.sol": "\n".join(lines)}, checks)
