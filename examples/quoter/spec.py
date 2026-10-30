"""An example spec: Quoter._quote from tests/fixtures/contracts/Quoter.sol, with the harness in
tests/fixtures/test/QuoterSetup.sol. Run it from the repo root with `python -m examples.quoter`."""

import pathlib

from z3sol.link import Link
from z3sol.run import Spec
from z3sol.workspace import Project

WAD = 10**18
QUOTER = "tests/fixtures/contracts/Quoter.sol"

PROJECT = Project(
    root=pathlib.Path(__file__).resolve().parents[2],
    link=("foundry.toml", "lib"),
    copy=("tests/fixtures",),
    generated="tests/fixtures/test/z3sol",
)
READS = {
    "$config.margin": "margin",
    "$config.cap": "cap",
    "$floor": "floor",
    "TOKEN.balanceOf(address(this))": "pot",
    "tx.gasprice": "txGasPrice",
    "block.basefee": "baseFee",
}
LINK = Link(
    setup="tests/fixtures/test/QuoterSetup.sol:QuoterSetup",
    call="quoter.exposedQuote(_gas, _price)",
    adapters={
        ("margin", "cap", "floor"): "quoter.setConfig(margin, cap, floor);",
        "pot": "token.set(pot);",
        "baseFee": "vm.fee(baseFee);",
    },
    observed={"txGasPrice": "tx.gasprice"},
)
SPEC = Spec(name="quoter", target=f"{QUOTER}:Quoter._quote", reads=READS, link=LINK, project=PROJECT)


def paying(q):
    return q.result > 0


@SPEC.theorem(assume=("no-revert", "inside"), witness=paying)
def quote_at_most_gas_plus_margin(q):
    """the quote is at most the gas at tx.gasprice plus the margin"""
    return q.result * WAD <= q._gas * q.txGasPrice * q._price + q.margin * WAD


@SPEC.theorem(
    assume=("no-revert", "inside"),
    expect="counterexample",
    reason="the quote pays the margin on top of the gas",
    show=("margin",),
)
def quote_at_most_gas(q):
    """the quote is at most the gas at tx.gasprice"""
    return q.result * WAD <= q._gas * q.txGasPrice * q._price
