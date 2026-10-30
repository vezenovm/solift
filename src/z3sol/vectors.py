"""Sampled vectors: the z3 terms against the emitted Solidity.

The link proves the emitted Solidity equal to the bytecode, and Halmos proves the rows of the operator table. Nothing
proves that smt.py composes the rows as the Solidity does: the vectors check that on samples. For each comparison and
each min of the model, z3 finds inputs that do not revert where the two sides are equal and one apart, with inputs that
pay first, so that an early return does not skip the comparison. Random inputs then cover each outcome: pays (a
positive or true result), zero, and reverts. z3 finds an input for an outcome that no random input reaches, such as a
revert that only inputs near 2**256 reach. A generated forge test runs the emitted Solidity on each vector and
compares whether it reverts, and its result, with the z3 terms.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import z3

from z3sol.ir import BOOL, Model
from z3sol.smt import domain, symbols, terms
from z3sol.solidity import names

CANDIDATES = 3000
PER_OUTCOME = 60
BOUNDARY_TIMEOUT_MS = 5_000
EDGES = [0, 1, 2, 2**64 - 1, 2**128 - 1, 2**128, 2**255, 2**256 - 1]
OUTCOMES = ("pays", "zero", "reverts")


class VectorError(Exception):
    pass


@dataclass(frozen=True)
class Vector:
    values: dict
    reverts: bool
    result: int | bool

    @property
    def outcome(self) -> str:
        return "reverts" if self.reverts else "pays" if self.result else "zero"


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
            return Vector(values, True, False if model.function.returns == BOOL else 0)
        value = z3.simplify(z3.substitute(t.value, *pairs))
        if z3.is_true(value) or z3.is_false(value):
            return Vector(values, False, z3.is_true(value))
        if not z3.is_int_value(value):
            raise VectorError(f"{model.name}: the result does not evaluate on {values}")
        return Vector(values, False, value.as_long())


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
    pays = t.value if z3.is_bool(t.value) else t.value > 0
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
    built = built or _Terms(model)
    inputs, t = built.inputs, built.terms
    if not t.comparisons:
        return []
    pays = t.value if z3.is_bool(t.value) else t.value > 0
    found = []
    for a, b in t.comparisons:
        for offset in (-1, 0, 1):
            undecided = False
            for wanted in (pays, z3.BoolVal(True)):
                s = z3.Solver()
                s.set("timeout", BOUNDARY_TIMEOUT_MS)
                s.add(domain(model, inputs), z3.Not(t.reverts), a == b + offset, wanted)
                r = s.check()
                if r == z3.sat:
                    found.append(built.evaluate(_values(model, inputs, s.model())))
                    break
                undecided = undecided or r == z3.unknown
            else:
                if undecided:
                    raise VectorError(f"{model.name}: z3 gave unknown at the boundary of a comparison, {a} vs {b}")
    return found


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


def sample(model: Model, impossible: tuple[str, ...] = ()) -> list[Vector]:
    """The boundary vectors, then up to PER_OUTCOME random vectors for each outcome. `impossible` names the outcomes
    that no input reaches, which the vectors then do not require."""
    built = _Terms(model)
    found = boundary(model, built)
    if not found and built.terms.comparisons:
        raise VectorError(f"{model.name}: z3 found no input at the boundary of any comparison")
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
    for outcome in OUTCOMES:
        if outcome in impossible:
            vector = kept[outcome][0] if kept[outcome] else reaching(model, outcome, built)
            if vector is not None:
                raise VectorError(f"{model.name}: the spec calls {outcome} impossible, but {vector.values} reaches it")
        elif not kept[outcome]:
            vector = reaching(model, outcome, built)
            if vector is None or vector.outcome != outcome:
                raise VectorError(f"{model.name}: neither random inputs nor z3 reached {outcome}")
            kept[outcome].append(vector)
    return found + [vector for outcome in OUTCOMES for vector in kept[outcome]]


def vectors_source(model: Model, vectors: list[Vector], test_import: str = "forge-std/Test.sol") -> str:
    """A forge test with one function for each vector. Each vector is the ABI encoding of the inputs struct."""
    library, host = names(model)
    stem = library.removesuffix("Model")
    returns = model.function.returns.solidity
    lines = [
        f"// SPDX-License-Identifier: {model.spdx}",
        f"// Generated by z3sol: the z3 terms of {model.name} against its emitted Solidity. Do not edit.",
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
        f"  function _check(bytes memory _inputs, bool _reverts, {returns} _result) internal {{",
        f"    {library}.Inputs memory i = abi.decode(_inputs, ({library}.Inputs));",
        "    (bool ok, bytes memory out) = address(host).call(abi.encodeCall(host.run, (i)));",
        '    assertEq(!ok, _reverts, "reverts");',
        "    if (ok) {",
        f'      assertEq(abi.decode(out, ({returns})), _result, "result");',
        "    }",
        "  }",
    ]
    for n, vector in enumerate(vectors):
        encoded = "".join(f"{int(vector.values[v.name]):064x}" for v in model.inputs)
        result = str(vector.result).lower() if isinstance(vector.result, bool) else str(vector.result)
        lines += [
            "",
            f"  function test_vector_{n:03d}() public {{",
            f'    _check(hex"{encoded}", {"true" if vector.reverts else "false"}, {result});',
            "  }",
        ]
    lines += ["}", ""]
    return "\n".join(lines)
