"""The Resupply project, at the commit of the pair that the exploit of 2025-06-26 drained.

The overlay builds with Resupply's deploy profile: the settings it deploys with (solc 0.8.28, via-IR, 200 optimizer
runs), and only what the files in `_` import, as its source, test and script directory is `_`. The harness is there.

The variants run the spec again under other compilers and settings (acceptance/compilers.py). Resupply's sources pin
solc 0.8.28, so the variant with solc 0.8.30 moves each pin, and the harness takes any 0.8 version from 0.8.28.
"""

import pathlib

from acceptance.compilers import Variant
from solift.workspace import Project

HARNESS = pathlib.Path(__file__).parent / "harness"
COMMIT = "6c816badbf47fffddf81e683df81e56ba8cb6537"

PROJECT = Project(
    profile="deploy",
    link=("foundry.toml", "remappings.txt", "node_modules"),
    copy=("src",),
    add={"_/ResupplySetup.sol": HARNESS / "ResupplySetup.sol"},
    generated="_/solift",
)

VARIANTS = (
    Variant("as-deployed"),
    Variant("optimizer-off", {"optimizer": False}),
    Variant("optimizer-runs-1m", {"optimizer_runs": 1_000_000}),
    Variant("legacy-pipeline", {"via_ir": False}),
    Variant("solc-0.8.30", solc="0.8.30"),
)
