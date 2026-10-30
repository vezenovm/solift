"""The command line of a spec module: `python -m <module> [--root PATH] [--workspace PATH] [--results PATH]
[--no-link] [--emit DIR]`.

`--root` is the project root, for a spec whose project lives outside this repo. The forge binary comes from
Z3SOL_FORGE.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import pathlib

from z3sol.run import Spec, run, summary
from z3sol.workspace import Workspace


def main(spec: Spec, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=f"Runs the z3sol spec {spec.name}.")
    parser.add_argument("--root", type=pathlib.Path, help="the root of the foundry project that holds the target")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path("build") / spec.name)
    parser.add_argument("--results", type=pathlib.Path, help="where to write the record (default: in the workspace)")
    parser.add_argument("--no-link", action="store_true", help="do not run Halmos")
    parser.add_argument("--emit", type=pathlib.Path, help="a directory to write the model and its link tests to")
    args = parser.parse_args(argv)
    if args.root is not None:
        spec = dataclasses.replace(spec, project=dataclasses.replace(spec.project, root=args.root.resolve()))
    if spec.project.root is None or not spec.project.root.is_dir():
        parser.error(f"the project root {spec.project.root} is not a directory: pass it with --root")
    workspace = Workspace(args.workspace)
    record = run(spec, workspace, link=not args.no_link, emit=args.emit)
    results = args.results or workspace.path / "results.json"
    results.write_text(json.dumps(record, indent=2) + "\n")
    print(summary(record))
    print(f"record: {results}")
    return 0 if record["ok"] else 1
