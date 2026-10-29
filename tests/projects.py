"""The fixture project and the Quoter link, shared by the link and Halmos tests."""

import pathlib

from z3sol.link import Link
from z3sol.workspace import Project

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUOTER = "tests/fixtures/contracts/Quoter.sol"
QUOTER_READS = {
    "$config.margin": "margin",
    "$config.cap": "cap",
    "$floor": "floor",
    "TOKEN.balanceOf(address(this))": "pot",
    "tx.gasprice": "txGasPrice",
    "block.basefee": "baseFee",
}
QUOTER_LINK = Link(
    setup="tests/fixtures/test/QuoterSetup.sol:QuoterSetup",
    call="quoter.exposedQuote(_gas, _price)",
    adapters={
        ("margin", "cap", "floor"): "quoter.setConfig(margin, cap, floor);",
        "pot": "token.set(pot);",
        "baseFee": "vm.fee(baseFee);",
    },
    observed={"txGasPrice": "tx.gasprice"},
)


def fixtures() -> Project:
    return Project(
        root=ROOT, link=("foundry.toml", "lib"), copy=("tests/fixtures",), generated="tests/fixtures/test/z3sol"
    )
