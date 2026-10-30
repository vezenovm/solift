import pytest
import z3

from z3sol import solvers

x, y = z3.Ints("x y")
b = z3.Bool("b")


def test_z3_answers_first_with_the_values_of_the_shown_terms():
    answer = solvers.check([x > 2, y == x * x, y < 20, b == (x > 3)], {"x": x, "y": y, "b": b, "sum": x + y})
    assert answer.verdict == "sat" and answer.solver == "z3"
    assert answer.values == {"x": 3, "y": 9, "b": False, "sum": 12} or answer.values["y"] == 16


@pytest.mark.parametrize("solver", solvers.SOLVERS)
def test_each_solver_of_the_portfolio_answers_alone(monkeypatch, solver):
    monkeypatch.setattr(solvers, "SOLVERS", (solver,))
    unsat = z3.Solver()
    unsat.add(x >= 0, y >= 0, x < 2**128, y < 2**128, z3.Not(x * y / (y + 1) <= x))
    assert solvers._portfolio(unsat.to_smt2(), {}, 20_000).verdict == "unsat"
    sat = z3.Solver()
    sat.add(x >= 0, x * x == 49, b == (x > 3))
    answer = solvers._portfolio(sat.to_smt2(), {"x": x, "b": b, "less": x - 10}, 20_000)
    assert (answer.verdict, answer.solver) == ("sat", solver)
    assert answer.values == {"x": 7, "b": True, "less": -3}


def test_the_portfolio_runs_after_z3_gives_unknown(monkeypatch):
    monkeypatch.setattr(solvers, "FIRST_MS", 1)
    hard = [x > 1, y > 1, x < 2**64, y < 2**64, x * y == 2**61 - 1]
    answer = solvers.check(hard, timeout_ms=4_000)
    assert answer.answers["z3 (first)"].startswith("unknown")
    assert answer.verdict in ("unsat", "unknown")
    assert set(answer.answers) == {"z3 (first)", *solvers.SOLVERS}
