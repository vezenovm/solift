"""The Oxide project that every spec of the acceptance run builds: Oxide's sources and harnesses, and the harnesses of
the contracts that Oxide does not prove yet, which the overlay adds from this directory at the paths where they would
live in Oxide."""

import pathlib

from z3sol.workspace import Project

HARNESS = pathlib.Path(__file__).parent / "harness"

PROJECT = Project(
    profile="subsidy-proofs",
    link=("foundry.toml", "node_modules", ".deps"),
    copy=("src", "proofs/subsidies/src/SubsidyHarnesses.sol", "proofs/subsidies/test/SubsidySetup.sol"),
    add={
        "proofs/subsidies/test/CapsSetup.sol": HARNESS / "CapsSetup.sol",
    },
    generated="proofs/subsidies/test/z3sol",
)
