"""Runs forge.

The binary is configurable (`Z3SOL_FORGE`, default `forge`), because some toolchains ship it under another name, such
as `aztec-forge`. A build writes only to the output and cache directories that the caller passes, through FOUNDRY_OUT
and FOUNDRY_CACHE_PATH, which take precedence over the profile. Other FOUNDRY_ variables of the caller's environment are
dropped, so that the profile and these settings are the whole configuration.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

# The flags of Halmos's own build. Every build of a run uses them, so that one output directory serves the extraction,
# the vectors and Halmos: solc compiles each file once, and every artifact has its AST.
FLAGS = ("--ast", "--extra-output", "storageLayout", "metadata")


class BuildError(Exception):
    pass


def binary() -> str:
    return os.environ.get("Z3SOL_FORGE", "forge")


def environment(out: pathlib.Path, cache: pathlib.Path, profile: str | None = None, allow_paths=()) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("FOUNDRY_")}
    env["FOUNDRY_OUT"] = str(out)
    env["FOUNDRY_CACHE_PATH"] = str(cache)
    if profile:
        env["FOUNDRY_PROFILE"] = profile
    if allow_paths:
        env["FOUNDRY_ALLOW_PATHS"] = "[" + ",".join(str(p) for p in allow_paths) + "]"
    return env


def build(
    root: pathlib.Path, out: pathlib.Path, cache: pathlib.Path, profile: str | None = None, allow_paths=()
) -> list[dict]:
    """Builds the project at `root` with the AST and the build info, and returns the build infos: one solc standard JSON
    input and output for each compile job. `out` and `cache` must be empty or absent."""
    for path in (out, cache):
        if path.exists() and any(path.iterdir()):
            raise BuildError(f"{path} is not empty: each build starts from empty output and cache directories")
    command = [binary(), "build", "--root", str(root), *FLAGS, "--build-info"]
    result = subprocess.run(
        command, env=environment(out, cache, profile, allow_paths), capture_output=True, text=True, check=False
    )
    if result.returncode:
        raise BuildError(f"{' '.join(command)} failed:\n{result.stdout}\n{result.stderr}")
    return [json.loads(path.read_text()) for path in sorted((out / "build-info").glob("*.json"))]


def version() -> str:
    result = subprocess.run([binary(), "--version"], capture_output=True, text=True, check=True)
    return result.stdout.splitlines()[0].strip()


def test(
    root: pathlib.Path,
    out: pathlib.Path,
    cache: pathlib.Path,
    contracts: tuple[str, ...],
    profile: str | None = None,
    allow_paths=(),
) -> dict[tuple[str, str, str], str]:
    """Runs the forge tests of the named contracts and returns the status of each test function, by source path,
    contract and function name."""
    pattern = "^(" + "|".join(contracts) + ")$"
    command = [binary(), "test", "--root", str(root), *FLAGS, "--match-contract", pattern, "--json"]
    result = subprocess.run(
        command, env=environment(out, cache, profile, allow_paths), capture_output=True, text=True, check=False
    )
    found = statuses(result.stdout, contracts)
    if not found and result.returncode:
        raise BuildError(f"{' '.join(command)} failed:\n{result.stdout}\n{result.stderr}")
    return found


def statuses(stdout: str, contracts: tuple[str, ...]) -> dict[tuple[str, str, str], str]:
    """The status of each test of the named contracts in `forge test --json` output. Two results for one test, from two
    contracts of one name or an overloaded test, are an error."""
    found = {}
    for line in stdout.splitlines():
        if not line.startswith("{"):
            continue
        for key, suite in json.loads(line).items():
            path, _, contract = key.rpartition(":")
            if contract not in contracts:
                continue
            for name, test_result in suite["test_results"].items():
                test = name.partition("(")[0]
                if any((c, t) == (contract, test) for _, c, t in found):
                    raise BuildError(f"two results for {contract}.{test}, one from {path}")
                found[(path, contract, test)] = test_result["status"]
    return found
