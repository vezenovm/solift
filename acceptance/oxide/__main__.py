"""The M1 acceptance run against Oxide:

    Z3SOL_FORGE=aztec-forge python -m acceptance.oxide --root <oxide>/l1-contracts

1. The spec of DepositSubsidy._quoteSubsidy: the extracted model, the depositQuote theorems of prove.py, the vectors,
   and the Halmos link against the bytecode built from Oxide's sources, with Oxide's harness.
2. z3 proves the extracted model equal to Oxide's deposit_quote().
3. Each mutant of _quoteSubsidy fails on the theorem it breaks.

The run fails when any of these fails, and when Oxide changed during the run.
"""

import argparse
import dataclasses
import json
import pathlib
import sys

from acceptance.oxide import mutants, reference
from acceptance.oxide.deposit_subsidy import SPEC
from z3sol import forge
from z3sol.extract import extract
from z3sol.run import run, summary
from z3sol.theorem import Terms
from z3sol.workspace import Workspace


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The M1 acceptance run against Oxide.")
    parser.add_argument("--root", type=pathlib.Path, required=True, help="Oxide's l1-contracts directory")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path("build") / "oxide")
    parser.add_argument("--results", type=pathlib.Path, help="where to write the record (default: in the workspace)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    spec = dataclasses.replace(SPEC, project=dataclasses.replace(SPEC.project, root=root))
    workspace = Workspace(args.workspace)
    guard = spec.project.guard(workspace)
    failures = []

    record = run(spec, workspace.child("spec"))
    print(summary(record))
    failures += [f"spec: {f}" for f in record["failures"]]

    print("\n== the extracted model against Oxide's deposit_quote()")
    equal = {"failures": ["no model"]}
    if "model" in record:
        equal = reference.equivalence(root, Terms(_model(spec, workspace.child("equivalence"))))
        for name, verdict in equal["obligations"].items():
            print(f"{verdict} the {name} is the same for every input in the domain")
    failures += equal["failures"]
    for failure in equal["failures"]:
        print(f"FAILED {failure}")

    print("\n== mutants of _quoteSubsidy")
    source = (root / mutants.PATH).read_text()
    mutant_records = {}
    for mutant in mutants.MUTANTS:
        mutated = mutants.apply(source, mutant)
        m = run(
            spec,
            workspace.child(f"mutant-{mutant.id}"),
            replace={mutants.PATH: mutated},
            link=mutant.link,
            vectors=False,
            only=(mutant.breaks, *mutant.holds),
        )
        mutant_records[mutant.id] = m
        problems = _mutant_problems(mutant, m)
        failures += [f"mutant {mutant.id}: {p}" for p in problems]
        status = "REJECTED" if not problems else "NOT REJECTED"
        print(f"{status} {mutant.id}: {mutant.breaks} fails" + (", and its link passes" if mutant.link else ""))
        for problem in problems:
            print(f"FAILED {problem}")

    failures += [f"oxide: {change}" for change in guard.changes()]
    result = {
        "ok": not failures,
        "failures": failures,
        "spec": record,
        "equivalence": equal,
        "mutants": mutant_records,
    }
    path = args.results or workspace.path / "acceptance.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print(f"\n{'ok' if not failures else f'{len(failures)} failure(s)'}; record: {path}")
    return 0 if not failures else 1


def _model(spec, workspace: Workspace):
    root = spec.project.overlay(workspace)
    infos = forge.build(
        root, workspace.fresh("out"), workspace.fresh("cache"), spec.project.profile, (spec.project.root,)
    )
    return extract(infos, spec.target, spec.reads).model


def _mutant_problems(mutant: mutants.Mutant, record: dict) -> list[str]:
    verdicts = {t["id"]: t["verdict"] for t in record.get("theorems", [])}
    problems = [f for f in record["failures"] if f.startswith(("extract:", "project:"))]
    if verdicts.get(mutant.breaks) != "COUNTEREXAMPLE":
        problems.append(f"{mutant.breaks} is {verdicts.get(mutant.breaks)}, not COUNTEREXAMPLE")
    for held in mutant.holds:
        if verdicts.get(held) != "PROVED":
            problems.append(f"{held} is {verdicts.get(held)}, not PROVED")
    if mutant.link:
        link = record.get("link") or {"results": [], "problems": ["no link"]}
        problems += [f"link: {p}" for p in link["problems"]]
        problems += [f"link: {r['check']} {r['verdict']}" for r in link["results"] if r["verdict"] != "PASS"]
    return problems


sys.exit(main())
