"""z3 proves each extracted model equal to Oxide's hand-written formula of the same function: the same value, the same
revert condition and the same outside, for every input in the model's domain.

Oxide's formulas/subsidies.py builds each formula on Oxide's own dsl.py. This repo imports no Oxide code: a separate
Python process runs subsidies.py from the Oxide checkout, with -B so that it writes no bytecode there, and prints the
three terms as SMT-LIB. Each reference maps the formula's inputs to terms of the model: most are inputs of the same
meaning, and some are a function of the model's inputs, such as the token kind of depositPricedGas, which the contract
computes from the token's address.
"""

import hashlib
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

import z3

from z3sol import solvers
from z3sol.ir import Const, walk
from z3sol.theorem import Terms

FORMULAS = "proofs/subsidies/formulas"
EXPORT = """
import sys

import z3
import subsidies

f = getattr(subsidies, sys.argv[1])()
x = {p.name: z3.Bool(p.name) if p.kind == "bool" else z3.Int(p.name) for p in f.params}
value, reverts, outside, _ = f.model(x)
s = z3.Solver()
s.add((z3.Bool if z3.is_bool(value) else z3.Int)("reference_value") == value)
s.add(z3.Bool("reference_reverts") == reverts)
s.add(z3.Bool("reference_outside") == outside)
print(",".join(p.name for p in f.params))
print(s.to_smt2())
"""


@dataclass(frozen=True)
class Reference:
    """`formula` names the function of subsidies.py. `inputs` maps the model's terms to a term for each input of the
    formula."""

    formula: str
    inputs: Callable[[Terms], dict]


def constant(q: Terms, name: str) -> int:
    """The value of a named constant of the model, such as USDC."""
    for function in (q.model.function, *q.model.callees):
        for e in walk(function):
            if isinstance(e, Const) and e.name == name:
                return e.value
    raise KeyError(f"{q.model.name} has no constant {name}")


def _quote_inputs(q: Terms) -> dict:
    price = q._tokenPerEth if "_tokenPerEth" in q.inputs else q.tokenPerEth
    same = ("targetRelayerProfit", "maxSubsidyPerOperation", "pot", "txGasPrice", "baseFee")
    return {"fee": q._fee, "reimbursableGas": q._reimbursableGas, "tokenPerEth": price, **{n: q[n] for n in same}}


def _priced_gas_inputs(q: Terms) -> dict:
    """tokenKind is 0 for TOKEN, 1 for USDC, 2 for USDT and 3 for any other token, in the order the contract compares
    them. relayerHoldsNoToken is a relayer balance of zero."""
    usdc, usdt = constant(q, "USDC"), constant(q, "USDT")
    kind = z3.If(q._token == q.token, 0, z3.If(q._token == usdc, 1, z3.If(q._token == usdt, 2, 3)))
    same = ("deployed", "relayerTransaction", "executorStartedEmpty", "frameGas", "calldataGas", "executorHeadGas")
    held = {"relayerHoldsNoToken": q.relayerBalance == 0}
    return {"intent": q._intent, "tokenKind": kind, **held, **{n: q[n] for n in same}}


def _withdrawal_inputs(q: Terms) -> dict:
    same = ("startPriceWei", "maxSubsidy", "tokenPerEth", "pot", "txGasPrice", "baseFee")
    return {"flow": q._flow, **{n: q[n] for n in same}}


REFERENCES = {
    "deposit-quote": Reference("deposit_quote", _quote_inputs),
    "deposit-public-quote": Reference("deposit_quote", _quote_inputs),
    "deposit-priced-gas": Reference("deposit_priced_gas", _priced_gas_inputs),
    "withdrawal-quote": Reference("withdrawal_quote", _withdrawal_inputs),
}


def equivalence(root: pathlib.Path, q: Terms, reference: Reference, timeout_ms: int = 300_000) -> dict:
    formulas = root / FORMULAS
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    result = subprocess.run(
        [sys.executable, "-B", "-c", EXPORT, reference.formula],
        cwd=formulas,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    record = {
        "formula": reference.formula,
        "reference": {name: _sha256(formulas / name) for name in ("dsl.py", "subsidies.py")},
        "obligations": {},
        "failures": [],
    }
    if result.returncode:
        record["failures"].append(f"{reference.formula}() did not export: {result.stderr.strip()}")
        return record
    params, _, smt2 = result.stdout.partition("\n")
    names = params.split(",")
    inputs = reference.inputs(q)
    if sorted(names) != sorted(inputs):
        record["failures"].append(f"{reference.formula}() has the inputs {names}, the reference maps {sorted(inputs)}")
        return record
    definitions = z3.parse_smt2_string(smt2)
    pairs = []
    for name, term in inputs.items():
        variable = z3.Bool(name) if z3.is_bool(term) else z3.Int(name)
        pairs.append((variable, term if z3.is_expr(term) else z3.IntVal(term)))
    definitions = [z3.substitute(a, *pairs) for a in definitions]
    value = (z3.Bool if z3.is_bool(q.result) else z3.Int)("reference_value")
    obligations = {
        "value": value == q.result,
        "reverts": z3.Bool("reference_reverts") == q.reverts,
        "outside": z3.Bool("reference_outside") == q.outside,
    }
    for name, equal in obligations.items():
        answer = solvers.check([q.domain, *definitions, z3.Not(equal)], timeout_ms=timeout_ms)
        verdict = {"unsat": "PROVED", "sat": "COUNTEREXAMPLE"}.get(answer.verdict, "UNKNOWN")
        record["obligations"][name] = verdict
        if verdict != "PROVED":
            record["failures"].append(
                f"equivalence: the {name} of the model and {reference.formula}() is not equal: {verdict}"
            )
    return record


def _sha256(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
