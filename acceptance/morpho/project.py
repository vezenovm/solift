"""The Morpho Blue project, at its v1.0.0 release.

The overlay builds with Morpho's default profile (via-IR) and has none of Morpho's tests: its test directory holds the
harness and the generated files only.
"""

import pathlib

from z3sol.workspace import Project

HARNESS = pathlib.Path(__file__).parent / "harness"
COMMIT = "55d2d99304fb3fb930c688462ae2ccabb1d533ad"

PROJECT = Project(
    link=("foundry.toml", "lib"),
    copy=("src",),
    add={"test/MorphoSetup.sol": HARNESS / "MorphoSetup.sol"},
    generated="test/z3sol",
)
