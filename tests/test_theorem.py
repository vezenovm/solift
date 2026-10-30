import pytest
import z3

from acceptance.oxide.deposit_subsidy import SPEC
from tests.models import quote
from z3sol import ops, solvers
from z3sol.ir import UINT256, Assign, Call, Const, Function, Let, Model, Return, Var
from z3sol.theorem import Terms, TheoremError, prove, theorem


@pytest.fixture(scope="module")
def q():
    return Terms(quote())


def results(q) -> dict:
    return {t.id: prove(t, q) for t in SPEC.theorems}


def test_the_deposit_theorems_of_prove_py_give_its_results_on_the_hand_built_model(q):
    found = results(q)
    assert {i: (r.verdict, r.witness) for i, r in found.items()} == {
        "deposit-no-farming": ("PROVED", "WITNESS"),
        "deposit-liveness": ("PROVED", "WITNESS"),
        "deposit-liveness-without-margin": ("COUNTEREXAMPLE", None),
        "deposit-liveness-without-margin-short-by-one": ("PROVED", "WITNESS"),
    }
    assert all(r.ok for r in found.values())
    assert set(found["deposit-liveness-without-margin"].counterexample) == {
        "_fee",
        "_reimbursableGas",
        "gasToCover",
        "txGasPrice",
        "_tokenPerEth",
    }


def test_each_result_lists_what_its_theorem_assumed(q):
    found = results(q)
    assert found["deposit-no-farming"].assumed == [
        "each input lies in the range of its type",
        "the model does not revert",
        "no mulDiv operand reaches 2**128",
    ]
    assert found["deposit-liveness"].assumed[0] == "each input lies in the range of its type"
    assert found["deposit-liveness"].assumed[1].startswith("envelope_with_margin: inside the envelope")
    assert len(found["deposit-liveness"].assumed) == 2


def test_a_theorem_expected_to_prove_needs_a_witness():
    with pytest.raises(TheoremError, match="needs a witness"):

        @theorem()
        def no_witness(q):
            """a statement"""
            return q.result >= 0


def test_an_expected_counterexample_needs_an_issue_or_a_reason():
    with pytest.raises(TheoremError, match="needs the issue of its gap or a reason"):

        @theorem(expect="counterexample")
        def no_issue(q):
            """a statement"""
            return q.result >= 0

    @theorem(expect="counterexample", issue="OX-2234")
    def with_issue(q):
        """a statement"""
        return q.result >= 0


def test_a_theorem_states_itself_and_assumes_only_the_named_facts():
    with pytest.raises(TheoremError, match="the docstring states the theorem"):

        @theorem(witness=lambda q: True)
        def undocumented(q):
            return q.result >= 0

    with pytest.raises(TheoremError, match="assume takes no-revert, inside, not no-overflow"):

        @theorem(assume=("no-overflow",), witness=lambda q: True)
        def unknown_assumption(q):
            """a statement"""
            return q.result >= 0


def test_a_hypothesis_that_mentions_reverts_is_refused(q):
    def hides_reverts(q):
        return z3.Not(q.reverts)

    @theorem(given=hides_reverts, witness=lambda q: q.result > 0)
    def no_revert_in_given(q):
        """the quote is at most the pot"""
        return q.result <= q.pot

    with pytest.raises(TheoremError, match="given mentions the model's reverts; state it with assume"):
        prove(no_revert_in_given, q)


@pytest.mark.parametrize(
    "given, name",
    [
        (lambda q: z3.Not(z3.simplify(q.reverts)), "reverts"),
        (lambda q: z3.Not(q["outside"]), "outside"),
        (lambda q: z3.Not(q.at(_fee=0).reverts), "at"),
    ],
)
def test_given_cannot_reach_the_reverts_or_the_outside_in_any_form(q, given, name):
    @theorem(given=given, witness=lambda q: q.result > 0)
    def never_reverts(q):
        """the quote does not revert"""
        return z3.Not(q.reverts)

    with pytest.raises(TheoremError, match=f"given mentions the model's {name}; state it with assume"):
        prove(never_reverts, q)


def test_a_proof_from_hypotheses_that_never_hold_fails(q):
    def impossible(q):
        """a fee below zero"""
        return q._fee < 0

    @theorem(given=impossible, witness=lambda q: q.result > 0)
    def vacuous(q):
        """the quote is at most the pot"""
        return q.result <= q.pot

    result = prove(vacuous, q)
    assert (result.verdict, result.witness, result.ok) == ("PROVED", "VACUOUS", False)


def test_an_expected_counterexample_that_proves_fails(q):
    @theorem(assume=("no-revert",), expect="counterexample", reason="a gap")
    def closed_gap(q):
        """the quote is at most the pot"""
        return q.result <= q.pot

    result = prove(closed_gap, q)
    assert (result.verdict, result.ok) == ("PROVED", False)


def test_unknown_fails(q):
    @theorem(witness=lambda q: True, timeout_ms=1)
    def hard(q):
        """no cube of a positive input is the sum of two other cubes"""
        a, b, c = q.free("a"), q.free("b"), q.free("c")
        return z3.Or(a <= 0, b <= 0, c <= 0, a * a * a + b * b * b != c * c * c + q._fee * q.pot * q.txGasPrice)

    result = prove(hard, q)
    assert (result.verdict, result.ok) == ("UNKNOWN", False)


def test_a_theorem_can_run_a_called_function_name_a_declared_value_and_fix_inputs():
    a, b, x, p = Var("a", input=True), Var("b", input=True), Var("x"), Var("p")
    double = Function("double", (p,), UINT256, (Return(ops.make("mul", p, Const(2))),))
    body = (
        Let(x, Call("double", (a,), UINT256)),
        Assign(x, ops.make("add", x, b)),
        Return(x),
    )
    q = Terms(Model("C", Function("f", (a, b), UINT256, body), callees=(double,)))
    assert z3.simplify(q.call("double", 21).value).as_long() == 42
    assert z3.simplify(z3.substitute(q.declared("x"), (q.a, z3.IntVal(5)))).as_long() == 10
    fixed = q.at(a=5, b=1)
    assert z3.simplify(fixed.value).as_long() == 11 and z3.simplify(fixed.locals["x"]).as_long() == 11
    assert z3.is_false(z3.simplify(fixed.reverts))
    with pytest.raises(AttributeError, match="has no called function f"):
        q.call("f", 1, 2)
    with pytest.raises(AttributeError, match="declares no local y"):
        q.declared("y")
    with pytest.raises(AttributeError, match="has no input c"):
        q.at(c=1)


def test_a_counterexample_without_the_values_it_shows_fails(q, monkeypatch):
    @theorem(expect="counterexample", reason="the quote can pay", show=("_fee", "pot"))
    def pays_nothing(q):
        """the quote pays nothing"""
        return q.result == 0

    assert prove(pays_nothing, q).ok
    monkeypatch.setattr(solvers, "check", lambda *args, **kwargs: solvers.Answer("sat", "cvc5", 0.0, {"_fee": 1}))
    result = prove(pays_nothing, q)
    assert not result.ok and result.detail.startswith("the solver gave no value for pot")


def test_given_indexes_an_input_as_terms_do_even_when_it_is_named_like_an_attribute():
    """f(uint256 result) returns result - 1: in given, q["result"] is the input, and q.result the output."""
    result = Var("result", input=True)
    q = Terms(Model("C", Function("f", (result,), UINT256, (Return(ops.make("sub", result, Const(1))),))))

    @theorem(given=lambda q: q["result"] > 0, witness=lambda q: q["result"] == 2)
    def positive(q):
        """the output is positive for a positive input"""
        return q.result > 0

    assert prove(positive, q).verdict == "COUNTEREXAMPLE"


@pytest.mark.parametrize(
    "given",
    [lambda h: z3.Not(z3.simplify(Terms(h.model).reverts)), lambda h: z3.Not(h._q.reverts)],
)
def test_given_holds_no_way_back_to_the_model_or_its_terms(q, given):
    @theorem(given=given, witness=lambda q: q.result > 0)
    def never_reverts(q):
        """the quote does not revert"""
        return z3.Not(q.reverts)

    with pytest.raises((TheoremError, AttributeError)):
        prove(never_reverts, q)
