"""The solver portfolio for theorems and other queries on the integer terms.

z3 runs first, in this process, for at most FIRST_MS. Most queries end there. On "unknown", z3, yices and cvc5 run in
parallel, each in its own process, on the same query as SMT-LIB, until the first one answers sat or unsat or the
timeout ends. The other solvers then stop. Each answer records the solver that gave it.

yices runs its MCSAT solver for QF_NIA, the logic of these queries: integers and booleans, with products and division
of variables. cvc5 has no binary in its Python package, so a Python process runs its SMT-LIB parser.
"""

from __future__ import annotations

import importlib.metadata
import os
import pathlib
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field

import z3

FIRST_MS = 10_000
SOLVERS = ("z3", "yices", "cvc5")
CVC5 = r"""
import sys

import cvc5

tm = cvc5.TermManager()
solver = cvc5.Solver(tm)
solver.setOption("tlimit", sys.argv[1])
symbols = cvc5.SymbolManager(tm)
parser = cvc5.InputParser(solver, symbols)
parser.setStringInput(cvc5.InputLanguage.SMT_LIB_2_6, sys.stdin.read(), "query")
while True:
    command = parser.nextCommand()
    if command.isNull():
        break
    sys.stdout.write(command.invoke(solver, symbols))
    sys.stdout.flush()
"""


@dataclass
class Answer:
    """`verdict` is sat, unsat or unknown. `values` holds the value of each shown term for sat, and `answers` what
    each solver that ran gave."""

    verdict: str
    solver: str
    time: float
    values: dict[str, int | bool] = field(default_factory=dict)
    detail: str = ""
    answers: dict[str, str] = field(default_factory=dict)


def check(assertions: list, show: dict[str, z3.ExprRef] | None = None, timeout_ms: int = 300_000) -> Answer:
    """Whether the assertions are satisfiable, and for sat the values of the `show` terms."""
    show = show or {}
    start = time.time()
    s = z3.Solver()
    s.set("timeout", min(FIRST_MS, timeout_ms))
    s.add(*assertions)
    r = s.check()
    if r != z3.unknown:
        answer = Answer(str(r), "z3", time.time() - start, answers={"z3": str(r)})
        if r == z3.sat:
            model = s.model()
            answer.values = {name: _value(model.eval(term, model_completion=True)) for name, term in show.items()}
        return answer
    first = s.reason_unknown()
    if timeout_ms <= FIRST_MS:
        return Answer("unknown", "z3", time.time() - start, detail=first, answers={"z3": f"unknown ({first})"})
    answer = _portfolio(s.to_smt2(), show, timeout_ms)
    answer.time = time.time() - start
    answer.answers = {"z3 (first)": f"unknown ({first})", **answer.answers}
    return answer


def versions() -> dict[str, str]:
    return {
        "z3-solver": importlib.metadata.version("z3-solver"),
        "yices-solver": importlib.metadata.version("yices-solver"),
        "cvc5": importlib.metadata.version("cvc5"),
    }


def _value(v: z3.ExprRef) -> int | bool:
    return z3.is_true(v) if v.sort() == z3.BoolSort() else v.as_long()


def _binary(name: str) -> str:
    """A solver binary next to this Python, as pip installs it, or on PATH."""
    local = pathlib.Path(sys.executable).parent / name
    found = str(local) if local.exists() else shutil.which(name)
    if found is None:
        raise FileNotFoundError(f"no {name} next to {sys.executable} or on PATH")
    return found


def _command(solver: str, timeout_ms: int) -> tuple[list[str], str]:
    """The command of a solver and the logic line its query starts with."""
    if solver == "z3":
        return [_binary("z3"), "-in", "-smt2", f"-t:{timeout_ms}"], ""
    if solver == "yices":
        return [_binary("yices-smt2"), f"--timeout={max(1, timeout_ms // 1000)}"], "(set-logic QF_NIA)\n"
    return [sys.executable, "-c", CVC5, str(timeout_ms)], "(set-logic QF_NIA)\n"


def _portfolio(smt2: str, show: dict[str, z3.ExprRef], timeout_ms: int) -> Answer:
    values = "(get-value (" + " ".join(term.sexpr() for term in show.values()) + "))\n" if show else ""
    query = smt2.replace("(check-sat)", "(check-sat)\n" + values)
    running = {}
    for solver in SOLVERS:
        command, logic = _command(solver, timeout_ms)
        p = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        p.stdin.write("(set-option :produce-models true)\n" + logic + query)
        p.stdin.close()
        running[solver] = p
    answers: dict[str, str] = {}
    deadline = time.time() + timeout_ms / 1000 + 2
    found = None
    while running and found is None and time.time() < deadline:
        for solver, p in list(running.items()):
            if p.poll() is None:
                continue
            del running[solver]
            out = p.stdout.read()
            first = out.split("\n", 1)[0].strip()
            if first in ("sat", "unsat", "unknown"):
                answers[solver] = first
            else:
                answers[solver] = f"error: {(out + p.stderr.read()).strip()}"
            if first in ("sat", "unsat"):
                found = (solver, first, out)
                break
        time.sleep(0.05)
    for solver, p in running.items():
        p.kill()
        p.wait()
        answers.setdefault(solver, "stopped")
    if found is None:
        return Answer("unknown", "portfolio", 0.0, detail="no solver answered sat or unsat", answers=answers)
    solver, verdict, out = found
    answer = Answer(verdict, solver, 0.0, answers=answers)
    if verdict == "sat" and show:
        parsed = _sexpr(out.split("\n", 1)[1])
        if not parsed or len(parsed[0]) != len(show):
            answer.detail = f"{solver} gave no values: {out.strip()}"
        else:
            answer.values = {name: _atom(pair[1]) for name, pair in zip(show, parsed[0], strict=True)}
    return answer


def _sexpr(text: str) -> list:
    """The s-expressions of an SMT-LIB response, as nested lists of strings."""
    stack: list[list] = [[]]
    token = ""
    for ch in text:
        if ch in "() \n\t":
            if token:
                stack[-1].append(token)
                token = ""
            if ch == "(":
                stack.append([])
            elif ch == ")" and len(stack) > 1:
                done = stack.pop()
                stack[-1].append(done)
        else:
            token += ch
    if token:
        stack[-1].append(token)
    return stack[0]


def _atom(value) -> int | bool:
    """An integer or boolean value, as `5`, `(- 5)`, `true` or `false`."""
    if value in ("true", "false"):
        return value == "true"
    if isinstance(value, list) and len(value) == 2 and value[0] == "-":
        return -int(value[1])
    return int(value)
