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
    command = [binary(), "build", "--root", str(root), "--ast", "--build-info"]
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
    contract: str,
    profile: str | None = None,
    allow_paths=(),
) -> dict[str, str]:
    """Runs the forge tests of one contract and returns the status of each test function."""
    command = [binary(), "test", "--root", str(root), "--match-contract", f"^{contract}$", "--json"]
    result = subprocess.run(
        command, env=environment(out, cache, profile, allow_paths), capture_output=True, text=True, check=False
    )
    statuses = {}
    for line in result.stdout.splitlines():
        if not line.startswith("{"):
            continue
        for path, suite in json.loads(line).items():
            if path.rpartition(":")[2] == contract:
                for name, test_result in suite["test_results"].items():
                    statuses[name.partition("(")[0]] = test_result["status"]
    if not statuses and result.returncode:
        raise BuildError(f"{' '.join(command)} failed:\n{result.stdout}\n{result.stderr}")
    return statuses
