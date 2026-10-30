"""The Resupply project, at the commit of the pair that the exploit of 2025-06-26 drained.

The overlay builds with Resupply's deploy profile: the settings it deploys with (solc 0.8.28, via-IR, 200 optimizer
runs), and only what the files in `_` import, as its source, test and script directory is `_`. The harness is there.
"""

import pathlib

from z3sol.workspace import Project

HARNESS = pathlib.Path(__file__).parent / "harness"
COMMIT = "6c816badbf47fffddf81e683df81e56ba8cb6537"

PROJECT = Project(
    profile="deploy",
    link=("foundry.toml", "remappings.txt", "node_modules"),
    copy=("src",),
    add={"_/ResupplySetup.sol": HARNESS / "ResupplySetup.sol"},
    generated="_/z3sol",
)
