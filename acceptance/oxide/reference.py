"""z3 proves the extracted model equal to Oxide's hand-written deposit_quote(): the same value, the same revert
condition and the same outside, for every input in the model's domain.

Oxide's formulas/subsidies.py builds deposit_quote() on Oxide's own dsl.py. This repo imports no Oxide code: a separate
Python process runs subsidies.py from the Oxide checkout, with -B so that it writes no bytecode there, and prints the
three terms as SMT-LIB.
"""

import hashlib
import os
import pathlib
import subprocess
import sys

import z3

from z3sol.theorem import Terms

FORMULAS = "proofs/subsidies/formulas"
EXPORT = """
import z3
from subsidies import deposit_quote

f = deposit_quote()
x = {p.name: z3.Int(p.name) for p in f.params}
value, reverts, outside, _ = f.model(x)
s = z3.Solver()
s.add(z3.Int("reference_value") == value)
s.add(z3.Bool("reference_reverts") == reverts)
s.add(z3.Bool("reference_outside") == outside)
print(",".join(p.name for p in f.params))
print(s.to_smt2())
"""
# deposit_quote() names three inputs after the parameters without their underscore.
RENAMED = {"fee": "_fee", "reimbursableGas": "_reimbursableGas", "tokenPerEth": "_tokenPerEth"}


def equivalence(root: pathlib.Path, q: Terms, timeout_ms: int = 300_000) -> dict:
    formulas = root / FORMULAS
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-B", "-c", EXPORT], cwd=formulas, env=env, capture_output=True, text=True, check=False
    )
    record = {
        "reference": {name: _sha256(formulas / name) for name in ("dsl.py", "subsidies.py")},
        "obligations": {},
        "failures": [],
    }
    if result.returncode:
        record["failures"].append(f"deposit_quote() did not export: {result.stderr.strip()}")
        return record
    params, _, smt2 = result.stdout.partition("\n")
    names = params.split(",")
    model_names = [RENAMED.get(name, name) for name in names]
    if sorted(model_names) != sorted(q.inputs):
        record["failures"].append(f"deposit_quote() has the inputs {names}, the model {sorted(q.inputs)}")
        return record
    pairs = [(z3.Int(name), q.inputs[RENAMED[name]]) for name in names if name in RENAMED]
    definitions = [z3.substitute(a, *pairs) for a in z3.parse_smt2_string(smt2)]
    obligations = {
        "value": z3.Int("reference_value") == q.result,
        "reverts": z3.Bool("reference_reverts") == q.reverts,
        "outside": z3.Bool("reference_outside") == q.outside,
    }
    for name, equal in obligations.items():
        s = z3.Solver()
        s.set("timeout", timeout_ms)
        s.add(q.domain, *definitions, z3.Not(equal))
        r = s.check()
        verdict = "PROVED" if r == z3.unsat else "COUNTEREXAMPLE" if r == z3.sat else "UNKNOWN"
        record["obligations"][name] = verdict
        if verdict != "PROVED":
            record["failures"].append(
                f"equivalence: the {name} of the model and deposit_quote() is not equal: {verdict}"
            )
    return record


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
