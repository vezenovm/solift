"""Theorems on a model, proved on its integer terms with the solver portfolio (solvers.py).

A theorem is a function of the model's terms (`Terms`) that returns the goal. It names what it assumes apart from what
it proves:

- the domain: each input in the range of its type. Every theorem assumes it.
- `assume`: "no-revert" (the model does not revert) and "inside" (no mulDiv operand reaches 2**128, where the lemma is
  not proved). A theorem that must prove these, such as liveness, puts them in its goal instead.
- `given`: a function of the terms that gives more hypotheses. It must not mention the model's reverts or outside:
  those go in `assume`, so that each result lists them. It sees the terms without `reverts`, `outside` and `at`, and
  a called function gives it only its value. This keeps a theorem from using them by mistake. It is not a sandbox: a
  theorem file is Python, which can reach any object, so it is reviewed like other code.

A theorem expected to prove needs a `witness`: a condition that, together with the hypotheses, z3 must satisfy, so a
proof from hypotheses that never hold fails. A theorem can instead expect a counterexample, with the `issue` of the
known gap or the `reason` it is expected. A proof then fails the run, so a fix that closes the gap updates the theorem.

Every result other than the expected one fails the run, and so does "unknown" from every solver.

Each result says how far it reaches. A counterexample and a witness give a value for every input and every free
variable. `lemma_domain` says whether the proof rests on the mulDiv lemma outside the domain where Halmos proves it:
"not-used" when no evaluation that the proof makes uses mulDiv, "established" when the hypotheses keep each such
evaluation inside the domain, with each argument in the range of its Solidity type, and "not-established" or
"unknown" otherwise. `callees` names the functions that the theorem runs on its own arguments with `call`: the link
proves such a function equal to the bytecode only where the modeled function calls it, so a theorem on it is a theorem
of the integer model.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass, field

import z3

from solift import solvers
from solift.ir import BOOL, Call, Function, Model, Op, walk
from solift.ops import TABLE
from solift.smt import Terms as ModelTerms
from solift.smt import domain, symbols, terms, terms_of_call

ASSUMPTIONS = {"no-revert": "the model does not revert", "inside": "no mulDiv operand reaches 2**128"}
DEFAULT_TIMEOUT_MS = 300_000
# The kinds of a free variable: an unbounded integer, a bool, or an unsigned Solidity type, which bounds it.
KINDS = {"int": None, "bool": None, "address": 2**160, **{f"uint{n}": 2**n for n in range(8, 257, 8)}}


class TheoremError(Exception):
    pass


@dataclass(frozen=True)
class Evaluation:
    """One run of the model or of one of its functions that a theorem makes: the model itself (`what` is "model"), the
    model with inputs replaced ("at"), or a called function ("call <name>"). `lemma` is whether the function uses an
    operator whose lemma is proved only on a domain, such as mulDiv. `obligation` holds where the run leaves that
    domain: its outside, or an argument outside the range of its Solidity type. `witness` marks a run that only the
    witness makes."""

    what: str
    lemma: bool
    obligation: z3.BoolRef
    witness: bool


class Terms:
    """A model's terms, by their Solidity names: `result`, `reverts`, `outside`, each input, and each local of the
    modeled function at the return that runs. `declared(name)` is a local's value where it is declared, before any
    assignment. `free(name, kind)` adds a variable that the theorem quantifies over, such as `shares`. `call(name,
    *args)` runs a function of the model on terms, and `at(**values)` gives the terms with inputs replaced, as for one
    configuration. Each theorem proves on its own `scoped()` view, which records its free variables and the runs of the
    model that it makes (`evaluations`)."""

    def __init__(self, model: Model):
        self.model = model
        self.inputs = symbols(model)
        t = terms(model, self.inputs)
        self._value, self._reverts, self._outside, self._locals = t.value, t.reverts, t.outside, t.locals
        self._declared, self._anywhere = t.declared, t.anywhere
        self._lemma = _uses_lemma(model, model.function)
        self.domain = domain(model, self.inputs)
        self.free_variables: dict[str, z3.ExprRef] = {}
        self.kinds: dict[str, str] = {}
        self.evaluations: list[Evaluation] = []
        self.witnessing = False

    def scoped(self) -> Terms:
        """The same terms, with no free variables and no evaluations yet: the view of one theorem."""
        view = object.__new__(Terms)
        view.__dict__.update(self.__dict__)
        view.free_variables, view.kinds, view.evaluations, view.witnessing = {}, {}, [], False
        return view

    def _evaluated(self, what: str, lemma: bool, obligation: z3.BoolRef) -> None:
        once = what in ("model", "model locals")
        if not (once and any(e.what == what and e.witness == self.witnessing for e in self.evaluations)):
            self.evaluations.append(Evaluation(what, lemma, obligation, self.witnessing))

    def _model(self) -> None:
        self._evaluated("model", self._lemma, self._outside)

    def _model_locals(self) -> None:
        """A local's value is a term also on the paths where its statement does not run, so a theorem that reads one
        rests on the lemma wherever the function's operations leave its domain."""
        self._evaluated("model locals", self._lemma, self._anywhere)

    @property
    def result(self) -> z3.ExprRef:
        self._model()
        return self._value

    @property
    def reverts(self) -> z3.BoolRef:
        self._model()
        return self._reverts

    @property
    def outside(self) -> z3.BoolRef:
        self._model()
        return self._outside

    @property
    def locals(self) -> dict[str, z3.ExprRef]:
        return _Locals(self._locals, self._model_locals)

    def __getattr__(self, name: str) -> z3.ExprRef:
        names = self.__dict__
        if name.startswith("__") or "inputs" not in names:
            raise AttributeError(name)
        if name in names["inputs"]:
            return names["inputs"][name]
        if name in names["_locals"]:
            self._model_locals()
            return names["_locals"][name]
        if name in names["free_variables"]:
            return names["free_variables"][name]
        raise AttributeError(f"{self.model.name} has no input, local or free variable {name}")

    def __getitem__(self, name: str) -> z3.ExprRef:
        return self.__getattr__(name)

    def free(self, name: str, kind: str | None = None) -> z3.ExprRef:
        """A variable named by an identifier, like the inputs, so that a counterexample names it as the theorem does.
        Its kind is "int" (the default, unbounded), "bool", "address" or an unsigned type such as "uint256", whose range
        the theorem then assumes. A later call with no kind gives the same variable."""
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise TheoremError(f"{name} is not an identifier")
        if name in self.inputs or name in self._locals:
            raise TheoremError(f"{name} is already an input or a local of {self.model.name}")
        if kind is not None and kind not in KINDS:
            raise TheoremError(f"the free variable {name} has the kind {kind}, not one of int, bool, address, uint<n>")
        if name in self.free_variables:
            if kind is not None and kind != self.kinds[name]:
                raise TheoremError(f"{name} is a free {self.kinds[name]}, not a {kind}")
            return self.free_variables[name]
        self.kinds[name] = kind or "int"
        self.free_variables[name] = z3.Bool(name) if kind == "bool" else z3.Int(name)
        return self.free_variables[name]

    def ranges(self) -> list[z3.BoolRef]:
        """The range of each free variable of a bounded kind."""
        bounds = [(self.free_variables[name], KINDS[kind]) for name, kind in self.kinds.items() if KINDS[kind]]
        return [z3.And(var >= 0, var < bound) for var, bound in bounds]

    def declared(self, name: str) -> z3.ExprRef:
        if name not in self._declared:
            raise AttributeError(f"{self.model.name} declares no local {name}")
        self._model_locals()
        return self._declared[name]

    def call(self, name: str, *args) -> ModelTerms:
        """The value, reverts and outside of the model's function `name` on the argument terms."""
        functions = {f.name: f for f in (self.model.function, *self.model.callees)}
        if name not in functions or functions[name] is self.model.function:
            raise AttributeError(f"{self.model.name} has no called function {name}")
        function = functions[name]
        called = terms_of_call(self.model, function, self.inputs, args)
        params = [(p, a if z3.is_expr(a) else z3.IntVal(a)) for p, a in zip(function.params, args, strict=True)]
        outside = [z3.Not(_in_range(p.type.bound, a)) for p, a in params if p.type != BOOL]
        self._evaluated(f"call {name}", _uses_lemma(self.model, function), z3.Or(called.outside, *outside))
        return called

    def at(self, **values) -> ModelTerms:
        """The value, reverts, outside and locals with the named inputs replaced by the given terms or numbers."""
        pairs, outside = [], []
        for name, value in values.items():
            if name not in self.inputs:
                raise AttributeError(f"{self.model.name} has no input {name}")
            term = self.inputs[name]
            pairs.append((term, value if z3.is_expr(value) else _const(term, value)))
            var = next(v for v in self.model.inputs if v.name == name)
            if var.type != BOOL:
                outside.append(z3.Not(_in_range(var.type.bound, pairs[-1][1])))

        def fixed(term: z3.ExprRef) -> z3.ExprRef:
            return z3.substitute(term, *pairs) if pairs else term

        value, reverts, out, anywhere = (fixed(x) for x in (self._value, self._reverts, self._outside, self._anywhere))
        values = {k: fixed(v) for k, v in self._locals.items()}
        locals_ = _Locals(values, lambda: self._evaluated("at locals", self._lemma, z3.Or(anywhere, *outside)))
        self._evaluated("at", self._lemma, z3.Or(out, *outside))
        return ModelTerms(value, reverts, out, locals_)


class _Locals(dict):
    """The locals of a run of the model, which record the run's obligation when the theorem reads one."""

    def __init__(self, values: dict, record: Callable[[], None]):
        super().__init__(values)
        self._record = record

    def __getitem__(self, name):
        self._record()
        return super().__getitem__(name)

    def get(self, name, default=None):
        self._record()
        return super().get(name, default)

    def values(self):
        self._record()
        return super().values()

    def items(self):
        self._record()
        return super().items()


def _uses_lemma(model: Model, function: Function) -> bool:
    """Whether the function, or a function it calls, uses an operator whose lemma is proved only on a domain."""
    functions = {f.name: f for f in model.callees}
    seen, stack = set(), [function]
    while stack:
        f = stack.pop()
        if f.name in seen:
            continue
        seen.add(f.name)
        for e in walk(f):
            if isinstance(e, Op) and TABLE[e.op].outside is not None:
                return True
            if isinstance(e, Call):
                stack.append(functions[e.function])
    return False


def _in_range(bound: int, term: z3.ExprRef) -> z3.BoolRef:
    return z3.And(term >= 0, term < bound)


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
    solver: str = ""
    answers: dict = field(default_factory=dict)
    witness_values: dict = field(default_factory=dict)
    lemma_domain: str = "not-used"
    callees: list[str] = field(default_factory=list)
    link: str | None = None
    query_sha256: str = ""


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


class _Hypotheses:
    """The terms as `given` sees them: the result, the inputs, the locals, `declared`, `free`, and a called function's
    value. None of its attributes or items is the model's reverts or outside, or the model or Terms. `h[name]` is an
    input, a local or a free variable, as for Terms, and `h.name` too unless the view has an attribute of that name."""

    HIDDEN = ("reverts", "outside", "at", "model")

    def __init__(self, q: Terms):
        self.inputs = dict(q.inputs)
        self.domain = q.domain
        self.free_variables = q.free_variables
        self.free = lambda name, kind=None: q.free(name, kind)
        self.declared = lambda name: q.declared(name)
        self.call = lambda function, *args: _Value(q.call(function, *args).value)
        self._result = lambda: q.result
        self._locals = lambda: q.locals
        self._term = lambda name: q[name]

    @property
    def result(self) -> z3.ExprRef:
        return self._result()

    @property
    def locals(self) -> dict[str, z3.ExprRef]:
        return self._locals()

    def __getattr__(self, name: str):
        if name in self.HIDDEN:
            raise TheoremError(f"given mentions the model's {name}; state it with assume")
        return self[name]

    def __getitem__(self, name: str):
        if name in self.HIDDEN and name not in self.inputs:
            raise TheoremError(f"given mentions the model's {name}; state it with assume")
        try:
            return self._term(name)
        except AttributeError:
            raise AttributeError(f"given has no input, local or free variable {name}") from None


@dataclass(frozen=True)
class _Value:
    value: z3.ExprRef


def _const(term: z3.ExprRef, value) -> z3.ExprRef:
    return z3.BoolVal(bool(value)) if term.sort() == z3.BoolSort() else z3.IntVal(value)


def prove(t: Theorem, q: Terms) -> Result:
    q = q.scoped()
    hypotheses = [q.domain]
    assumed = ["each input lies in the range of its type"]
    if "no-revert" in t.assume:
        hypotheses.append(z3.Not(q.reverts))
        assumed.append(ASSUMPTIONS["no-revert"])
    if "inside" in t.assume:
        hypotheses.append(z3.Not(q.outside))
        assumed.append(ASSUMPTIONS["inside"])
    if t.given is not None:
        given = t.given(_Hypotheses(q))
        for name, term in (("reverts", q._reverts), ("outside", q._outside)):
            if _mentions(given, term):
                raise TheoremError(f"{t.id}: given mentions the model's {name}; state it with assume")
        hypotheses.append(given)
        doc = " ".join((t.given.__doc__ or "").split())
        assumed.append(f"{t.given.__name__}: {doc}" if doc else t.given.__name__)
    goal = t.goal(q)
    proof = list(q.evaluations)
    q.witnessing = True
    witness = t.witness(q) if t.witness is not None else None
    q.witnessing = False
    if q.ranges():
        hypotheses += q.ranges()
        bounded = ", ".join(f"{name}: {kind}" for name, kind in q.kinds.items() if KINDS[kind])
        assumed.append(f"each free variable lies in the range of its kind ({bounded})")
    values = {**q.inputs, **q.free_variables}
    shown = {**values, **{name: q[name] for name in t.show}}
    result = Result(t.id, t.statement, "", t.expect, False, assumed, issue=t.issue, reason=t.reason)
    query = z3.Solver()
    query.add(*hypotheses, z3.Not(goal))
    result.query_sha256 = hashlib.sha256(query.to_smt2().encode()).hexdigest()
    answer = solvers.check(hypotheses + [z3.Not(goal)], shown, t.timeout_ms)
    result.time, result.solver, result.answers = answer.time, answer.solver, answer.answers
    result.verdict = {"unsat": "PROVED", "sat": "COUNTEREXAMPLE"}.get(answer.verdict, "UNKNOWN")
    result.counterexample = answer.values
    result.detail = answer.detail
    if witness is not None:
        w = solvers.check(hypotheses + [witness], values, timeout_ms=t.timeout_ms)
        result.witness = {"sat": "WITNESS", "unsat": "VACUOUS"}.get(w.verdict, "UNKNOWN")
        result.witness_values = w.values
    result.lemma_domain = _lemma_domain(hypotheses, proof, t.timeout_ms)
    result.callees = sorted({e.what.removeprefix("call ") for e in proof if e.what.startswith("call ")})
    missing = [name for name in shown if name not in result.counterexample]
    if result.verdict == "COUNTEREXAMPLE" and missing:
        result.detail = f"the solver gave no value for {', '.join(missing)}. {result.detail}".strip()
    expected = "PROVED" if t.expect == "proved" else "COUNTEREXAMPLE"
    result.ok = result.verdict == expected and result.witness in (None, "WITNESS")
    result.ok = result.ok and not (result.verdict == "COUNTEREXAMPLE" and missing)
    return result


def _lemma_domain(hypotheses: list, proof: list[Evaluation], timeout_ms: int) -> str:
    """Whether the hypotheses keep each run of the proof inside the domain of the lemmas that it uses."""
    obligations = [e.obligation for e in proof if e.lemma]
    if not obligations:
        return "not-used"
    answer = solvers.check(hypotheses + [z3.Or(*obligations)], timeout_ms=timeout_ms)
    return {"unsat": "established", "sat": "not-established"}.get(answer.verdict, "unknown")
