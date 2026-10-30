import random

import pytest
import z3

from tests.models import WAD, quote, quote_reference
from z3sol import ops
from z3sol.ir import ADDRESS, BOOL, UINT256, Assign, Call, Const, Function, If, Let, Model, Return, Var, enum
from z3sol.smt import domain, symbols, terms

EDGES = [0, 1, 2, WAD, 2**64 - 1, 2**128 - 1, 2**128, 2**255, 2**256 - 1]


def evaluate(model: Model, values: dict) -> tuple[bool, int | bool, bool]:
    """(reverts, value, outside) of the model on concrete inputs."""
    inputs = {v.name: z3.BoolVal(values[v.name]) if v.type == BOOL else z3.IntVal(values[v.name]) for v in model.inputs}
    t = terms(model, inputs)
    reverts, outside, value = (z3.simplify(x) for x in (t.reverts, t.outside, t.value))
    assert z3.is_bool(reverts) and (z3.is_true(reverts) or z3.is_false(reverts))
    return z3.is_true(reverts), (z3.is_true(value) if z3.is_bool(value) else value.as_long()), z3.is_true(outside)


def function_model(body, params=(), returns=UINT256, callees=()) -> Model:
    return Model("C", Function("f", tuple(params), returns, tuple(body)), callees=tuple(callees))


def test_the_quote_terms_give_the_reference_result_on_edge_and_random_inputs():
    model = quote()
    rng = random.Random(0)
    outcomes = set()
    for _ in range(400):
        values = {}
        for v in model.inputs:
            values[v.name] = min(rng.choice(EDGES + [rng.getrandbits(rng.randint(1, 80))] * 4), v.type.bound - 1)
        expected = quote_reference(**values)
        reverts, value, _ = evaluate(model, values)
        assert reverts == (expected is None), values
        if not reverts:
            assert value == expected, values
        outcomes.add("reverts" if reverts else "pays" if value else "zero")
    assert outcomes == {"pays", "zero", "reverts"}


def test_the_quote_is_outside_where_a_mulDiv_operand_reaches_half_width():
    base = dict(_gas=1, _price=1, margin=0, cap=0, floor=0, pot=0)
    assert not evaluate(quote(), {**base, "txGasPrice": 2**128 - 1, "baseFee": 2**128})[2]
    assert evaluate(quote(), {**base, "txGasPrice": 2**128, "baseFee": 2**128})[2]
    assert evaluate(quote(), {**base, "_price": 2**128, "txGasPrice": 1, "baseFee": 0})[2]
    # The first return skips the mulDiv.
    assert not evaluate(quote(), {**base, "_gas": 0, "_price": 2**128, "txGasPrice": 1, "baseFee": 0})[2]


def test_the_domain_bounds_each_input_by_its_type():
    model = quote()
    x = symbols(model)
    s = z3.Solver()
    s.add(domain(model, x), x["margin"] == 2**128)
    assert s.check() == z3.unsat
    s = z3.Solver()
    s.add(domain(model, x), x["pot"] == 2**256 - 1, x["margin"] == 2**128 - 1)
    assert s.check() == z3.sat


def test_a_statement_after_a_return_does_not_run():
    a = Var("a", input=True)
    model = function_model(
        [If(ops.make("eq", a, Const(0)), (Return(Const(7)),)), Return(ops.make("sub", a, Const(1)))], [a]
    )
    assert evaluate(model, {"a": 0}) == (False, 7, False)
    assert evaluate(model, {"a": 5}) == (False, 4, False)


def test_a_function_that_ends_without_a_return_returns_zero():
    a = Var("a", input=True)
    model = function_model([If(ops.make("gt", a, Const(3)), (Return(a),))], [a])
    assert evaluate(model, {"a": 9}) == (False, 9, False)
    assert evaluate(model, {"a": 2}) == (False, 0, False)
    flag = Var("flag", BOOL, input=True)
    model = function_model([If(flag, (Return(Const(True, BOOL)),))], [flag], returns=BOOL)
    assert evaluate(model, {"flag": False}) == (False, False, False)


def test_a_branch_reverts_only_where_it_runs():
    a, b = Var("a", input=True), Var("b", input=True)
    model = function_model(
        [
            If(
                ops.make("lt", a, b),
                (Return(ops.make("sub", b, a)),),
                (Let(Var("d"), ops.make("sub", a, b)), Return(Var("d"))),
            )
        ],
        [a, b],
    )
    assert evaluate(model, {"a": 1, "b": 5}) == (False, 4, False)
    assert evaluate(model, {"a": 5, "b": 1}) == (False, 4, False)


def test_a_branch_that_does_not_return_still_reverts_where_it_runs():
    a = Var("a", input=True)
    model = function_model(
        [If(ops.make("gt", a, Const(3)), (Let(Var("x"), ops.make("sub", Const(2), a)),)), Return(a)], [a]
    )
    assert evaluate(model, {"a": 2}) == (False, 2, False)
    assert evaluate(model, {"a": 4})[0]


def test_and_or_and_ite_skip_the_operand_that_does_not_run():
    a = Var("a", input=True)
    underflows = ops.make("eq", ops.make("sub", a, Const(1)), Const(0))
    is_zero = ops.make("eq", a, Const(0))
    for op, a_value, reverts in [("or", 0, False), ("and", 0, False), ("or", 5, False), ("and", 5, False)]:
        guard = is_zero if op == "or" else ops.make("ne", a, Const(0))
        model = function_model([Return(ops.make(op, guard, underflows))], [a], returns=BOOL)
        assert evaluate(model, {"a": a_value})[0] == reverts
    model = function_model([Return(ops.make("ite", is_zero, Const(0), ops.make("sub", a, Const(1))))], [a])
    assert evaluate(model, {"a": 0}) == (False, 0, False)
    model = function_model([Return(ops.make("ite", is_zero, ops.make("sub", a, Const(1)), Const(0)))], [a])
    assert evaluate(model, {"a": 0})[0]


def test_an_internal_call_runs_the_callee_with_its_early_returns_and_reverts():
    x, a = Var("x"), Var("a", input=True)
    callee = Function(
        "g",
        (x,),
        UINT256,
        (If(ops.make("eq", x, Const(0)), (Return(Const(100)),)), Return(ops.make("sub", x, Const(10)))),
    )
    call = Call("g", (a,), UINT256)
    model = function_model([Return(ops.make("add", call, Const(1)))], [a], callees=[callee])
    assert evaluate(model, {"a": 0}) == (False, 101, False)
    assert evaluate(model, {"a": 15}) == (False, 6, False)
    assert evaluate(model, {"a": 5})[0]


def test_a_function_that_calls_itself_is_refused():
    x = Var("x")
    callee = Function("g", (x,), UINT256, (Return(Call("g", (x,), UINT256)),))
    model = function_model([Return(Call("g", (Const(1),), UINT256))], callees=[callee])
    with pytest.raises(ValueError, match="g calls itself"):
        terms(model, {})


def test_the_terms_expose_each_local_of_the_modeled_function():
    model = quote()
    t = terms(model, symbols(model))
    assert set(t.locals) == {"gasPrice", "cost", "target"}


def test_a_local_assigned_in_a_branch_takes_the_value_of_the_branch_that_ran():
    """x = a; if (a > 10) { x += 1; if (a > 20) return x; x *= 2; } else { x = 0; } return x + 100;"""
    a, x = Var("a", input=True), Var("x")
    inner = If(ops.make("gt", a, Const(20)), (Return(x),))
    body = [
        Let(x, a),
        If(
            ops.make("gt", a, Const(10)),
            (Assign(x, ops.make("add", x, Const(1))), inner, Assign(x, ops.make("mul", x, Const(2)))),
            (Assign(x, Const(0)),),
        ),
        Return(ops.make("add", x, Const(100))),
    ]
    model = function_model(body, [a])

    def reference(v):
        if v > 10:
            if v + 1 > 0 and v > 20:
                return v + 1
            return (v + 1) * 2 + 100
        return 100

    for v in (0, 10, 11, 15, 20, 21, 1000):
        assert evaluate(model, {"a": v}) == (False, reference(v), False), v
    t = terms(model, symbols(model))
    assert set(t.locals) == {"x"} and set(t.declared) == {"x"}
    for v in (5, 15, 25):
        final = z3.simplify(z3.substitute(t.locals["x"], (symbols(model)["a"], z3.IntVal(v))))
        assert final.as_long() == {5: 0, 15: 32, 25: 26}[v]
    assert evaluate(model, {"a": 2**256 - 1})[0]


def test_a_named_return_starts_at_zero_and_is_returned_at_the_end():
    a, r = Var("a", input=True), Var("r")
    body = [
        If(ops.make("gt", a, Const(3)), (Assign(r, ops.make("add", a, Const(1))),)),
        If(ops.make("eq", a, Const(9)), (Return(r),)),
    ]
    model = Model("C", Function("f", (a,), UINT256, tuple(body), result=r))
    assert evaluate(model, {"a": 2}) == (False, 0, False)
    assert evaluate(model, {"a": 5}) == (False, 6, False)
    assert evaluate(model, {"a": 9}) == (False, 10, False)


def test_an_address_and_an_enum_are_integers_in_their_range():
    kind = Var("kind", enum("K", 3), input=True)
    who = Var("who", ADDRESS, input=True)
    body = [Return(ops.make("and", ops.make("eq", kind, Const(1, enum("K", 3), text="K.B")), ops.make("ne", who, who)))]
    model = function_model(body, [kind, who], returns=BOOL)
    x = symbols(model)
    s = z3.Solver()
    s.add(domain(model, x), z3.Or(x["kind"] == 3, x["who"] == 2**160))
    assert s.check() == z3.unsat
    with pytest.raises(TypeError, match="lt takes unsigned operands, not enum, enum"):
        ops.make("lt", kind, kind)
    with pytest.raises(TypeError, match="add takes unsigned operands, not address, literal"):
        ops.make("add", who, Const(1))


def test_a_local_with_the_name_of_a_visible_variable_is_refused():
    x, y, inner = Var("x"), Var("y"), Var("x")
    g = Function("g", (x, y), UINT256, (If(ops.make("eq", y, Const(1)), (Let(inner, Const(1)),)), Return(x)))
    a = Var("a", input=True)
    model = Model("C", Function("f", (a,), UINT256, (Return(Call("g", (a, a), UINT256)),)), callees=(g,))
    with pytest.raises(ValueError, match="local x shadows a variable of the same name"):
        terms(model, symbols(model))
