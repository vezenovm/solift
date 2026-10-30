"""The command line.

`solift run MODULE[:ATTR] [--root PATH] [--workspace PATH] [--results PATH] [--no-link] [--emit DIR]` runs the specs of
a Python module: `ATTR` names a Spec or a list of them, and without it the module has exactly one of `SPEC` and
`SPECS`. The module is found from the current directory, as `python -m` finds it, and imports without writing bytecode.

`solift reads TARGET [--root PATH] [--profile NAME] [--copy PATH ...] [--json]` builds the project and prints the read
map that the target needs, a link skeleton, the internal calls that a read map entry can cut, and the writes and
events that the model leaves out (discover.py).

A spec module can also run itself with `sys.exit(main(SPEC))` and `python -m <module>`, with the options of `run`.
`--root` is the project root, for specs whose project lives outside their repo. The forge binary comes from
SOLIFT_FORGE. The record is JSON (run.py).
"""

from __future__ import annotations

import argparse
import dataclasses
import importlib
import json
import os
import pathlib
import sys
from collections.abc import Sequence

from solift import forge
from solift.discover import as_json, discover, report
from solift.run import Spec, run, summary
from solift.workspace import Project, Workspace


class SpecError(Exception):
    pass


def main(specs: Spec | Sequence[Spec], argv: list[str] | None = None) -> int:
    """Runs the specs with the options of `solift run`, for a spec module that runs itself."""
    specs = [specs] if isinstance(specs, Spec) else list(specs)
    parser = argparse.ArgumentParser(description=f"Runs the solift specs {', '.join(s.name for s in specs)}.")
    _run_options(parser)
    return _run(specs, parser.parse_args(argv), parser)


def entrypoint(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="solift", description="Proves arithmetic theorems on Solidity functions.")
    commands = parser.add_subparsers(dest="command", required=True)
    runs = commands.add_parser("run", help="run the specs of a module")
    runs.add_argument("specs", help="the module of the specs, as package.module or package.module:ATTR")
    _run_options(runs)
    reads = commands.add_parser("reads", help="print the read map and a link skeleton for a target")
    reads.add_argument("target", help="the function, as path:Contract.function")
    reads.add_argument("--root", type=pathlib.Path, default=pathlib.Path("."), help="the foundry project")
    reads.add_argument("--profile", help="the foundry profile to build with")
    reads.add_argument("--copy", action="append", help="a project path to copy (default: the source directory)")
    reads.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path("build") / "reads")
    reads.add_argument("--json", action="store_true", help="print JSON")
    args = parser.parse_args(argv)
    if args.command == "reads":
        return _reads(args)
    try:
        specs = load_specs(args.specs)
    except SpecError as err:
        parser.error(str(err))
    return _run(specs, args, runs)


def load_specs(text: str) -> list[Spec]:
    """The specs that `module` or `module:ATTR` names, imported from the current directory without writing bytecode."""
    name, _, attr = text.partition(":")
    here = os.getcwd()
    if here not in sys.path:
        sys.path.insert(0, here)
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        module = importlib.import_module(name)
    except Exception as err:
        raise SpecError(f"cannot import {name}: {type(err).__name__}: {err}") from err
    finally:
        sys.dont_write_bytecode = writes
    if attr:
        if not hasattr(module, attr):
            raise SpecError(f"{name} has no {attr}")
        value = getattr(module, attr)
    else:
        found = [n for n in ("SPEC", "SPECS") if hasattr(module, n)]
        if len(found) != 1:
            raise SpecError(f"{name} must have exactly one of SPEC and SPECS, or name one as {name}:ATTR")
        value = getattr(module, found[0])
    specs = [value] if isinstance(value, Spec) else list(value) if isinstance(value, list | tuple) else None
    if not specs or not all(isinstance(spec, Spec) for spec in specs):
        raise SpecError(f"{text} is not a Spec or a nonempty list of them")
    if any(spec.project != specs[0].project for spec in specs):
        raise SpecError(f"the specs of {text} do not share one project")
    return specs


def _run_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=pathlib.Path, help="the root of the foundry project that holds the targets")
    parser.add_argument("--workspace", type=pathlib.Path, help="the run's workspace (default: build/<spec or run>)")
    parser.add_argument("--results", type=pathlib.Path, help="where to write the record (default: in the workspace)")
    parser.add_argument("--no-link", action="store_true", help="do not run Halmos")
    parser.add_argument("--emit", type=pathlib.Path, help="a directory to write the models and their link tests to")


def _run(specs: list[Spec], args, parser: argparse.ArgumentParser) -> int:
    if args.root is not None:
        project = dataclasses.replace(specs[0].project, root=args.root.resolve())
        specs = [dataclasses.replace(spec, project=project) for spec in specs]
    root = specs[0].project.root
    if root is None or not root.is_dir():
        parser.error(f"the project root {root} is not a directory: pass it with --root")
    name = specs[0].name if len(specs) == 1 else "run"
    workspace = Workspace(args.workspace or pathlib.Path("build") / name)
    record = run(specs, workspace, link=not args.no_link, emit=args.emit)
    results = args.results or workspace.path / "results.json"
    results.write_text(json.dumps(record, indent=2) + "\n")
    print(summary(record))
    print(f"record: {results}")
    return 0 if record["ok"] else 1


def _reads(args) -> int:
    root = args.root.resolve()
    project = Project.foundry(root=root, profile=args.profile, copy=tuple(args.copy) if args.copy else None).resolve()
    workspace = Workspace(args.workspace)
    guard = project.guard(workspace)
    overlay = project.overlay(workspace)
    infos = forge.build(overlay, workspace.fresh("out"), workspace.fresh("cache"), project.profile, (root,))
    found = discover(infos, args.target)
    print(json.dumps(as_json(found), indent=2) if args.json else report(found))
    changes = guard.changes()
    for change in changes:
        print(f"FAILED project: {change}", file=sys.stderr)
    return 0 if found.complete and not changes else 1
