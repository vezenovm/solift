"""The acceptance run against Oxide:

    Z3SOL_FORGE=aztec-forge python -m acceptance.oxide --root <oxide>/l1-contracts

1. One run of every spec (specs.py): the models of DepositSubsidy._quoteSubsidy, DepositSubsidy.quoteSubsidy,
   DepositSubsidy._pricedGas and PlainWithdrawalSubsidy.quoteSubsidy, with the theorems of prove.py on them, and of
   Caps.getCurrentAvailable, with its own theorems; their vectors; and their Halmos links against the bytecode built
   from Oxide's sources, with Oxide's harness or, for Caps, the harness in harness/.
2. z3 proves each model of a function of formulas/subsidies.py equal to Oxide's formula of it.
3. Each mutant fails on the theorem it breaks.

The run fails when any of these fails, and when Oxide changed during the run. It prints the time of each part.
"""

import argparse
import dataclasses
import json
import pathlib
import sys
import time

from acceptance.oxide import mutants, reference
from acceptance.oxide.specs import SPECS
from z3sol.run import load, run, summary
from z3sol.workspace import Workspace


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="The acceptance run against Oxide.")
    parser.add_argument("--root", type=pathlib.Path, required=True, help="Oxide's l1-contracts directory")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path("build") / "oxide")
    parser.add_argument("--results", type=pathlib.Path, help="where to write the record (default: in the workspace)")
    parser.add_argument("--no-mutants", action="store_true", help="do not run the mutants")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    project = dataclasses.replace(SPECS[0].project, root=root)
    specs = [dataclasses.replace(spec, project=project) for spec in SPECS]
    workspace = Workspace(args.workspace)
    guard = project.guard(workspace)
    failures = []
    times = {}

    start = time.time()
    record = run(specs, workspace.child("specs"))
    times["specs"] = time.time() - start
    print(summary(record))
    failures += [f"specs: {f}" for f in record["failures"]]

    print("\n== each model against Oxide's formula of the same function")
    start = time.time()
    equal = {}
    try:
        models = load(specs, workspace.child("equivalence"))
    except Exception as err:  # noqa: BLE001 - any failure to load fails this part, with its message
        models = {}
        failures.append(f"equivalence: {err}")
    for spec in specs:
        if spec.name not in models or spec.name not in reference.REFERENCES:
            continue
        ref = reference.REFERENCES[spec.name]
        equal[spec.name] = reference.equivalence(root, models[spec.name], ref)
        for name, verdict in equal[spec.name]["obligations"].items():
            print(f"{verdict} {spec.name}: the {name} is the same as {ref.formula}() for every input in the domain")
        failures += [f"{spec.name}: {f}" for f in equal[spec.name]["failures"]]
        for failure in equal[spec.name]["failures"]:
            print(f"FAILED {failure}")
    times["equivalence"] = time.time() - start

    mutant_records = {}
    if not args.no_mutants:
        print("\n== mutants")
        start = time.time()
        by_name = {spec.name: spec for spec in specs}
        for mutant in mutants.MUTANTS:
            source = (root / mutant.path).read_text()
            m = run(
                by_name[mutant.spec],
                workspace.child(f"mutant-{mutant.id}"),
                replace={mutant.path: mutants.apply(source, mutant)},
                link=mutant.link,
                vectors=False,
                only=(mutant.breaks, *mutant.holds),
            )
            mutant_records[mutant.id] = m
            problems = mutants.problems(mutant, m)
            failures += [f"mutant {mutant.id}: {p}" for p in problems]
            status = "REJECTED" if not problems else "NOT REJECTED"
            print(f"{status} {mutant.id}: {mutant.breaks} fails" + (", and its link passes" if mutant.link else ""))
            for problem in problems:
                print(f"FAILED {problem}")
        times["mutants"] = time.time() - start

    failures += [f"oxide: {change}" for change in guard.changes()]
    result = {
        "ok": not failures,
        "failures": failures,
        "times": times,
        "specs": record,
        "equivalence": equal,
        "mutants": mutant_records,
    }
    path = args.results or workspace.path / "acceptance.json"
    path.write_text(json.dumps(result, indent=2) + "\n")
    print("\n" + ", ".join(f"{name} {seconds:.0f}s" for name, seconds in times.items()))
    print(f"{'ok' if not failures else f'{len(failures)} failure(s)'}; record: {path}")
    return 0 if not failures else 1


sys.exit(main())
