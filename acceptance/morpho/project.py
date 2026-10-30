"""The Morpho Blue project, at its v1.0.0 release.

A conventional Foundry project: the overlay infers from Morpho's foundry config that it links foundry.toml and lib and
copies src, under the default profile (via-IR). It has none of Morpho's tests: its test directory holds the harness
and the generated files only. The harness keeps its place, test/MorphoSetup.sol, because a target is in it.
"""

import pathlib

from solift import Harness, Project

COMMIT = "55d2d99304fb3fb930c688462ae2ccabb1d533ad"
HARNESS = "test/MorphoSetup.sol"

PROJECT = Project.foundry()
SETUP = Harness(pathlib.Path(__file__).parent / "harness" / "MorphoSetup.sol", "MorphoSetup", at=HARNESS)
