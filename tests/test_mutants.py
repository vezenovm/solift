from functools import partial

import pytest

from acceptance import mutants
from acceptance.morpho.mutants import MUTANTS as MORPHO_MUTANTS
from acceptance.morpho.specs import SPECS as MORPHO_SPECS
from acceptance.mutants import apply, problems
from acceptance.resupply import fix
from acceptance.resupply.mutants import MUTANTS as RESUPPLY_MUTANTS
from acceptance.resupply.solvency import SOLVENT

# A mutant of C._quote in SOURCE, unless it names another function.
Mutant = partial(mutants.Mutant, spec="quoter", path="src/C.sol", function="_quote")

SOURCE = """\
contract C {
  function _quote(uint256 _gas) internal view returns (uint256) {
    return Math.min(cfg.margin, _gas);
  }

  function other(uint256 _gas) internal view returns (uint256) {
    return _gas + 1;
  }
}
"""


def test_a_mutant_changes_its_text_inside_its_function_only():
    mutant = Mutant("m", "Math.min(cfg.margin, _gas)", "cfg.margin", breaks="t")
    assert "return cfg.margin;" in apply(SOURCE, mutant)
    with pytest.raises(ValueError, match="does not occur exactly once, inside _quote"):
        apply(SOURCE, Mutant("m", "_gas + 1", "_gas", breaks="t"))
    with pytest.raises(ValueError, match="does not occur exactly once"):
        apply(SOURCE, Mutant("m", "_gas", "1", breaks="t"))
    assert "return _gas;" in apply(SOURCE, Mutant("m", "_gas + 1", "_gas", breaks="t", function="other"))
    overloaded = SOURCE.replace("function other(uint256 _gas)", "function _quote(uint256 _gas, bool)")
    assert "return _gas;" in apply(overloaded, Mutant("m", "_gas + 1", "_gas", breaks="t"))


def test_each_mutant_names_a_theorem_of_its_spec_that_rejects_it_and_the_fix_names_its_theorem():
    for specs, found in ((MORPHO_SPECS, MORPHO_MUTANTS), ([SOLVENT], RESUPPLY_MUTANTS)):
        by_name = {spec.name: spec for spec in specs}
        assert len({m.id for m in found}) == len(found)
        for m in found:
            ids = {t.id for t in by_name[m.spec].theorems}
            assert m.breaks in ids and set(m.holds) <= ids, m.id
        assert any(m.link for m in found)
    (theorem,) = [t for t in SOLVENT.theorems if t.id == fix.THEOREM]
    assert theorem.expect == "counterexample"
    assert set(fix.CHANGES) == {fix.CORE, fix.CONSTANTS}


def mutant_record(theorems, failures, link=None):
    spec = {"spec": "quoter", "theorems": theorems, "link": link, "failures": failures}
    return {"specs": [spec], "link": {"problems": [], "shared": []} if link is not None else None, "failures": failures}


def test_a_mutant_counts_as_rejected_only_when_its_one_expected_failure_is_the_only_failure():
    mutant = Mutant("m", "a", "b", breaks="t-broken", holds=("t-held",))
    broken = {"id": "t-broken", "verdict": "COUNTEREXAMPLE", "witness": None, "ok": False}
    held = {"id": "t-held", "verdict": "PROVED", "witness": "WITNESS", "ok": True}
    expected = "quoter: theorem t-broken: COUNTEREXAMPLE, expected PROVED"
    assert problems(mutant, mutant_record([broken, held], [expected])) == []
    vacuous = {**held, "witness": "VACUOUS", "ok": False}
    found = problems(
        mutant,
        mutant_record(
            [broken, vacuous], [expected, "quoter: theorem t-held: PROVED, witness VACUOUS, expected PROVED"]
        ),
    )
    assert found == ["quoter: theorem t-held: PROVED, witness VACUOUS, expected PROVED", "t-held is not ok"]
    shared = "link: Z3solOperators.check_lemma_minIsTheSmallerValue COUNTEREXAMPLE"
    assert problems(mutant, mutant_record([broken, held], [expected, shared])) == [shared]
    assert problems(Mutant("m", "a", "b", breaks="t-broken", link=True), mutant_record([broken], [expected])) == [
        "link: not run"
    ]


def test_only_the_expected_counterexample_of_the_broken_theorem_is_exempt():
    mutant = Mutant("m", "a", "b", breaks="t-broken")
    unsure = {"id": "t-broken", "verdict": "COUNTEREXAMPLE", "witness": "UNKNOWN", "ok": False, "detail": ""}
    failure = "quoter: theorem t-broken: COUNTEREXAMPLE, witness UNKNOWN, expected PROVED"
    assert problems(mutant, mutant_record([unsure], [failure])) == [failure]
    no_values = {**unsure, "witness": "WITNESS", "detail": "the solver gave no value for x."}
    expected = "quoter: theorem t-broken: COUNTEREXAMPLE, expected PROVED"
    assert problems(mutant, mutant_record([no_values], [expected])) == ["t-broken: the solver gave no value for x."]
