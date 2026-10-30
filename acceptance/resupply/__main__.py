"""The acceptance run against Resupply, at the commit that the exploit of 2025-06-26 drained:

    git clone https://github.com/resupplyfi/resupply && cd resupply
    git checkout 6c816badbf47fffddf81e683df81e56ba8cb6537 && npm ci --ignore-scripts
    python -m acceptance.resupply --root <resupply>

1. One run of the spec (solvency.py): its theorems, its vectors, and its Halmos link against the bytecode of Resupply's
   deploy profile.
2. The fix (fix.py): on the fixed source, the theorem that found the exploit proves, and the link passes.
3. Each mutant fails on the theorem it breaks.

The run fails when any of these fails, when the project is not the pinned commit, and when it changed during the run.
"""

import argparse
import dataclasses
import pathlib
import sys
import time

from acceptance.common import finish, pinned, run_mutants
from acceptance.resupply import fix, mutants
from acceptance.resupply.project import COMMIT
from acceptance.resupply.solvency import SOLVENT
from solift.run import run, summary
from solift.workspace import Workspace


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The acceptance run against Resupply.")
    parser.add_argument("--root", type=pathlib.Path, required=True, help="the Resupply checkout")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path("build") / "resupply")
    parser.add_argument("--results", type=pathlib.Path, help="where to write the record (default: in the workspace)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    failures = pinned(root, COMMIT)
    spec = dataclasses.replace(SOLVENT, project=dataclasses.replace(SOLVENT.project, root=root))
    workspace = Workspace(args.workspace)
    guard = spec.project.guard(workspace)
    times = {}

    start = time.time()
    record = run(spec, workspace.child("spec"))
    times["spec"] = time.time() - start
    print(summary(record))
    failures += [f"spec: {f}" for f in record["failures"]]

    print("\n== the fix")
    start = time.time()
    fixed = run(spec, workspace.child("fix"), replace=fix.replacements(root), vectors=False, only=(fix.THEOREM,))
    found = fix.problems(fixed)
    failures += [f"fix: {p}" for p in found]
    print(summary(fixed))
    print(("FIXED" if not found else "NOT FIXED") + f": on the fixed source, {fix.THEOREM} proves")
    times["fix"] = time.time() - start

    start = time.time()
    mutant_records, found = run_mutants(mutants.MUTANTS, [spec], root, workspace)
    failures += found
    times["mutants"] = time.time() - start

    failures += [f"resupply: {change}" for change in guard.changes()]
    result = {"spec": record, "fix": fixed, "mutants": mutant_records}
    return finish(result, failures, times, args.results or workspace.path / "acceptance.json")


sys.exit(main())
