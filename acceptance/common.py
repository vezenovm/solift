"""The parts that the acceptance runs share: the pin of a project to one commit, the mutant runs, and the record."""

import json
import pathlib
import subprocess

from acceptance.mutants import apply, problems
from solift.run import run


def pinned(root: pathlib.Path, commit: str) -> list[str]:
    """Why the project at `root` is not the pinned commit with no change to its tracked files."""
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    if head.returncode:
        return [f"{root} is not a git checkout of the pinned commit {commit}"]
    found = [] if head.stdout.strip() == commit else [f"{root} is at {head.stdout.strip()}, not at {commit}"]
    changed = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
                             capture_output=True, text=True).stdout.strip()  # fmt: skip
    return found + ([f"{root} has changes to its tracked files"] if changed else [])


def run_mutants(mutants, specs, root: pathlib.Path, workspace) -> tuple[dict, list[str]]:
    """Runs each mutant on its spec, and returns the records and the failures: a mutant that is not rejected."""
    print("\n== mutants")
    by_name = {spec.name: spec for spec in specs}
    records, failures = {}, []
    for mutant in mutants:
        source = (root / mutant.path).read_text()
        record = run(
            by_name[mutant.spec],
            workspace.child(f"mutant-{mutant.id}"),
            replace={mutant.path: apply(source, mutant)},
            link=mutant.link,
            vectors=False,
            only=(mutant.breaks, *mutant.holds),
        )
        records[mutant.id] = record
        found = problems(mutant, record)
        failures += [f"mutant {mutant.id}: {p}" for p in found]
        status = "REJECTED" if not found else "NOT REJECTED"
        print(f"{status} {mutant.id}: {mutant.breaks} fails" + (", and its link passes" if mutant.link else ""))
        for problem in found:
            print(f"FAILED {problem}")
    return records, failures


def finish(result: dict, failures: list[str], times: dict, path: pathlib.Path) -> int:
    """Writes the record and prints the time of each part. The run fails when any part failed."""
    result = {"ok": not failures, "failures": failures, "times": times, **result}
    path.write_text(json.dumps(result, indent=2) + "\n")
    print("\n" + ", ".join(f"{name} {seconds:.0f}s" for name, seconds in times.items()))
    print(f"{'ok' if not failures else f'{len(failures)} failure(s)'}; record: {path}")
    return 0 if not failures else 1
