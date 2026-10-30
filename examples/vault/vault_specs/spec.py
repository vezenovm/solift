"""The specs of the vault's share math (src/Vault.sol), as a consumer writes them. From examples/vault, or from a copy
of it made with `cp -rL`:

    solift reads src/Vault.sol:Vault.previewRedeem
    solift run vault_specs.spec --root .
"""

import pathlib

import z3

from solift import Harness, Link, Project, Spec

SETUP = Harness(pathlib.Path(__file__).parent / "VaultSetup.sol", "VaultSetup")
PROJECT = Project.foundry()
READS = {"ASSET.balanceOf(address(this))": "held", "totalSupply": "supply"}
ADAPTERS = {"held": "asset.set(held);", "supply": "vault.setSupply(supply);"}

REDEEM = Spec(
    name="vault-redeem",
    target="src/Vault.sol:Vault.previewRedeem",
    reads=READS,
    link=Link(setup=SETUP, call="vault.previewRedeem(_shares)", adapters=ADAPTERS),
    project=PROJECT,
)
DEPOSIT = Spec(
    name="vault-deposit",
    target="src/Vault.sol:Vault.previewDeposit",
    reads=READS,
    link=Link(setup=SETUP, call="vault.previewDeposit(_assets)", adapters=ADAPTERS),
    project=PROJECT,
)
SPECS = [REDEEM, DEPOSIT]


def pays(q):
    return q.result > 0


@REDEEM.theorem(assume=("no-revert", "inside"), witness=pays)
def redeem_rounds_down(q):
    """a redeem pays at most the shares' exact part of the assets, shares * (held + 1) / (supply + 1)"""
    return q.result * (q.supply + 1) <= q._shares * (q.held + 1)


def within_the_supply(q):
    """the shares are at most the supply, and the supply and the assets held are below 2**64"""
    return z3.And(q._shares <= q.supply, q.supply < 2**64, q.held < 2**64)


@REDEEM.theorem(assume=("no-revert",), given=within_the_supply, witness=pays)
def redeem_pays_at_most_the_assets(q):
    """a redeem of at most the supply pays at most the assets that the vault holds"""
    return q.result <= q.held


@REDEEM.theorem(assume=("no-revert", "inside"), expect="counterexample", reason="the redeem rounds down")
def redeem_pays_the_exact_part(q):
    """a redeem pays exactly the shares' part of the assets"""
    return q.result * (q.supply + 1) == q._shares * (q.held + 1)


@DEPOSIT.theorem(assume=("no-revert", "inside"), witness=pays)
def deposit_mints_at_most_the_exact_shares(q):
    """a deposit mints at most the assets' exact part of the shares, assets * (supply + 1) / (held + 1)"""
    return q.result * (q.held + 1) <= q._assets * (q.supply + 1)
