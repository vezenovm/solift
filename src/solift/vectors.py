"""Sampled vectors: the z3 terms against the emitted Solidity.

The link proves the emitted Solidity equal to the bytecode, and Halmos proves the rows of the operator table. Nothing
proves that smt.py composes the rows as the Solidity does: the vectors check that on samples. For each comparison and
each min or max of the model, z3 finds inputs that do not revert, under which the comparison runs, where the two sides
are equal and one apart, with inputs that pay first. Random inputs then cover each outcome: pays (a positive or true
result, or for several returns a positive or true value), zero, and reverts. z3 finds an input for an outcome that no
random input reaches, such as a revert that only inputs near 2**256 reach. An outcome or a boundary that z3 proves no
input reaches is recorded as unreachable, and an "unknown" from z3 is an error. A generated forge test runs the emitted
Solidity on each vector and compares whether it reverts, and its result, with the z3 terms.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import z3

from solift.ir import BOOL, Model, Type, parts
from solift.smt import domain, symbols, terms
from solift.solidity import names

CANDIDATES = 3000
PER_OUTCOME = 60
BOUNDARY_TIMEOUT_MS = 5_000
EDGES = [0, 1, 2, 2**64 - 1, 2**128 - 1, 2**128, 2**255, 2**256 - 1]
OUTCOMES = ("pays", "zero", "reverts")


class VectorError(Exception):
    pass


@dataclass(frozen=True)
class Vector:
    """The inputs, whether the model reverts, and its result: a tuple for a function of several returns."""

    values: dict
    reverts: bool
    result: int | bool | tuple

    @property
    def outcome(self) -> str:
        pays = any(self.result) if isinstance(self.result, tuple) else self.result
        return "reverts" if self.reverts else "pays" if pays else "zero"


def _pays(value) -> z3.BoolRef:
    """Where a result pays: it is positive or true, or for several returns one of its values is."""
    if isinstance(value, tuple):
        return z3.Or(*(_pays(v) for v in value))
    return value if z3.is_bool(value) else value > 0


def _zero(type_: Type):
    if type_.kind == "tuple":
        return tuple(_zero(part) for part in type_.parts)
    return False if type_ == BOOL else 0


@dataclass(frozen=True)
class Samples:
    """The vectors, and how each outcome and each comparison boundary fared: `outcomes` maps each outcome to reached or
    unreachable, and `boundaries` counts the boundaries (a comparison at one offset) that a vector reached and those
    that z3 proved unreachable."""

    vectors: list[Vector]
    outcomes: dict[str, str]
    boundaries: dict[str, int]


class _Terms:
    """A model's symbolic terms, built once and evaluated on each vector by substitution, so that the vectors check the
    same terms that the theorems use."""

    def __init__(self, model: Model):
        self.model = model
        self.inputs = symbols(model)
        self.terms = terms(model, self.inputs)

    def evaluate(self, values: dict) -> Vector:
        model, t = self.model, self.terms
        pairs = [
            (self.inputs[v.name], z3.BoolVal(values[v.name]) if v.type == BOOL else z3.IntVal(values[v.name]))
            for v in model.inputs
        ]
        reverts = z3.simplify(z3.substitute(t.reverts, *pairs))
        if not (z3.is_true(reverts) or z3.is_false(reverts)):
            raise VectorError(f"{model.name}: the revert condition does not evaluate on {values}")
        if z3.is_true(reverts):
            return Vector(values, True, _zero(model.function.returns))
        found = []
        for result in t.value if isinstance(t.value, tuple) else (t.value,):
            value = z3.simplify(z3.substitute(result, *pairs))
            if z3.is_true(value) or z3.is_false(value):
                found.append(z3.is_true(value))
            elif z3.is_int_value(value):
                found.append(value.as_long())
            else:
                raise VectorError(f"{model.name}: the result does not evaluate on {values}")
        return Vector(values, False, tuple(found) if isinstance(t.value, tuple) else found[0])


def evaluate(model: Model, values: dict) -> Vector:
    return _Terms(model).evaluate(values)


def _values(model: Model, inputs: dict, m: z3.ModelRef) -> dict:
    values = {}
    for v in model.inputs:
        x = m.eval(inputs[v.name], model_completion=True)
        values[v.name] = z3.is_true(x) if v.type == BOOL else x.as_long()
    return values


def reaching(model: Model, outcome: str, built: _Terms | None = None) -> Vector | None:
    """An input with the outcome, from z3, or None when z3 proves that no input reaches it. A VectorError when z3
    cannot decide."""
    built = built or _Terms(model)
    inputs, t = built.inputs, built.terms
    pays = _pays(t.value)
    condition = {"reverts": t.reverts, "pays": z3.And(z3.Not(t.reverts), pays)}.get(
        outcome, z3.And(z3.Not(t.reverts), z3.Not(pays))
    )
    s = z3.Solver()
    s.set("timeout", BOUNDARY_TIMEOUT_MS)
    s.add(domain(model, inputs), condition)
    r = s.check()
    if r == z3.unknown:
        raise VectorError(f"{model.name}: z3 could not decide whether an input reaches {outcome}: {s.reason_unknown()}")
    if r == z3.unsat:
        return None
    return built.evaluate(_values(model, inputs, s.model()))


def boundary(model: Model, built: _Terms | None = None) -> list[Vector]:
    return _boundaries(model, built)[0]


def _boundaries(model: Model, built: _Terms | None = None) -> tuple[list[Vector], int]:
    """The vectors at the boundary of each comparison, where the comparison runs, and the number of boundaries that z3
    proves no input reaches."""
    built = built or _Terms(model)
    inputs, t = built.inputs, built.terms
    pays = _pays(t.value)
    found, unreachable = [], 0
    for runs, a, b in t.comparisons:
        for offset in (-1, 0, 1):
            undecided = False
            for wanted in (pays, z3.BoolVal(True)):
                s = z3.Solver()
                s.set("timeout", BOUNDARY_TIMEOUT_MS)
                s.add(domain(model, inputs), z3.Not(t.reverts), runs, a == b + offset, wanted)
                r = s.check()
                if r == z3.sat:
                    found.append(built.evaluate(_values(model, inputs, s.model())))
                    break
                undecided = undecided or r == z3.unknown
            else:
                if undecided:
                    raise VectorError(f"{model.name}: z3 gave unknown at the boundary of a comparison, {a} vs {b}")
                unreachable += 1
    return found, unreachable


def draw(rng: random.Random, bound: int | None, drawn: list[int]):
    """A value for one input. Edge values and small values reach the reverts and the early returns. A value equal to
    another input, or one off it, reaches more comparisons at their boundary."""
    if bound is None:
        return rng.random() < 0.5
    r = rng.random()
    if r < 0.15:
        value = rng.choice(EDGES)
    elif r < 0.25 and drawn:
        value = rng.choice(drawn) + rng.choice((-1, 0, 1))
    elif r < 0.45:
        value = rng.getrandbits(rng.randint(1, 3))
    elif r < 0.9:
        value = rng.getrandbits(rng.randint(1, 72))
    else:
        value = rng.getrandbits(rng.randint(1, 256))
    return min(max(value, 0), bound - 1)


def sample(model: Model, impossible: tuple[str, ...] = ()) -> Samples:
    """The boundary vectors, then up to PER_OUTCOME random vectors for each outcome. `impossible` names the outcomes
    that the spec asserts no input reaches: a vector that reaches one is an error."""
    built = _Terms(model)
    found, unreachable = _boundaries(model, built)
    rng = random.Random(model.name)
    kept = {outcome: [] for outcome in OUTCOMES}
    for _ in range(CANDIDATES):
        values, drawn = {}, []
        for v in model.inputs:
            values[v.name] = draw(rng, None if v.type == BOOL else v.type.bound, drawn)
            if v.type != BOOL:
                drawn.append(values[v.name])
        vector = built.evaluate(values)
        if len(kept[vector.outcome]) < PER_OUTCOME:
            kept[vector.outcome].append(vector)
    outcomes = {}
    for outcome in OUTCOMES:
        if not kept[outcome]:
            vector = reaching(model, outcome, built)
            if vector is not None and vector.outcome != outcome:
                raise VectorError(f"{model.name}: z3's input {vector.values} for {outcome} gives {vector.outcome}")
            if vector is not None:
                kept[outcome].append(vector)
        if kept[outcome] and outcome in impossible:
            values = kept[outcome][0].values
            raise VectorError(f"{model.name}: the spec calls {outcome} impossible, but {values} reaches it")
        outcomes[outcome] = "reached" if kept[outcome] else "unreachable"
    vectors = found + [vector for outcome in OUTCOMES for vector in kept[outcome]]
    return Samples(vectors, outcomes, {"reached": len(found), "unreachable": unreachable})


def vectors_source(model: Model, vectors: list[Vector], test_import: str = "forge-std/Test.sol") -> str:
    """A forge test with one function for each vector. Each vector is the ABI encoding of the inputs struct, and the
    result is compared as its ABI words, one for each value, which hold a number, a bool, an address or an enum member
    alike."""
    library, host = names(model)
    stem = library.removesuffix("Model")
    lines = [
        f"// SPDX-License-Identifier: {model.spdx}",
        f"// Generated by solift: the z3 terms of {model.name} against its emitted Solidity. Do not edit.",
        f"pragma solidity {model.pragma};",
        "",
        f'import {{Test}} from "{test_import}";',
        f'import {{{library}, {host}}} from "./{stem}.sol";',
        "",
        f"contract {stem}Vectors is Test {{",
        f"  {host} internal host;",
        "",
        "  function setUp() public {",
        f"    host = new {host}();",
        "  }",
        "",
        "  function _check(bytes memory _inputs, bool _reverts, bytes memory _result) internal {",
        f"    {library}.Inputs memory i = abi.decode(_inputs, ({library}.Inputs));",
        "    (bool ok, bytes memory out) = address(host).call(abi.encodeCall(host.run, (i)));",
        '    assertEq(!ok, _reverts, "reverts");',
        "    if (ok) {",
        '      assertEq(out, _result, "result");',
        "    }",
        "  }",
    ]
    count = len(parts(model.function.returns))
    for n, vector in enumerate(vectors):
        encoded = "".join(f"{int(vector.values[v.name]):064x}" for v in model.inputs)
        results = vector.result if isinstance(vector.result, tuple) else (vector.result,)
        if len(results) != count:
            raise VectorError(f"{model.name}: a vector has {len(results)} values, and the model returns {count}")
        result = "".join(f"{int(value):064x}" for value in results)
        lines += [
            "",
            f"  function test_vector_{n:03d}() public {{",
            f'    _check(hex"{encoded}", {"true" if vector.reverts else "false"}, hex"{result}");',
            "  }",
        ]
    lines += ["}", ""]
    return "\n".join(lines)
