"""The spec of a supply and a withdraw of the shares it minted, in Morpho Blue's share math, and its theorem.

The harness's supplyThenWithdraw computes what supply and withdraw compute in one block with no interest accrued
(harness/MorphoSetup.sol): supply mints toSharesDown of the assets at the market's totals and adds both to the totals,
and withdraw of those shares pays toAssetsDown of them at the new totals. That supply and withdraw make these calls
on these totals is a hypothesis: the tool does not model their storage writes.

The theorem is Certora's supplyWithdraw (certora/specs/ExactMath.spec): there is no profit from a supply followed by a
withdraw. Certora proves it on Morpho's supply and withdraw, storage included, and for a withdraw by assets too.
"""

from acceptance.morpho.project import PROJECT
from solift.link import Link
from solift.run import Spec

SUPPLY_WITHDRAW = Spec(
    name="morpho-supply-withdraw",
    target="test/MorphoSetup.sol:MorphoShares.supplyThenWithdraw",
    reads={},
    link=Link(
        setup="test/MorphoSetup.sol:MorphoSetup",
        call="shares.supplyThenWithdraw(assets, totalSupplyAssets, totalSupplyShares)",
    ),
    project=PROJECT,
)


def paid(q):
    return q.result > 0


@SUPPLY_WITHDRAW.theorem(assume=("no-revert",), witness=paid)
def supply_then_withdraw_pays_at_most_the_supply(q):
    """a withdraw of the shares that a supply of assets minted pays at most the assets: Certora's supplyWithdraw, on
    the share math"""
    return q.result <= q.assets
