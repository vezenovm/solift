import pytest
import z3

from solift import solvers

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


@pytest.mark.parametrize("solver", solvers.SOLVERS)
def test_a_shown_term_that_no_assertion_names_still_gets_its_value(monkeypatch, solver):
    monkeypatch.setattr(solvers, "SOLVERS", (solver,))
    s = z3.Solver()
    s.add(x > 1)
    answer = solvers._portfolio(s.to_smt2(), {"x": x, "b": b, "y": y}, 20_000)
    assert answer.verdict == "sat" and set(answer.values) == {"x", "b", "y"}, answer


@pytest.mark.parametrize("solver", solvers.SOLVERS)
def test_a_name_that_smt_lib_reserves_reaches_each_solver_of_the_portfolio(monkeypatch, solver):
    """z3 writes a constant named `as`, `let` or `div` as it is, and the SMT-LIB parsers of the portfolio refuse those
    names. The portfolio gets the query with names of its own."""

    class Unknown(z3.Solver):
        def check(self, *args):
            return z3.unknown

        def reason_unknown(self):
            return "forced"

    monkeypatch.setattr(solvers.z3, "Solver", Unknown)
    monkeypatch.setattr(solvers, "SOLVERS", (solver,))
    a, let, div = z3.Int("as"), z3.Int("let"), z3.Bool("div")
    show = {"as": a, "let": let, "div": div, "sum": a + let}
    answer = solvers.check([a > 2, let == a * a, let < 20, div == (a > 3)], show, timeout_ms=20_000)
    assert (answer.verdict, answer.solver) == ("sat", solver), answer.answers
    assert answer.values["let"] == answer.values["as"] ** 2 < 20 and answer.values["as"] > 2
    assert answer.values["div"] == (answer.values["as"] > 3)
    assert answer.values["sum"] == answer.values["as"] + answer.values["let"]
