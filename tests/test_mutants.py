import pytest

from acceptance.oxide.mutants import MUTANTS, Mutant, apply
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
    assert {m.spec for m in MUTANTS} == {"deposit-quote", "deposit-priced-gas", "withdrawal-quote"}
    assert any(m.link for m in MUTANTS)
