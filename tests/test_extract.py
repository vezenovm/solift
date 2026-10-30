import pathlib
import random

import pytest
import z3

from tests.models import QUOTE_SOURCE, WAD, quote_reference
from tests.test_smt import evaluate
from z3sol import forge, ops
from z3sol.extract import ExtractionError, extract
from z3sol.ir import uint
from z3sol.smt import symbols, terms
from z3sol.solidity import source

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUOTER = "tests/fixtures/contracts/Quoter.sol:Quoter"
OUT_OF_SCOPE = "tests/fixtures/contracts/OutOfScope.sol:OutOfScope"
READS = {
    "$config.margin": "margin",
    "$config.cap": "cap",
    "$floor": "floor",
    "TOKEN.balanceOf(address(this))": "pot",
    "tx.gasprice": "txGasPrice",
    "block.basefee": "baseFee",
}


@pytest.fixture(scope="session")
def build(tmp_path_factory):
    work = tmp_path_factory.mktemp("forge")
    return forge.build(ROOT, work / "out", work / "cache")


def test_the_quoter_model_is_the_function_with_its_reads_as_inputs(build):
    extraction = extract(build, f"{QUOTER}._quote", READS)
    model = extraction.model
    assert [(v.name, v.type) for v in model.inputs][2:4] == [("margin", uint(128)), ("cap", uint(128))]
    assert [r.source for r in model.reads] == list(READS)
    assert source(model) == QUOTE_SOURCE
    assert set(extraction.sources) == {
        "tests/fixtures/contracts/Quoter.sol",
        "lib/openzeppelin-contracts/contracts/utils/math/Math.sol",
    }


def test_the_sources_of_an_inherited_function_hold_the_file_that_defines_it(build):
    extraction = extract(build, "tests/fixtures/contracts/Inherited.sol:Inherited._plusOne", {})
    assert set(extraction.sources) == {
        "tests/fixtures/contracts/Inherited.sol",
        "tests/fixtures/contracts/Base.sol",
    }


def test_the_quoter_terms_give_the_reference_result(build):
    model = extract(build, f"{QUOTER}._quote", READS).model
    rng = random.Random(1)
    edges = [0, 1, WAD, 2**128 - 1, 2**128, 2**256 - 1]
    outcomes = set()
    for _ in range(300):
        values = {}
        for v in model.inputs:
            values[v.name] = min(rng.choice(edges + [rng.getrandbits(rng.randint(1, 70))] * 4), v.type.bound - 1)
        expected = quote_reference(**values)
        reverts, value, _ = evaluate(model, values)
        assert reverts == (expected is None), values
        if not reverts:
            assert value == expected, values
        outcomes.add("reverts" if reverts else "pays" if value else "zero")
    assert outcomes == {"pays", "zero", "reverts"}


def test_a_storage_field_read_directly_is_the_same_read_as_through_a_copy(build):
    model = extract(build, f"{QUOTER}._capDirectly", {"$config.cap": "cap"}).model
    assert [(r.var.name, r.var.type, r.source) for r in model.reads] == [("cap", uint(128), "$config.cap")]


def test_a_public_function_calls_its_callee_inline(build):
    model = extract(build, f"{QUOTER}.quote", READS).model
    assert [f.name for f in model.callees] == ["_withMargin", "_quote"]
    t = terms(model, symbols(model))
    assert z3.is_expr(t.value)


@pytest.mark.parametrize(
    "function, reads, message",
    [
        ("loop", {}, r"OutOfScope.sol:12:5: a loop"),
        ("storageWrite", {}, r"OutOfScope.sol:19:5: a write with \+=: its value can revert"),
        ("uncheckedBlock", {}, r"OutOfScope.sol:24:5: an unchecked block"),
        ("signedParameter", {}, r"OutOfScope.sol:29:28: a signed integer"),
        ("cast", {}, r"OutOfScope.sol:34:12: a type conversion: uint128\(_a\)"),
        ("tupleReturn", {}, r"tupleReturn returns 2 values, not one"),
        ("tupleDeclaration", {}, r"OutOfScope.sol:42:5: a tuple declaration"),
        ("exponentiation", {}, r"OutOfScope.sol:47:12: exponentiation"),
        ("assembly_", {}, r"OutOfScope.sol:52:5: inline assembly"),
        ("unmappedRead", {}, r"reads an environment field block.timestamp, which the read map does not name"),
        ("modulo", {}, r"OutOfScope.sol:63:12: mod is not in the operator table"),
        ("narrowArithmetic", {}, r"OutOfScope.sol:67:12: add at uint128: arithmetic narrower than uint256"),
        ("mathMax", {}, r"Math.max with 2 arguments has no lemma"),
        ("recursion", {}, r"recursion calls itself"),
        ("assignment", {}, r"OutOfScope.sol:83:5: an assignment to the input _a"),
        ("requireCall", {}, r"OutOfScope.sol:88:21: the message of require is not a string literal or an error"),
        ("structParameterOfACallee", {}, r"the struct parameter _pair of a called function"),
        ("moduloAssignment", {}, r"mod is not in the operator table"),
        ("enumCast", {}, r"a type conversion: uint8\(_side\)"),
        ("enumOrder", {}, r"lt takes unsigned operands, not enum, enum"),
        ("addressLocalWithoutValue", {}, r"a local of type address declared without a value"),
        ("inputsName", {}, r"OutOfScope.sol:\d+:19: i names the inputs struct of the emitted Solidity"),
        ("noInputs", {}, r"noInputs has no inputs: its result is a constant"),
        ("constantNamedI", {}, r"OutOfScope.sol:\d+:3: the constant i names the inputs struct of the emitted Solidity"),
        ("constantNamedAsALocal", {}, r"OutOfScope.sol:\d+:3: the constant A has the name of a parameter, local"),
        ("shadowing", {}, r"OutOfScope.sol:\d+:7: the local _x shadows a parameter, local or return of the same name"),
        (
            "readsTotal",
            {"$total": "total", "TOKEN.balanceOf(address(this))": "pot"},
            r"the read map names TOKEN.balanceOf\(address\(this\)\), which OutOfScope.readsTotal does not read",
        ),
        ("readsTotal", {}, r"reads the mutable variable \$total, which the read map does not name"),
    ],
)
def test_the_extractor_refuses_each_construct_outside_the_scope(build, function, reads, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{OUT_OF_SCOPE}.{function}", reads)


def test_an_environment_field_in_the_read_map_is_an_input_even_where_the_function_does_not_read_it(build):
    model = extract(build, f"{QUOTER}._withMargin", {"tx.gasprice": "txGasPrice"}).model
    assert [(r.var.name, r.source) for r in model.reads] == [("txGasPrice", "tx.gasprice")]
    with pytest.raises(ExtractionError, match="the read map names \\$floor, which Quoter._withMargin does not read"):
        extract(build, f"{QUOTER}._withMargin", {"$floor": "floor"})


PRICER = "tests/fixtures/contracts/Pricer.sol:Pricer"
OTHER = 0xA0B86991C6218B36C1D19D4A2E9EB0CE3606EB48
PRICED_READS = {
    "_meter.used": "used",
    "_meter.fresh": "fresh",
    "_meter.paid": "paid",
    "TOKEN": "token",
    "TOKEN.balanceOf(tx.origin)": "originBalance",
}


def priced_reference(_kind, _token, used, fresh, paid, token, originBalance):
    """Pricer._priced in plain Python integers. None means it reverts."""
    base = {1: 1000, 2: 9000}.get(_kind, 0)
    ceiling = 0 if base == 0 else base if _token == token else base + 700 if _token == OTHER else 0
    if not paid or ceiling == 0:
        return 0
    gross = used + 21_000
    if gross >= 2**256:
        return None
    if originBalance == 0:
        gross, ceiling = gross + 5000, ceiling + 5000
        if gross >= 2**256:
            return None
    refund = min((300 if _token == token else 700) + (100 if fresh else 200), gross // 5)
    return min(gross - refund, ceiling)


def test_the_pricer_model_reads_struct_fields_an_immutable_address_and_an_enum(build):
    model = extract(build, f"{PRICER}._priced", PRICED_READS).model
    assert [(v.name, v.type.solidity) for v in model.inputs] == [
        ("_kind", "Kinds.Kind"),
        ("_token", "address"),
        ("used", "uint256"),
        ("fresh", "bool"),
        ("paid", "bool"),
        ("token", "address"),
        ("originBalance", "uint256"),
    ]
    assert model.function.params[0].type.bound == 4
    assert [f.name for f in model.callees] == ["_ceiling", "_refund"]
    assert model.callees[1].result.name == "refund"
    text = source(model)
    assert 'import {Kinds} from "tests/fixtures/contracts/Kinds.sol";' in text
    assert "address internal constant OTHER = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;" in text
    assert "      gross = gross + EXTRA;\n      ceiling = ceiling + EXTRA;\n" in text
    assert "function _refund(Inputs memory i, address _token) internal pure returns (uint256 refund) {" in text


def test_the_pricer_terms_give_the_reference_result(build):
    model = extract(build, f"{PRICER}._priced", PRICED_READS).model
    rng = random.Random(2)
    token = 0xBEEF
    outcomes = set()
    for _ in range(300):
        values = {
            "_kind": rng.randrange(4),
            "_token": rng.choice([token, OTHER, 0xD00D]),
            "used": rng.choice([0, 1, 2**64, 2**256 - 21_001, 2**256 - 21_000, 2**256 - 1, rng.getrandbits(40)]),
            "fresh": rng.random() < 0.5,
            "paid": rng.random() < 0.8,
            "token": token,
            "originBalance": rng.choice([0, 1, rng.getrandbits(80)]),
        }
        expected = priced_reference(**values)
        reverts, value, _ = evaluate(model, values)
        assert reverts == (expected is None), values
        if not reverts:
            assert value == expected, values
        outcomes.add("reverts" if reverts else "pays" if value else "zero")
    assert outcomes == {"pays", "zero", "reverts"}


def test_a_quote_reads_a_mapping_entry_through_a_copy_and_cuts_an_internal_call(build):
    reads = {"$rates[_kind].floor": "floor", "$rates[_kind].cap": "cap", "_price()": "price"}
    model = extract(build, f"{PRICER}.quoteFor", reads).model
    assert [r.source for r in model.reads] == list(reads)
    assert [f.name for f in model.callees] == ["units"]
    for kind, units in ((0, 1), (1, 3), (2, 7), (3, 1)):
        values = {"_kind": kind, "floor": 5, "cap": 2**200, "price": 10}
        assert evaluate(model, values)[1] == max(units * 10 - 5, 0)
    with pytest.raises(ExtractionError, match=r"reads a storage field \$rates\[_kind\].cap"):
        extract(build, f"{PRICER}.quoteFor", {"$rates[_kind].floor": "floor", "_price()": "price"})


def test_named_arguments_bind_to_the_parameters_of_their_names(build):
    model = extract(build, f"{PRICER}._named", {}).model
    assert evaluate(model, {"_x": 3, "_y": 5}) == (False, 2 + 6, False)
    assert evaluate(model, {"_x": 5, "_y": 3})[0]


def test_an_address_constant_keeps_its_conversion_unless_it_is_an_address_literal(build):
    text = source(extract(build, f"{PRICER}._isNobody", {}).model)
    assert "  address internal constant NOBODY = address(0);\n" in text
    priced = source(extract(build, f"{PRICER}._priced", PRICED_READS).model)
    assert "  address internal constant OTHER = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48;\n" in priced


def test_a_conditional_takes_the_type_that_holds_both_branches(build):
    model = extract(build, f"{PRICER}._wide", {}).model
    assert evaluate(model, {"_flag": True, "_x": 3}) == (False, 4, False)
    assert evaluate(model, {"_flag": False, "_x": 3}) == (False, 2**255 + 1, False)


def test_an_operation_whose_type_differs_from_solc_s_is_refused(build, monkeypatch):
    monkeypatch.setattr(ops, "literal_type", lambda value: uint(8))
    with pytest.raises(ExtractionError, match="ite has the type uint8 here and uint256 in solc"):
        extract(build, f"{PRICER}._wide", {})


def test_a_bool_constant_and_an_address_literal_keep_their_types(build):
    strict = extract(build, f"{PRICER}._strict", {}).model
    assert evaluate(strict, {"_a": 1})[1] is True and evaluate(strict, {"_a": 0})[1] is False
    assert "  bool internal constant STRICT = true;\n" in source(strict)
    one = extract(build, f"{PRICER}._isOne", {}).model
    assert evaluate(one, {"_a": 1})[1] is True and evaluate(one, {"_a": 2})[1] is False
    assert "return i._a == 0x0000000000000000000000000000000000000001;" in source(one)


WRITES = "tests/fixtures/contracts/Writes.sol:Writes"
REVERTS = "tests/fixtures/contracts/Reverts.sol:Reverts"


def reverts(model, values) -> bool:
    inputs = symbols(model)
    pairs = [(inputs[v.name], z3.IntVal(values[v.name])) for v in model.inputs]
    return z3.is_true(z3.simplify(z3.substitute(terms(model, inputs).reverts, *pairs)))


LIBRARIES = "tests/fixtures/contracts/Libraries.sol:Libraries"


def test_a_function_that_writes_storage_is_modeled_by_its_value_and_the_model_lists_the_writes(build):
    reads = {"$info.rate": "stored", "$balances[_who]": "balance"}
    model = extract(build, f"{WRITES}.updateThenRead", reads).model
    assert [v.name for v in model.inputs] == ["_price", "_who", "stored", "balance"]
    assert evaluate(model, {"_price": 10**18, "_who": 1, "stored": 0, "balance": 5}) == (False, 10**18 + 5, False)
    assert reverts(model, {"_price": 0, "_who": 1, "stored": 0, "balance": 5})
    assert [e.partition(": ")[2] for e in model.effects] == [
        "info.lastTimestamp = uint96(block.timestamp)",
        "info.rate = rate",
        "$info = info",
        "emit Updated(rate)",
    ]
    effect = "//   tests/fixtures/contracts/Writes.sol:32:7: info.lastTimestamp = uint96(block.timestamp)\n"
    assert effect in source(model)
    credit = extract(build, f"{WRITES}.credit", {}).model
    assert evaluate(credit, {"_who": 1, "_x": 7}) == (False, 7, False)


def test_require_assert_and_revert_revert_where_they_run(build):
    model = extract(build, f"{REVERTS}.checked", {}).model
    assert [reverts(model, {"_x": x}) for x in range(5)] == [True, True, True, True, False]
    assert evaluate(model, {"_x": 4}) == (False, 4, False)
    assert "      revert();\n" in source(model)
    always = extract(build, f"{REVERTS}.alwaysReverts", {}).model
    assert reverts(always, {v.name: 0 for v in always.inputs})


@pytest.mark.parametrize(
    "function, reads, message",
    [
        ("readAfterWrite", {"$count": "count"}, r"Writes.sol:\d+:12: reads \$count after a write to it"),
        ("readCopyAfterWrite", {"$info.rate": "rate"}, r"reads \$info.rate after a write to its memory copy"),
        ("copyAfterWrite", {"$info.rate": "rate"}, r"reads \$info after a write to it"),
        ("writeThatCanRevert", {}, r"Writes.sol:\d+:14: _x \+ 1 in a write or an event: the model leaves it out"),
        ("writeToAnArray", {}, r"\$list\[_x\] in a write or an event"),
        ("eventThatCanRevert", {}, r"_x \* 2 in a write or an event"),
        ("updateInsideAnExpression", {"$info.rate": "rate"}, r"update writes storage, so its call must be the whole"),
        ("readsInfoAfterUpdate", {"$info.rate": "rate"}, r"reads \$info after a write to it"),
        ("callsAReaderAfterUpdate", {"$info.rate": "rate"}, r"calls _rate, which reads \$info, after a write"),
        (
            "externalReadAfterWrite",
            {"TOKEN.balanceOf(address(this))": "pot"},
            r"the read TOKEN.balanceOf\(address\(this\)\) follows a write, and its call could read the change",
        ),
        ("stateChangingRead", {"TOKEN.transfer(address(1), 1)": "sent"}, r"calls transfer, which can change state"),
    ],
)
def test_the_extractor_refuses_a_write_that_could_change_the_value_or_the_revert(build, function, reads, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{WRITES}.{function}", reads)


def test_a_library_call_on_a_value_widened_storage_and_a_user_defined_key(build):
    reads = {"$positions[key][who].collateral": "collateral"}
    model = extract(build, f"{LIBRARIES}.value(Key,address,uint256)", reads).model
    assert [v.name for v in model.inputs] == ["who", "price", "collateral"]
    assert [v.type.solidity for v in model.inputs] == ["address", "uint256", "uint128"]
    assert [f.name for f in model.callees] == ["mulDivDown"]
    assert evaluate(model, {"who": 1, "price": 3 * 10**18, "collateral": 2**128 - 1})[1] == 3 * (2**128 - 1)
    assert "return mulDivDown(i, uint256(i.collateral), i.price, 1e18);" in source(model)
    shares = extract(build, f"{LIBRARIES}.shares", {}).model
    assert [f.name for f in shares.callees] == ["mulDivDown", "toSharesDown"]
    values = {"assets": 10, "totalAssets": 99, "totalShares": 0}
    assert evaluate(shares, values)[1] == 10 * 10**6 // 100 + 5
    product = extract(build, f"{LIBRARIES}.product", {}).model
    assert evaluate(product, {"a": 2**64 - 1, "b": 2**64 - 1})[1] == (2**64 - 1) ** 2


def test_a_target_of_several_functions_of_one_name_gives_its_parameter_types(build):
    with pytest.raises(ExtractionError, match="Libraries has 2 functions named value, not one"):
        extract(build, f"{LIBRARIES}.value", {})
    with pytest.raises(ExtractionError, match=r"Libraries has 0 functions named value\(Key\), not one"):
        extract(build, f"{LIBRARIES}.value(Key)", {})
    # Its callee, the other value, has a parameter of the user-defined type, which only the modeled function can have.
    with pytest.raises(ExtractionError, match=r"Libraries.sol:33:18: the type Key is not supported"):
        extract(build, f"{LIBRARIES}.value(Key,address)", {"$positions[key][who].collateral": "c"})
