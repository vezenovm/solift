from tests.models import QUOTE_SOURCE, quote
from z3sol import ops
from z3sol.ir import UINT256, Call, Const, Function, If, Model, Return, Var
from z3sol.solidity import names, source


def test_the_quote_model_emits_the_contract_operators_in_source_order():
    assert source(quote()) == QUOTE_SOURCE


def test_a_callee_is_its_own_function_and_takes_the_inputs():
    x, a = Var("x"), Var("a", input=True)
    callee = Function("_g", (x,), UINT256, (If(ops.make("eq", x, Const(0)), (Return(Const(1)),), (Return(x),)),))
    model = Model(
        "C", Function("f", (a,), UINT256, (Return(Call("_g", (a,), UINT256)),)), callees=(callee,), pragma="0.8.30"
    )
    text = source(model)
    assert "    return _g(i, i.a);\n" in text
    assert "  function _g(Inputs memory i, uint256 x) internal pure returns (uint256) {\n" in text
    assert "    if (x == 0) {\n      return 1;\n    } else {\n      return x;\n    }\n" in text
    assert names(model) == ("CFModel", "CFHost")
