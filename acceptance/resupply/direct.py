"""The Resupply theorems proved directly on the bytecode with Halmos, without the extracted model: the baseline of the
README's proof performance. A check can take hours, so CI does not run it.

    python -m acceptance.resupply.direct --root <resupply> --solver yices --budget 3600 [--check <name>]

The setup of the Resupply checkout is in acceptance/resupply/__main__.py. Each check is one Halmos run of one test of
harness/ResupplyDirect.t.sol, which states a theorem of solvency.py on the contract, with the harness, inputs and
adapters of solift's link. The test builds in the acceptance run's overlay, with Resupply's deploy profile, and the
check of the fix applies the fix to it first (fix.py). The pair's bytecode must be the one that the acceptance run's
link checked, when that run's workspace is there (--acceptance). Before Halmos, forge runs the test's helpers on
concrete values, and replays solift's counterexample of the exploit, which holds of the first check only. A
counterexample that Halmos reports is replayed the same way, which says whether it holds.

Halmos runs with its defaults and the link's loop bound, no timeout for a query (--solver-timeout-assertion 0), and
--early-exit, so that a run with a counterexample ends there. It writes each query to a file for its solver, and the
run keeps them in the workspace, to count the queries that the solver answered. The budget is the wall-clock time of
the Halmos process, which the run kills with its solvers when the budget ends: the result is then TIMEOUT. Halmos finds
a solver in ~/.halmos/solvers, then on PATH, then in this venv, and the run puts this venv first on PATH. A solver
that Halmos does not find is an error, before Halmos would download it.

The record holds each result with its times, the counterexamples and their replays, the sha256 of the pair's
bytecode, the versions of the tools, the machine and the load average. The run fails when a step fails, when the
project is not the pinned commit or changed, when the bytecode is not the link's, and when a result contradicts
solift's: a counterexample that holds of a theorem that solift proves, or a proof of the one that it refutes. A
TIMEOUT does not fail it.
"""

import argparse
import dataclasses
import datetime
import hashlib
import json
import os
import pathlib
import platform
import shutil
import signal
import subprocess
import sys
import time

from acceptance.common import pinned
from acceptance.resupply import fix
from acceptance.resupply.project import COMMIT, HARNESS, PROJECT
from acceptance.resupply.solvency import (
    solvent_debt_within_twice_the_ltv,
    solvent_debt_within_twice_the_ltv_up_to_a_price_of_1e36,
)
from solift import forge
from solift.halmos import EXITCODES
from solift.workspace import Workspace

TEST = "ResupplyDirect.t.sol"
CONTRACT = "ResupplyDirect"
HELPERS = "ResupplyDirectWide"
REPLAY = "ResupplyDirectReplay"
PAIR = ("ResupplySetup.sol", "ResupplyPairHarness")
LOOP = 4  # the link's loop bound (solift.halmos.HalmosEngine)
# solift's counterexample of the exploit, as the link's inputs: with no total shares, the debt is the shares.
SOLIFT_COUNTEREXAMPLE = {
    "_borrower": 0,
    "price": 10**36 + 2,
    "storedRate": 0,
    "maxLTV": 1,
    "collateral": 2,
    "totalAmount": 0,
    "totalShares": 0,
    "shares": 80000000000001,
}
# The parameters of each check, in order, as the link declares them.
PARAMETERS = (
    ("_borrower", "address"),
    ("price", "uint256"),
    ("storedRate", "uint256"),
    ("maxLTV", "uint256"),
    ("collateral", "uint256"),
    ("totalAmount", "uint128"),
    ("totalShares", "uint128"),
    ("shares", "uint256"),
)


@dataclasses.dataclass(frozen=True)
class Check:
    """A theorem of solvency.py, stated by `function` of the test, on the pinned source or on the fix, with the verdict
    that solift gives it and the price cap of its hypotheses."""

    name: str
    theorem: str
    function: str
    fixed: bool
    solift: str
    cap: int


CHECKS = {
    c.name: c
    for c in (
        Check(
            "exploit",
            solvent_debt_within_twice_the_ltv.id,
            "check_solvent_debt_within_twice_the_ltv",
            False,
            "COUNTEREXAMPLE",
            2**256 - 1,
        ),
        Check(
            "exploit-up-to-1e36",
            solvent_debt_within_twice_the_ltv_up_to_a_price_of_1e36.id,
            "check_solvent_debt_within_twice_the_ltv_up_to_a_price_of_1e36",
            False,
            "PROVED",
            10**36,
        ),
        Check(
            "fix",
            solvent_debt_within_twice_the_ltv.id,
            "check_solvent_debt_within_twice_the_ltv",
            True,
            "PROVED",
            2**256 - 1,
        ),
    )
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The Resupply theorems, proved directly on the bytecode with Halmos.")
    parser.add_argument("--root", type=pathlib.Path, required=True, help="the Resupply checkout")
    parser.add_argument("--solver", default="yices", help="Halmos's --solver: yices, z3, cvc5, bitwuzla, ...")
    parser.add_argument("--budget", type=float, default=3600, help="the wall-clock seconds of the Halmos run")
    parser.add_argument("--check", choices=sorted(CHECKS), action="append", help="a check to run (default: all)")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path("build") / "resupply-direct")
    parser.add_argument(
        "--acceptance",
        type=pathlib.Path,
        default=pathlib.Path("build") / "resupply",
        help="the workspace of the acceptance run, whose link's bytecode each check must have",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    failures = pinned(root, COMMIT)
    if failures:
        print("\n".join(f"FAILED {f}" for f in failures))
        return 1
    workspace = Workspace(args.workspace)
    records = []
    for name in args.check or list(CHECKS):
        check = CHECKS[name]
        child = workspace.child(f"{name}-{args.solver}")
        try:
            record = run_check(check, root, child, args.solver, args.budget, args.acceptance.resolve())
        except (forge.BuildError, FileNotFoundError) as err:
            record = {"check": name, "theorem": check.theorem, "solver": args.solver, "failures": [str(err)]}
        records.append(record)
        print(line(record))
        failures += [f"{name}: {f}" for f in record["failures"]]
    path = workspace.path / f"direct-{args.solver}.json"
    path.write_text(json.dumps({"ok": not failures, "failures": failures, "checks": records}, indent=2) + "\n")
    print("\n".join(f"FAILED {f}" for f in failures))
    print(f"{'ok' if not failures else f'{len(failures)} failure(s)'}; record: {path}")
    return 0 if not failures else 1


def run_check(
    check: Check, root: pathlib.Path, workspace: Workspace, solver: str, budget: float, acceptance: pathlib.Path
) -> dict:
    """Builds the overlay with the test, runs the helpers' tests, replays solift's counterexample, runs Halmos on the
    check under the budget, and replays each counterexample that Halmos reports."""
    project = dataclasses.replace(PROJECT, root=root, add={**PROJECT.add, f"_/{TEST}": HARNESS / TEST})
    record = {
        "check": check.name,
        "theorem": check.theorem,
        "function": check.function,
        "fixed": check.fixed,
        "solift": check.solift,
        "solver": solver,
        "budget": budget,
        "started": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "machine": machine(),
        "sha256": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (HARNESS / TEST, pathlib.Path(__file__).resolve())
        },
        "failures": [],
    }
    guard = project.guard(workspace)
    overlay = project.overlay(workspace, replace=fix.replacements(root) if check.fixed else None)
    out, cache = workspace.fresh("out"), workspace.fresh("cache")
    env = environment(workspace, out, cache, project.profile, (root,))
    record["versions"] = versions(solver, env)
    if record["versions"]["solver"] is None:
        record["failures"].append(f"Halmos finds no {solver} solver in ~/.halmos/solvers, on PATH or in the venv")
        return record
    start = time.monotonic()
    forge.build(overlay, out, cache, project.profile, (root,))
    record["build seconds"] = round(time.monotonic() - start, 2)
    record["bytecode sha256"] = bytecode(out)
    link = acceptance / ("fix" if check.fixed else "spec") / "build-out"
    record["link bytecode sha256"] = bytecode(link) if (link / PAIR[0]).exists() else None
    if record["link bytecode sha256"] not in (None, record["bytecode sha256"]):
        record["failures"].append(f"the pair's bytecode is not the one that the link in {link} checked")
    helpers = forge.test(overlay, out, cache, (HELPERS,), project.profile, (root,))
    record["helpers"] = {name: status for (_, _, name), status in sorted(helpers.items())}
    if not helpers or any(status != "Success" for status in helpers.values()):
        record["failures"].append(f"the helpers' tests: {record['helpers']}")
        return record
    (solift,) = replay([{"values": SOLIFT_COUNTEREXAMPLE}], check, overlay, project, out, cache, root)
    record["solift's counterexample holds"] = solift["holds"]
    if solift["holds"] != (check.solift == "COUNTEREXAMPLE"):
        record["failures"].append(f"solift's counterexample {'holds' if solift['holds'] else 'does not hold'}")

    record["load before"] = os.getloadavg()
    record.update(halmos(check, overlay, workspace, out, solver, budget, env))
    record["load after"] = os.getloadavg()
    if record["verdict"] == "COUNTEREXAMPLE":
        record["replays"] = replay(record["counterexamples"], check, overlay, project, out, cache, root)
    record["result"] = result(record)
    record["failures"] += problems(check, record)
    record["failures"] += [f"resupply: {change}" for change in guard.changes()]
    (workspace.path / "record.json").write_text(json.dumps(record, indent=2) + "\n")
    return record


def environment(workspace: Workspace, out, cache, profile, allow) -> dict:
    """The environment of forge and Halmos, as the link's (solift.halmos): the build directories, a `forge` shim for
    the configured binary, and this venv first on PATH."""
    shim = workspace.fresh("shim")
    binary = shutil.which(forge.binary())
    if binary is None:
        raise FileNotFoundError(f"no {forge.binary()} on PATH")
    (shim / "forge").symlink_to(binary)
    env = forge.environment(out, cache, profile, allow)
    env["PATH"] = os.pathsep.join([str(shim), str(pathlib.Path(sys.executable).parent), env.get("PATH", "")])
    return env


def halmos(check: Check, overlay, workspace: Workspace, out, solver: str, budget: float, env: dict) -> dict:
    """Runs Halmos on the check, and kills it with its solvers when the budget ends."""
    report = workspace.path / "halmos.json"
    command = [
        sys.executable,
        "-m",
        "halmos",
        "--root",
        str(overlay),
        "--forge-build-out",
        str(out),
        "--solver",
        solver,
        "--loop",
        str(LOOP),
        "--solver-timeout-assertion",
        "0",
        "--early-exit",
        "--match-contract",
        f"^{CONTRACT}$",
        "--match-test",
        f"^{check.function}\\(",
        "--json-output",
        str(report),
        "--dump-smt-directory",
        str(workspace.fresh("queries")),
    ]
    log = (workspace.path / "halmos.log").open("w")
    start = time.monotonic()
    process = subprocess.Popen(command, cwd=overlay, env=env, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True)  # fmt: skip
    try:
        returncode = process.wait(timeout=budget)
        timed_out = False
    except subprocess.TimeoutExpired:
        _kill(process)
        returncode, timed_out = None, True
    wall = time.monotonic() - start
    log.close()
    found = {
        "command": command,
        "wall seconds": round(wall, 2),
        "returncode": returncode,
        "queries": queries(workspace.path / "queries"),
    }
    if timed_out:
        return {**found, "verdict": "TIMEOUT", "seconds": None, "paths": None, "counterexamples": []}
    data = json.loads(report.read_text()) if report.exists() else {"test_results": {}}
    results = [r for rs in data["test_results"].values() for r in rs if r["name"].startswith(f"{check.function}(")]
    if len(results) != 1:
        return {**found, "verdict": "MISSING", "seconds": None, "paths": None, "counterexamples": []}
    (r,) = results
    return {
        **found,
        "verdict": EXITCODES.get(r["exitcode"], f"EXIT {r['exitcode']}"),
        "seconds": (r.get("time") or [None])[0],
        "paths": (r.get("num_paths") or [None])[0],
        "counterexamples": [counterexample(m) for m in r.get("models") or []],
    }


def _kill(process: subprocess.Popen) -> None:
    """Ends the process group of Halmos, which holds its solver processes."""
    for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 10)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=grace)
            return
        except subprocess.TimeoutExpired:
            continue


def queries(directory: pathlib.Path) -> dict:
    """The answers of the solver to the queries that Halmos wrote: the first query of each path, with multiplication
    and division as uninterpreted functions, and the refined one, with the real operations, which Halmos writes when
    the first one has a model that uses them. A query with no answer has no output, or an empty one when the run
    stopped its solver."""
    counts: dict[str, dict[str, int]] = {"first": {}, "refined": {}}
    for query in sorted(directory.rglob("*.smt2")):
        out = query.with_name(query.name + ".out")
        answer = (out.read_text().partition("\n")[0].strip() if out.exists() else "") or "unanswered"
        kind = counts["refined" if query.name.endswith(".refined.smt2") else "first"]
        kind[answer] = kind.get(answer, 0) + 1
    return {kind: dict(sorted(found.items())) for kind, found in counts.items()}


def counterexample(found: dict) -> dict:
    """The values of a model that Halmos reports, by parameter name, from the names of its variables, such as
    `p_price_uint256_8e8e310_00`. A parameter that the model leaves out is 0."""
    values = {}
    for variable in (found.get("model") or {}).values():
        for name, type_ in PARAMETERS:
            if variable["full_name"].startswith(f"p_{name}_{type_}_"):
                values[name] = variable["value"]
    return {"values": {name: values.get(name, 0) for name, _ in PARAMETERS}, "valid": found.get("is_valid")}


def replay(counterexamples, check: Check, overlay, project, out, cache, root) -> list[dict]:
    """Replays each counterexample with forge: it holds when the hypotheses hold and the bound fails. Each replay
    replaces the last."""
    tests = []
    for n, found in enumerate(counterexamples):
        arguments = ", ".join(_literal(found["values"][name], type_) for name, type_ in PARAMETERS)
        tests.append(
            f"  function test_replay_{n}() public {{\n"
            f"    (bool hypotheses, bool bound) = outcome({arguments}, {check.cap});\n"
            '    assertTrue(hypotheses, "the hypotheses hold");\n'
            '    assertFalse(bound, "the bound fails");\n'
            "  }\n"
        )
    source = (
        "// SPDX-License-Identifier: Apache-2.0\n"
        "pragma solidity 0.8.28;\n\n"
        f'import {{{CONTRACT}}} from "_/{TEST}";\n\n'
        f"contract {REPLAY} is {CONTRACT} {{\n" + "\n".join(tests) + "}\n"
    )
    project.write(overlay, f"{REPLAY}.t.sol", source)
    statuses = forge.test(overlay, out, cache, (REPLAY,), project.profile, (root,))
    by_name = {name: status for (_, _, name), status in statuses.items()}
    return [{**found, "holds": by_name.get(f"test_replay_{n}") == "Success"} for n, found in enumerate(counterexamples)]


def _literal(value: int, type_: str) -> str:
    if type_ == "address":
        return f"address(uint160({value}))"
    return f"{type_}({value})"


def result(record: dict) -> str:
    """PROVED, COUNTEREXAMPLE (one that holds), INVALID COUNTEREXAMPLE (none holds), TIMEOUT, or Halmos's verdict."""
    if record["verdict"] == "PASS":
        return "PROVED"
    if record["verdict"] == "COUNTEREXAMPLE":
        return "COUNTEREXAMPLE" if any(r["holds"] for r in record["replays"]) else "INVALID COUNTEREXAMPLE"
    return record["verdict"]


def problems(check: Check, record: dict) -> list[str]:
    """A result that contradicts solift's, or a Halmos run that did not give a verdict."""
    found = []
    if record["result"] in ("PROVED", "COUNTEREXAMPLE") and record["result"] != check.solift:
        found.append(f"Halmos gives {record['result']} and solift {check.solift}")
    if record["result"] not in ("PROVED", "COUNTEREXAMPLE", "INVALID COUNTEREXAMPLE", "TIMEOUT"):
        found.append(f"Halmos gives {record['result']}; see {record['command'][-1]} and halmos.log")
    return found


def line(record: dict) -> str:
    head = f"{record.get('result', 'ERROR')} [{record['check']}] {record['theorem']} with {record['solver']}"
    if "wall seconds" not in record:
        return head
    seconds = record.get("seconds")
    shown = f"{seconds:.2f}s" if seconds is not None else f"stopped at the budget of {record['budget']:.0f}s"
    return f"{head} [{shown}, wall {record['wall seconds']}s, paths {record.get('paths')}, queries {record['queries']}]"


def bytecode(out: pathlib.Path) -> str:
    """The sha256 of the pair's deployed bytecode, the bytecode that the link checks."""
    artifact = json.loads((out / PAIR[0] / f"{PAIR[1]}.json").read_text())
    return hashlib.sha256(artifact["deployedBytecode"]["object"].encode()).hexdigest()


def versions(solver: str, env: dict) -> dict:
    """The versions of Halmos, of the solver binary that Halmos finds, and of forge."""
    probe = (
        "import sys; from halmos.solvers import SOLVERS, find_solver_binary; "
        "print(find_solver_binary(SOLVERS[sys.argv[1]]) or '')"
    )
    found = subprocess.run([sys.executable, "-c", probe, solver], env=env, capture_output=True, text=True)
    binary = found.stdout.strip() or None
    version = None
    if binary:
        shown = subprocess.run([binary, "--version"], capture_output=True, text=True)
        version = (shown.stdout or shown.stderr).strip().splitlines()[0]
    halmos_version = subprocess.run([sys.executable, "-m", "halmos", "--version"], env=env, capture_output=True,
                                    text=True).stdout.strip()  # fmt: skip
    return {
        "python": platform.python_version(),
        "halmos": halmos_version,
        "solver": binary,
        "solver version": version,
        "forge": forge.version(),
    }


def machine() -> dict:
    """The CPU, the memory and the OS."""
    cpu, memory = platform.processor(), None
    if sys.platform == "darwin":
        cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout
        memory = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout or 0)
    elif pathlib.Path("/proc/cpuinfo").exists():
        names = [x for x in pathlib.Path("/proc/cpuinfo").read_text().splitlines() if x.startswith("model name")]
        cpu = names[0].partition(":")[2] if names else cpu
        total = [x for x in pathlib.Path("/proc/meminfo").read_text().splitlines() if x.startswith("MemTotal")]
        memory = int(total[0].split()[1]) * 1024 if total else None
    return {"cpu": cpu.strip(), "cpus": os.cpu_count(), "memory bytes": memory, "os": platform.platform()}


if __name__ == "__main__":
    sys.exit(main())
