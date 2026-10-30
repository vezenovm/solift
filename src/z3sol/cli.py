"""The command line of a spec module: `python -m <module> [--root PATH] [--workspace PATH] [--results PATH]
[--no-link] [--emit DIR]`.

A module runs one spec or several specs of one project in one run. `--root` is the project root, for specs whose project
lives outside this repo. The forge binary comes from Z3SOL_FORGE. The record is JSON (run.py).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib
from collections.abc import Sequence

from z3sol.run import Spec, run, summary
from z3sol.workspace import Workspace


def main(specs: Spec | Sequence[Spec], argv: list[str] | None = None) -> int:
    specs = [specs] if isinstance(specs, Spec) else list(specs)
    name = specs[0].name if len(specs) == 1 else "run"
    parser = argparse.ArgumentParser(description=f"Runs the z3sol specs {', '.join(s.name for s in specs)}.")
    parser.add_argument("--root", type=pathlib.Path, help="the root of the foundry project that holds the targets")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path("build") / name)
    parser.add_argument("--results", type=pathlib.Path, help="where to write the record (default: in the workspace)")
    parser.add_argument("--no-link", action="store_true", help="do not run Halmos")
    parser.add_argument("--emit", type=pathlib.Path, help="a directory to write the models and their link tests to")
    args = parser.parse_args(argv)
    if args.root is not None:
        project = dataclasses.replace(specs[0].project, root=args.root.resolve())
        specs = [dataclasses.replace(spec, project=project) for spec in specs]
    root = specs[0].project.root
    if root is None or not root.is_dir():
        parser.error(f"the project root {root} is not a directory: pass it with --root")
    workspace = Workspace(args.workspace)
    record = run(specs, workspace, link=not args.no_link, emit=args.emit)
    results = args.results or workspace.path / "results.json"
    results.write_text(json.dumps(record, indent=2) + "\n")
    print(summary(record))
    print(f"record: {results}")
    return 0 if record["ok"] else 1
