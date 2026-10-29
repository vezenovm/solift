import re

import pytest

from z3sol import ops
from z3sol.ir import BOOL, UINT256, Const, Var, uint
from z3sol.ops import PROOFS, TABLE


def test_each_row_that_can_revert_or_leave_the_lemma_domain_has_a_halmos_proof():
    for operator in TABLE.values():
        if operator.reverts or operator.outside or operator.kind == "library":
            assert operator.proofs, operator.name


def test_each_named_proof_exists_and_each_proof_is_used():
    named = [name for operator in TABLE.values() for name in operator.proofs]
    assert sorted(set(named)) == sorted(PROOFS)
    for name, proof in PROOFS.items():
        assert re.search(rf"function {name}\(", proof.check), name
        assert proof.wrapper or name.startswith("check_lemma_"), name


def test_arithmetic_is_at_uint256():
    narrow = Var("x", uint(128))
    wide = Var("y")
    assert ops.make("add", narrow, wide).type == UINT256
    with pytest.raises(TypeError, match="add at uint128"):
        ops.make("add", narrow, narrow)
    with pytest.raises(TypeError, match="mul at uint128"):
        ops.make("mul", narrow, Const(2))
    with pytest.raises(TypeError, match="two literals"):
        ops.make("add", Const(1), Const(2))


def test_library_calls_and_comparisons_take_any_unsigned_width():
    narrow = Var("x", uint(128))
    assert ops.make("min", narrow, Var("y")).type == UINT256
    assert ops.make("lt", narrow, narrow).type == BOOL
    assert ops.make("eq", Var("p", BOOL), Var("q", BOOL)).type == BOOL
    with pytest.raises(TypeError, match="unsigned operands"):
        ops.make("lt", Var("p", BOOL), Var("y"))


def test_a_ternary_of_two_literals_has_the_type_solc_gives_it():
    flag = Var("flag", BOOL)
    assert ops.make("ite", flag, Const(200), Const(100)).type == uint(8)
    assert ops.make("ite", flag, Const(256), Const(0)).type == uint(16)
    assert ops.make("ite", flag, Var("x", uint(128)), Var("y")).type == UINT256


def test_an_operator_outside_the_table_is_refused():
    with pytest.raises(TypeError, match="mod is not in the operator table"):
        ops.make("mod", Var("x"), Var("y"))
