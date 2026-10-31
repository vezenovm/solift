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
reads nothing that the function wrote (extract.py).

The generated files are the model library and host (solidity.py), the link test, and a test of each operator-table
row and lemma that the model uses, which this build proves on its own bytecode and OpenZeppelin version.
"""

from __future__ import annotations

import dataclasses
import pathlib
import re
from dataclasses import dataclass, field

from solift.ir import Model, Op, parts, walk
from solift.ops import PROOFS, TABLE
from solift.solidity import imports, names
from solift.solidity import source as model_source

RESERVED = {"i", "host", "codeOk", "codeOut", "modelOk", "modelOut"}


class LinkError(Exception):
    pass


@dataclass(frozen=True)
class Harness:
    """A harness kept next to the spec, outside the project, as the setup of a link. The run adds `path` to the overlay
    at `at`, a path in the overlay that is by default `<generated>/harness/<file name>`, and the link test inherits
    `contract`. Its imports resolve from that place, so a file that it imports by a relative path goes in
    `Project.add` too."""

    path: pathlib.Path
    contract: str
    at: str | None = None

    def destination(self, generated: str) -> str:
        return self.at or f"{generated}/harness/{pathlib.Path(self.path).name}"


@dataclass(frozen=True)
class Link:
    """`setup` is the harness as `path:Contract`, with the path relative to the project root, or a `Harness` that the
    run adds to the overlay. `call` calls the contract
    under test from the harness, such as `quoter.exposedQuote(_gas, _price)`. `adapters` maps
    an input name, or a tuple of names, to the Solidity statements that set it, such as a struct argument built from the
    inputs for its fields. `observed` maps an input name to the expression that the test reads it from. `parameters`
    maps more symbolic parameters of the check, which are not inputs, to their types: an adapter can use them to set
    what the contract reads, and an observed expression reads the input that results, as for a cut point such as a
    price that the contract computes from a feed. `imports` are more import directives that the adapters need. `post`
    maps each output of the model to the expression that the test reads it from after the call, such as
    `pool.leftOf(address(this))` for a write or `psm.lastAmount()` for an argument that a stub records."""

    setup: str | Harness
    call: str
    adapters: dict = field(default_factory=dict)
    observed: dict = field(default_factory=dict)
    test_import: str = "forge-std/Test.sol"
    imports: tuple[str, ...] = ()
    parameters: dict = field(default_factory=dict)
    post: dict = field(default_factory=dict)


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

    for name in re.findall(r"\b[A-Za-z_]\w*\b", arguments):
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
        if name in RESERVED or re.fullmatch(r"(code|model)Result\d+", name):
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
    params = [f"    {v.type.solidity} {v.name}" for v in model.inputs if covered[v.name] != "observed"]
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
        "  function check_link(",
        ",\n".join(params),
        "  ) public {",
        *adapters,
        f"    {library}.Inputs memory i = {library}.Inputs({{",
        ",\n".join(fields),
        "    });",
        f"    (bool codeOk, bytes memory codeOut) = address({target}).call(",
        f"      abi.encodeCall({target}.{function}, ({arguments.strip()}))",
        "    );",
        f"    {host} host = new {host}();",
        "    (bool modelOk, bytes memory modelOut) = address(host).call(abi.encodeCall(host.run, (i)));",
        "    assert(codeOk == modelOk);",
        "    if (codeOk) {",
        *_compare(model, link),
        "    }",
        "  }",
        "}",
        "",
    ]
    return "\n".join(lines)


def _compare(model: Model, link: Link) -> list[str]:
    """The asserts that the contract's result is the model's, each value of several returns, and that each output
    that the test reads after the call is the model's value of it, which the model returns after its own values."""
    returns = model.function.returns
    if returns.kind != "tuple" and not model.outputs:
        type_ = returns.solidity
        return [f"      assert(abi.decode(codeOut, ({type_})) == abi.decode(modelOut, ({type_})));"]
    lines = []
    for side, types in (("code", model.results), ("model", parts(returns))):
        if types:
            declared = ", ".join(f"{t.solidity} {side}Result{n}" for n, t in enumerate(types))
            listed = ", ".join(t.solidity for t in types)
            target = declared if len(types) == 1 else f"({declared})"
            lines.append(f"      {target} = abi.decode({side}Out, ({listed}));")
    lines += [f"      assert(codeResult{n} == modelResult{n});" for n in range(len(model.results))]
    for n, output in enumerate(model.outputs, start=len(model.results)):
        lines.append(f"      assert(({link.post[output.var.name].strip()}) == modelResult{n});")
    return lines


def _proofs(model: Model):
    """Each row and lemma that the model uses, with whether it is of a Math call."""
    for function in (model.function, *model.callees):
        for e in walk(function):
            if isinstance(e, Op):
                operator = TABLE[e.op]
                for name in operator.proofs:
                    yield PROOFS[name], operator.kind == "library"


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
        "\n".join(p.check for p in proofs).rstrip("\n"),
        "}",
        "",
    ]
    checks = tuple(Check(f"{stem}Operators", proof.name, f"{stem}Operators.t.sol") for proof in proofs)
    return Generated({f"{stem}Operators.t.sol": "\n".join(lines)}, checks)
