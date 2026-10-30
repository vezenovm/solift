import pytest

from acceptance.oxide.mutants import MUTANTS, Mutant, apply, problems
from acceptance.oxide.specs import SPECS

SOURCE = """\
contract C {
  function _quoteSubsidy(uint256 _fee) internal view returns (uint256) {
    return Math.min(cfg.targetRelayerProfit, _fee);
  }

  function other(uint256 _fee) internal view returns (uint256) {
    return _fee + 1;
  }
}
"""


def test_a_mutant_changes_its_text_inside_quote_subsidy_only():
    mutant = Mutant("m", "Math.min(cfg.targetRelayerProfit, _fee)", "cfg.targetRelayerProfit", breaks="t")
    assert "return cfg.targetRelayerProfit;" in apply(SOURCE, mutant)
    with pytest.raises(ValueError, match="does not occur exactly once, inside _quoteSubsidy"):
        apply(SOURCE, Mutant("m", "_fee + 1", "_fee", breaks="t"))
    with pytest.raises(ValueError, match="does not occur exactly once"):
        apply(SOURCE, Mutant("m", "_fee", "1", breaks="t"))
    assert "return _fee;" in apply(SOURCE, Mutant("m", "_fee + 1", "_fee", breaks="t", function="other"))


def test_each_mutant_names_a_theorem_of_its_spec_that_rejects_it():
    specs = {spec.name: spec for spec in SPECS}
    assert len({m.id for m in MUTANTS}) == len(MUTANTS)
    for m in MUTANTS:
        ids = {t.id for t in specs[m.spec].theorems}
        assert m.breaks in ids and set(m.holds) <= ids, m.id
        assert m.path == specs[m.spec].target.partition(":")[0], m.id
    assert {m.spec for m in MUTANTS} == {"deposit-quote", "deposit-priced-gas", "withdrawal-quote", "caps-available"}
    assert any(m.link for m in MUTANTS)


def mutant_record(theorems, failures, link=None):
    spec = {"spec": "deposit-quote", "theorems": theorems, "link": link, "failures": failures}
    return {"specs": [spec], "link": {"problems": [], "shared": []} if link is not None else None, "failures": failures}


def test_a_mutant_counts_as_rejected_only_when_its_one_expected_failure_is_the_only_failure():
    mutant = Mutant("m", "a", "b", breaks="t-broken", holds=("t-held",))
    broken = {"id": "t-broken", "verdict": "COUNTEREXAMPLE", "witness": None, "ok": False}
    held = {"id": "t-held", "verdict": "PROVED", "witness": "WITNESS", "ok": True}
    expected = "deposit-quote: theorem t-broken: COUNTEREXAMPLE, expected PROVED"
    assert problems(mutant, mutant_record([broken, held], [expected])) == []
    vacuous = {**held, "witness": "VACUOUS", "ok": False}
    found = problems(
        mutant,
        mutant_record(
            [broken, vacuous], [expected, "deposit-quote: theorem t-held: PROVED, witness VACUOUS, expected PROVED"]
        ),
    )
    assert found == ["deposit-quote: theorem t-held: PROVED, witness VACUOUS, expected PROVED", "t-held is not ok"]
    shared = "link: Z3solOperators.check_lemma_minIsTheSmallerValue COUNTEREXAMPLE"
    assert problems(mutant, mutant_record([broken, held], [expected, shared])) == [shared]
    assert problems(Mutant("m", "a", "b", breaks="t-broken", link=True), mutant_record([broken], [expected])) == [
        "link: not run"
    ]


def test_only_the_expected_counterexample_of_the_broken_theorem_is_exempt():
    mutant = Mutant("m", "a", "b", breaks="t-broken")
    unsure = {"id": "t-broken", "verdict": "COUNTEREXAMPLE", "witness": "UNKNOWN", "ok": False, "detail": ""}
    failure = "deposit-quote: theorem t-broken: COUNTEREXAMPLE, witness UNKNOWN, expected PROVED"
    assert problems(mutant, mutant_record([unsure], [failure])) == [failure]
    no_values = {**unsure, "witness": "WITNESS", "detail": "the solver gave no value for x."}
    expected = "deposit-quote: theorem t-broken: COUNTEREXAMPLE, expected PROVED"
    assert problems(mutant, mutant_record([no_values], [expected])) == ["t-broken: the solver gave no value for x."]
