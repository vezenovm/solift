"""Theorems on a model, proved with z3 on its integer terms.

A theorem is a function of the model's terms (`Terms`) that returns the goal. It names what it assumes apart from what
it proves:

- the domain: each input in the range of its type. Every theorem assumes it.
- `assume`: "no-revert" (the model does not revert) and "inside" (no mulDiv operand reaches 2**128, where the lemma is
  not proved). A theorem that must prove these, such as liveness, puts them in its goal instead.
- `given`: a function of the terms that gives more hypotheses. It must not mention the model's reverts or outside:
  those go in `assume`, so that each result lists them.

A theorem expected to prove needs a `witness`: a condition that, together with the hypotheses, z3 must satisfy, so a
proof from hypotheses that never hold fails. A theorem can instead expect a counterexample, with the `issue` of the
known gap or the `reason` it is expected. A proof then fails the run, so a fix that closes the gap updates the theorem.

Every result other than the expected one fails the run, and so does z3's "unknown".
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import z3

from z3sol.ir import Model
from z3sol.smt import domain, symbols, terms

ASSUMPTIONS = {"no-revert": "the model does not revert", "inside": "no mulDiv operand reaches 2**128"}
DEFAULT_TIMEOUT_MS = 300_000


class TheoremError(Exception):
    pass


class Terms:
    """A model's terms, by their Solidity names: `result`, `reverts`, `outside`, each input and each local of the
    modeled function. `free(name)` adds a variable that the theorem quantifies over, such as `gasToCover`."""

    def __init__(self, model: Model):
        self.model = model
        self.inputs = symbols(model)
        t = terms(model, self.inputs)
        self.result, self.reverts, self.outside, self.locals = t.value, t.reverts, t.outside, t.locals
        self.domain = domain(model, self.inputs)
        self.free_variables: dict[str, z3.ExprRef] = {}

    def __getattr__(self, name: str) -> z3.ExprRef:
        for names in (self.inputs, self.locals, self.free_variables):
            if name in names:
                return names[name]
        raise AttributeError(f"{self.model.name} has no input, local or free variable {name}")

    def __getitem__(self, name: str) -> z3.ExprRef:
        return self.__getattr__(name)

    def free(self, name: str, kind: str = "int") -> z3.ExprRef:
        if name in self.inputs or name in self.locals:
            raise TheoremError(f"{name} is already an input or a local of {self.model.name}")
        if name not in self.free_variables:
            self.free_variables[name] = z3.Bool(name) if kind == "bool" else z3.Int(name)
        return self.free_variables[name]


@dataclass(frozen=True)
class Theorem:
    id: str
    statement: str
    goal: Callable
    assume: tuple[str, ...] = ()
    given: Callable | None = None
    witness: Callable | None = None
    expect: str = "proved"
    issue: str | None = None
    reason: str | None = None
    show: tuple[str, ...] = ()
    timeout_ms: int = DEFAULT_TIMEOUT_MS

    def __post_init__(self):
        unknown = set(self.assume) - set(ASSUMPTIONS)
        if unknown:
            raise TheoremError(f"{self.id}: assume takes {', '.join(ASSUMPTIONS)}, not {', '.join(sorted(unknown))}")
        if self.expect not in ("proved", "counterexample"):
            raise TheoremError(f"{self.id}: expect is proved or counterexample, not {self.expect}")
        if self.expect == "proved" and self.witness is None:
            raise TheoremError(f"{self.id}: a theorem expected to prove needs a witness that its hypotheses hold")
        if self.expect == "counterexample" and not (self.issue or self.reason):
            raise TheoremError(f"{self.id}: an expected counterexample needs the issue of its gap or a reason")


def theorem(**options) -> Callable[[Callable], Theorem]:
    """Declares a theorem: its id is the function name with dashes, its statement the docstring."""

    def wrap(goal: Callable) -> Theorem:
        statement = " ".join((goal.__doc__ or "").split())
        if not statement:
            raise TheoremError(f"{goal.__name__}: the docstring states the theorem")
        return Theorem(goal.__name__.replace("_", "-"), statement, goal, **options)

    return wrap


@dataclass
class Result:
    id: str
    statement: str
    verdict: str
    expected: str
    ok: bool
    assumed: list[str]
    witness: str | None = None
    counterexample: dict = field(default_factory=dict)
    time: float = 0.0
    issue: str | None = None
    reason: str | None = None
    detail: str = ""


def _mentions(term: z3.ExprRef, sub: z3.ExprRef) -> bool:
    if z3.is_false(sub) or z3.is_true(sub):
        return False
    seen, stack = set(), [term]
    while stack:
        t = stack.pop()
        if t.get_id() in seen:
            continue
        seen.add(t.get_id())
        if t.eq(sub):
            return True
        stack.extend(t.children())
    return False


def _check(hypotheses, goal, timeout_ms: int):
    s = z3.Solver()
    s.set("timeout", timeout_ms)
    s.add(*hypotheses)
    if goal is not None:
        s.add(z3.Not(goal))
    start = time.time()
    r = s.check()
    return r, s, time.time() - start


def prove(t: Theorem, q: Terms) -> Result:
    hypotheses = [q.domain]
    assumed = ["each input lies in the range of its type"]
    if "no-revert" in t.assume:
        hypotheses.append(z3.Not(q.reverts))
        assumed.append(ASSUMPTIONS["no-revert"])
    if "inside" in t.assume:
        hypotheses.append(z3.Not(q.outside))
        assumed.append(ASSUMPTIONS["inside"])
    if t.given is not None:
        given = t.given(q)
        for name, term in (("reverts", q.reverts), ("outside", q.outside)):
            if _mentions(given, term):
                raise TheoremError(f"{t.id}: given mentions the model's {name}; state it with assume")
        hypotheses.append(given)
        doc = " ".join((t.given.__doc__ or "").split())
        assumed.append(f"{t.given.__name__}: {doc}" if doc else t.given.__name__)
    result = Result(t.id, t.statement, "", t.expect, False, assumed, issue=t.issue, reason=t.reason)
    r, s, result.time = _check(hypotheses, t.goal(q), t.timeout_ms)
    if r == z3.unsat:
        result.verdict = "PROVED"
    elif r == z3.sat:
        result.verdict = "COUNTEREXAMPLE"
        m = s.model()
        for name in t.show:
            value = m.eval(q[name], model_completion=True)
            result.counterexample[name] = z3.is_true(value) if value.sort() == z3.BoolSort() else value.as_long()
    else:
        result.verdict = "UNKNOWN"
        result.detail = s.reason_unknown()
    if t.witness is not None:
        w, s, _ = _check(hypotheses + [t.witness(q)], None, t.timeout_ms)
        result.witness = "WITNESS" if w == z3.sat else "VACUOUS" if w == z3.unsat else "UNKNOWN"
    expected = "PROVED" if t.expect == "proved" else "COUNTEREXAMPLE"
    result.ok = result.verdict == expected and result.witness in (None, "WITNESS")
    return result
