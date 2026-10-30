import pytest
import z3

from examples.quoter.spec import SPEC
from solift import ops, solvers
from solift.ir import UINT256, Assign, Call, Const, Function, If, Let, Model, Return, Var
from solift.theorem import Terms, TheoremError, prove, theorem
from tests.models import quote


@pytest.fixture(scope="module")
def q():
    return Terms(quote())


def no_margin_no_floor(q):
    """no margin and no floor"""
    return z3.And(q.margin == 0, q.floor == 0)


@theorem(assume=("no-revert",), given=no_margin_no_floor, witness=lambda q: q.result > 0)
def capped_without_a_margin(q):
    """without a margin or a floor, the quote is at most the cap"""
    return q.result <= q.cap


def results(q) -> dict:
    return {t.id: prove(t, q) for t in [*SPEC.theorems, capped_without_a_margin]}


def test_the_quoter_theorems_give_their_results_on_the_hand_built_model(q):
    found = results(q)
    assert {i: (r.verdict, r.witness) for i, r in found.items()} == {
        "quote-at-most-gas-plus-margin": ("PROVED", "WITNESS"),
        "quote-at-most-gas": ("COUNTEREXAMPLE", None),
        "capped-without-a-margin": ("PROVED", "WITNESS"),
    }
    assert all(r.ok for r in found.values())
    inputs = {"_gas", "_price", "margin", "cap", "floor", "pot", "txGasPrice", "baseFee"}
    assert set(found["quote-at-most-gas"].counterexample) == inputs
    assert set(found["quote-at-most-gas-plus-margin"].witness_values) == inputs


def test_each_result_lists_what_its_theorem_assumed(q):
    found = results(q)
    assert found["quote-at-most-gas-plus-margin"].assumed == [
        "each input lies in the range of its type",
        "the model does not revert",
        "no mulDiv operand reaches 2**128",
    ]
    assert found["capped-without-a-margin"].assumed == [
        "each input lies in the range of its type",
        "the model does not revert",
        "no_margin_no_floor: no margin and no floor",
    ]


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

    @theorem(expect="counterexample", issue="#12")
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
        (lambda q: z3.Not(q.at(_gas=0).reverts), "at"),
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
        """a gas below zero"""
        return q._gas < 0

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
        return z3.Or(a <= 0, b <= 0, c <= 0, a * a * a + b * b * b != c * c * c + q._gas * q.pot * q.txGasPrice)

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
    @theorem(expect="counterexample", reason="the quote can pay", show=("_gas", "pot"))
    def pays_nothing(q):
        """the quote pays nothing"""
        return q.result == 0

    assert prove(pays_nothing, q).ok
    monkeypatch.setattr(solvers, "check", lambda *args, **kwargs: solvers.Answer("sat", "cvc5", 0.0, {"_gas": 1}))
    result = prove(pays_nothing, q)
    assert not result.ok
    assert result.detail.startswith("the solver gave no value for _price, margin, cap, floor, pot, txGasPrice, baseFee")


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


def test_a_free_variable_is_named_by_an_identifier(q):
    with pytest.raises(TheoremError, match="gas limit is not an identifier"):
        q.free("gas limit")
    assert str(q.free("gasLimit")) == "gasLimit"


def test_a_free_variable_of_an_unsigned_kind_lies_in_its_range_and_keeps_its_kind(q):
    @theorem(witness=lambda q: q.free("x") > 0)
    def bounded(q):
        """a uint8 is below 256"""
        return q.free("x", "uint8") < 256

    @theorem(witness=lambda q: q.free("x") > 0)
    def unbounded(q):
        """an integer is below 256"""
        return q.free("x") < 256

    assert prove(bounded, q).verdict == "PROVED"
    assert prove(unbounded, q).verdict == "COUNTEREXAMPLE"
    view = q.scoped()
    view.free("y", "uint256")
    assert view.free("y") is view.free("y", "uint256")
    with pytest.raises(TheoremError, match="y is a free uint256, not a int"):
        view.free("y", "int")
    with pytest.raises(TheoremError, match="has the kind uint7"):
        view.free("z", "uint7")
    assert "y" not in q.scoped().free_variables


def test_the_lemma_domain_is_established_by_inside_or_by_hypotheses_that_imply_it(q):
    def small(q):
        """the gas, the gas price and the price are small"""
        return z3.And(q._gas < 2**64, q.txGasPrice < 2**64, q._price < 2**128)

    @theorem(assume=("no-revert",), witness=lambda q: q.result > 0)
    def capped(q):
        """the quote is at most the cap"""
        return q.result <= q.cap

    @theorem(assume=("no-revert",), given=small, witness=lambda q: q.result > 0)
    def capped_when_small(q):
        """the quote is at most the cap"""
        return q.result <= q.cap

    @theorem(witness=lambda q: q._gas > 0)
    def only_inputs(q):
        """the gas is not negative"""
        return q._gas >= 0

    found = {t.id: prove(t, q) for t in (*SPEC.theorems, capped, capped_when_small, only_inputs)}
    assert {i: r.lemma_domain for i, r in found.items()} == {
        "quote-at-most-gas-plus-margin": "established",
        "quote-at-most-gas": "established",
        "capped": "not-established",
        "capped-when-small": "established",
        "only-inputs": "not-used",
    }


def test_a_theorem_on_a_callee_names_it_and_its_arguments_count_for_the_lemma_domain():
    a, x = Var("a", input=True), Var("x")
    halve = Function("halve", (x,), UINT256, (Return(ops.make("mulDiv", x, Const(1), Const(2))),))
    q = Terms(Model("C", Function("f", (a,), UINT256, (Return(Call("halve", (a,), UINT256)),)), callees=(halve,)))

    @theorem(witness=lambda q: q.free("y") > 0)
    def halves(q):
        """where it does not revert, halve(y) * 2 <= y"""
        call = q.call("halve", q.free("y"))
        return z3.Or(call.reverts, call.value * 2 <= q.free("y"))

    @theorem(witness=lambda q: q.free("y") > 0)
    def halves_a_uint128(q):
        """halve(y) * 2 <= y for a uint128 y"""
        call = q.call("halve", q.free("y", "uint128"))
        return z3.Or(call.reverts, call.value * 2 <= q.free("y"))

    @theorem(witness=lambda q: True)
    def halves_minus_one(q):
        """halve(-1) is below 1"""
        return q.call("halve", -1).value < 1

    found = {t.id: prove(t, q) for t in (halves, halves_a_uint128, halves_minus_one)}
    assert {i: (r.lemma_domain, r.callees) for i, r in found.items()} == {
        "halves": ("not-established", ["halve"]),
        "halves-a-uint128": ("established", ["halve"]),
        "halves-minus-one": ("not-established", ["halve"]),
    }


def test_a_local_on_a_path_that_skips_its_mulDiv_does_not_establish_the_lemma_domain():
    """if (a >= 2**128) return 0; uint256 v = Math.mulDiv(a, 1, 2); return v: v is a term also where its statement does
    not run, so a theorem that reads it rests on mulDiv at a >= 2**128."""
    a, v = Var("a", input=True), Var("v")
    body = (
        If(ops.make("ge", a, Const(2**128)), (Return(Const(0)),)),
        Let(v, ops.make("mulDiv", a, Const(1), Const(2))),
        Return(v),
    )
    q = Terms(Model("C", Function("f", (a,), UINT256, body)))
    goals = {
        "result": lambda q: q.result >= 0,
        "declared": lambda q: q.declared("v") >= 0,
        "attribute": lambda q: q.v >= 0,
        "locals": lambda q: q.locals["v"] >= 0,
        "at locals": lambda q: q.at(a=q.free("b")).locals["v"] >= 0,
        "copied locals": lambda q: dict(q.locals)["v"] >= 0,
        "copied at locals": lambda q: {**q.at(a=2**129).locals}["v"] >= 0,
    }
    found = {}
    for name, goal in goals.items():
        goal.__doc__ = f"the {name} is not negative"
        found[name] = prove(theorem(witness=lambda q: True)(goal), q).lemma_domain
    assert found == {
        "result": "established",
        "declared": "not-established",
        "attribute": "not-established",
        "locals": "not-established",
        "at locals": "not-established",
        "copied locals": "not-established",
        "copied at locals": "not-established",
    }


def test_given_reads_the_locals_as_a_mapping(q):
    @theorem(assume=("no-revert",), given=lambda h: h.locals["cost"] >= 0, witness=lambda q: q.result > 0)
    def capped(q):
        """the quote is at most the cap"""
        return q.result <= q.cap

    assert prove(capped, q).verdict == "PROVED"


def test_a_mulDiv_operand_below_zero_on_a_path_that_reverted_is_outside_the_lemma_domain():
    """if (a < 5) return 0; uint256 b = a - 5; uint256 v = Math.mulDiv(b, 1, 2); return v: at a < 5, the term of v
    gives mulDiv the operand a - 5, below zero."""
    a, b, v = Var("a", input=True), Var("b"), Var("v")
    body = (
        If(ops.make("lt", a, Const(5)), (Return(Const(0)),)),
        Let(b, ops.make("sub", a, Const(5))),
        Let(v, ops.make("mulDiv", b, Const(1), Const(2))),
        Return(v),
    )
    q = Terms(Model("C", Function("f", (a,), UINT256, body)))

    @theorem(given=lambda h: h.a < 5, witness=lambda q: True)
    def negative(q):
        """below 5, v is below zero"""
        return q.v < 0

    result = prove(negative, q)
    assert (result.verdict, result.lemma_domain) == ("PROVED", "not-established")


def test_inside_assumes_away_only_mulDiv_operands_at_or_above_half_width():
    """uint256 b = a - 5; return Math.mulDiv(b, 1, 2): `inside` does not hide the revert at a < 5."""
    a, b = Var("a", input=True), Var("b")
    body = (Let(b, ops.make("sub", a, Const(5))), Return(ops.make("mulDiv", b, Const(1), Const(2))))
    q = Terms(Model("C", Function("f", (a,), UINT256, body)))

    @theorem(assume=("inside",), witness=lambda q: q.a == 5)
    def live(q):
        """the quote does not revert"""
        return z3.Not(q.reverts)

    result = prove(live, q)
    assert result.verdict == "COUNTEREXAMPLE" and result.counterexample["a"] < 5
