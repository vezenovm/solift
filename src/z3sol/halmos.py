"""The Halmos link engine.

Halmos runs `forge build --ast` itself, without `--out`, and reads the build from `--forge-build-out`. So the run sets
FOUNDRY_OUT and FOUNDRY_CACHE_PATH to the same directories, and puts a `forge` shim for the configured binary on PATH.
Halmos skips, with only a warning, a contract whose cached build has no AST. So it builds from empty directories, or
from the directories of a build with Halmos's own flags (forge.FLAGS), where every artifact has its AST. Halmos's own
default solver may change, so the run names yices.

A check passes only with a PASS result and no path stopped at the loop bound: Halmos reports such a check as a pass,
with a warning. The run fails when a declared check has no result or two (two contracts of one name, or an overloaded
check), when the run has a check that it did not declare, when Halmos exits with a failure, and when the log shows a
skipped contract or a path at the loop bound.
"""

from __future__ import annotations

import importlib.metadata
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

from z3sol import forge
from z3sol.link import Check
from z3sol.workspace import Workspace

EXITCODES = {0: "PASS", 1: "COUNTEREXAMPLE", 2: "TIMEOUT", 3: "STUCK", 4: "REVERT_ALL", 5: "EXCEPTION"}
LOG_FAILURES = {
    "parsing failure": "Halmos skipped a contract, so its checks did not run",
    "loop unrolling bound": "a path stopped at the loop bound, so the proof is not complete",
}


@dataclass(frozen=True)
class CheckResult:
    """`verdict` is PASS, COUNTEREXAMPLE, TIMEOUT, STUCK, REVERT_ALL, EXCEPTION, BOUNDED (a pass with a path at the loop
    bound), MISSING (no result) or UNDECLARED (a result for a check that the run did not declare)."""

    check: Check
    verdict: str
    time: float | None = None
    paths: int | None = None

    @property
    def ok(self) -> bool:
        return self.verdict == "PASS"


@dataclass(frozen=True)
class LinkRun:
    results: tuple[CheckResult, ...]
    problems: tuple[str, ...]
    log: str

    @property
    def ok(self) -> bool:
        return not self.problems and all(r.ok for r in self.results)


class HalmosEngine:
    """The interface of a link engine: `name`, `versions()`, and `run()`."""

    name = "halmos"

    def __init__(self, solver: str = "yices", loop: int = 4, extra_args: tuple[str, ...] = ()):
        self.solver = solver
        self.loop = loop
        self.extra_args = extra_args

    def versions(self) -> dict[str, str]:
        return {
            "halmos": importlib.metadata.version("halmos"),
            "yices-solver": importlib.metadata.version("yices-solver"),
            "z3-solver (Halmos)": importlib.metadata.version("z3-solver"),
            "forge": forge.version(),
        }

    def run(
        self,
        root: pathlib.Path,
        workspace: Workspace,
        checks: tuple[Check, ...],
        profile: str | None = None,
        allow_paths=(),
        fresh: bool = True,
        build: tuple[pathlib.Path, pathlib.Path] | None = None,
    ) -> LinkRun:
        """Runs the declared checks. `build` is the output and cache directories of a build with forge.FLAGS, which
        Halmos then extends instead of building from empty directories. `fresh=False` keeps the existing directories of
        an earlier Halmos run, which only the tests of the skipped-contract guard need."""
        if build is not None:
            out, cache = build
        elif fresh:
            out, cache = workspace.fresh("halmos-out"), workspace.fresh("halmos-cache")
        else:
            out, cache = workspace.path / "halmos-out", workspace.path / "halmos-cache"
        shim = workspace.fresh("shim")
        binary = shutil.which(forge.binary())
        if binary is None:
            raise FileNotFoundError(f"no {forge.binary()} on PATH")
        (shim / "forge").symlink_to(binary)
        env = forge.environment(out, cache, profile, allow_paths)
        env["PATH"] = os.pathsep.join([str(shim), str(pathlib.Path(sys.executable).parent), env.get("PATH", "")])
        report = workspace.path / "halmos.json"
        report.unlink(missing_ok=True)
        contracts = sorted({c.contract for c in checks})
        command = [
            sys.executable,
            "-m",
            "halmos",
            "--root",
            str(root),
            "--forge-build-out",
            str(out),
            "--solver",
            self.solver,
            "--loop",
            str(self.loop),
            "--match-contract",
            "^(" + "|".join(contracts) + ")$",
            "--json-output",
            str(report),
            *self.extra_args,
        ]
        result = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True)
        log = _plain(result.stdout + result.stderr)
        (workspace.path / "halmos.log").write_text(log)
        data = json.loads(report.read_text()) if report.exists() else None
        return judge(checks, data, log)


def judge(checks: tuple[Check, ...], data: dict | None, log: str) -> LinkRun:
    """The verdict of each declared check, from Halmos's JSON output and its log."""
    problems = []
    if data is None:
        problems.append("Halmos wrote no results")
        data = {"test_results": {}}
    folded = " ".join(log.split())
    for needle, problem in LOG_FAILURES.items():
        if needle in folded:
            problems.append(problem)
    if data.get("exitcode"):
        problems.append(f"Halmos exited with {data['exitcode']}")
    found = {}
    for key, results in data["test_results"].items():
        path, _, contract = key.rpartition(":")
        for r in results:
            check = Check(contract, r["name"].partition("(")[0], path)
            if any((c.contract, c.function) == (check.contract, check.function) for c in found):
                problems.append(f"two results for {check.contract}.{check.function}: {key} and another")
            found[check] = r
    results = []
    for check in checks:
        match = [c for c in found if (c.contract, c.function) == (check.contract, check.function)]
        match = [c for c in match if not check.path or c.path == check.path]
        if not match:
            results.append(CheckResult(check, "MISSING"))
            continue
        r = found.pop(match[0])
        verdict = EXITCODES.get(r["exitcode"], f"EXIT {r['exitcode']}")
        if verdict == "PASS" and r.get("num_bounded_loops"):
            verdict = "BOUNDED"
        results.append(CheckResult(check, verdict, (r.get("time") or [None])[0], (r.get("num_paths") or [None])[0]))
    for check in found:
        results.append(CheckResult(check, "UNDECLARED"))
    return LinkRun(tuple(results), tuple(problems), log)


def _plain(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)
