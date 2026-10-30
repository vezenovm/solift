import pytest

from acceptance.oxide.mutants import MUTANTS, Mutant, apply

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


def test_each_mutant_names_the_theorem_that_rejects_it():
    assert len({m.id for m in MUTANTS}) == len(MUTANTS)
    assert {m.breaks for m in MUTANTS} == {"deposit-no-farming", "deposit-liveness"}
    assert any(m.link for m in MUTANTS)
