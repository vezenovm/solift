"""The acceptance run against Morpho Blue, at its v1.0.0 release:

    git clone https://github.com/morpho-org/morpho-blue && cd morpho-blue
    git checkout v1.0.0 && git submodule update --init --recursive
    python -m acceptance.morpho --root <morpho-blue>

1. One run of every spec (specs.py): _isHealthy, and a supply then a withdraw in the share math, with their theorems,
   their vectors, and their Halmos links against the bytecode of Morpho's default profile.
2. Each mutant fails on the theorem it breaks.

The run fails when any of these fails, when the project is not the pinned commit, and when it changed during the run.
"""

import argparse
import dataclasses
import pathlib
import sys
import time

from acceptance.common import finish, pinned, run_mutants
from acceptance.morpho import mutants
from acceptance.morpho.project import COMMIT
from acceptance.morpho.specs import SPECS
from solift.run import run, summary
from solift.workspace import Workspace


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The acceptance run against Morpho Blue.")
    parser.add_argument("--root", type=pathlib.Path, required=True, help="the Morpho Blue checkout")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path("build") / "morpho")
    parser.add_argument("--results", type=pathlib.Path, help="where to write the record (default: in the workspace)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    failures = pinned(root, COMMIT)
    project = dataclasses.replace(SPECS[0].project, root=root)
    specs = [dataclasses.replace(spec, project=project) for spec in SPECS]
    workspace = Workspace(args.workspace)
    guard = project.guard(workspace)
    times = {}

    start = time.time()
    record = run(specs, workspace.child("specs"))
    times["specs"] = time.time() - start
    print(summary(record))
    failures += [f"specs: {f}" for f in record["failures"]]

    start = time.time()
    mutant_records, found = run_mutants(mutants.MUTANTS, specs, root, workspace)
    failures += found
    times["mutants"] = time.time() - start

    failures += [f"morpho: {change}" for change in guard.changes()]
    result = {"specs": record, "mutants": mutant_records}
    return finish(result, failures, times, args.results or workspace.path / "acceptance.json")


sys.exit(main())
