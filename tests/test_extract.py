import random

import pytest
import z3

from solift import ops
from solift.extract import ExtractionError, extract
from solift.ir import Op, declared_locals, enum, uint, walk
from solift.smt import symbols, terms
from solift.solidity import source
from tests.models import QUOTE_SOURCE, WAD, quote_reference
from tests.test_smt import evaluate

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


VIRTUAL = "tests/fixtures/contracts/Virtual.sol"


@pytest.mark.parametrize(
    "target, values, result",
    [
        # The base's own function runs its own fee, and the derived contract's runs the override.
        ("Fees.total", {"_a": 5}, 6),
        ("DerivedFees.total", {"_a": 5}, 7),
        # weigh({_value: 3, _weight: 4}) passes 3 first, which the override names _weight.
        ("DerivedFees.weighed", {"_a": 3, "_b": 4}, 34),
        # The same named call passes (1, 2) in Fees and (2, 1) in DerivedFees: 12 and 21.
        ("DerivedFees.weighedTwice", {"_a": 5}, 38),
        ("Tiers.own", {"_a": 5}, 9),
        # super in HighTier is Tier in HighTier, and LowTier in Tiers; Tier.rate() is Tier's in both.
        ("HighTier.next", {"_a": 5}, 6),
        ("Tiers.next", {"_a": 5}, 7),
        ("Tiers.named", {"_a": 5}, 6),
        # super in Third runs Second's share, which does not override First's.
        ("Shares.shared", {"_a": 5}, 8),
    ],
)
def test_an_internal_call_runs_the_override_in_the_linearization_of_the_targets_contract(build, target, values, result):
    model = extract(build, f"{VIRTUAL}:{target}", {}).model
    assert evaluate(model, values) == (False, result, False)


def test_a_function_that_a_base_declares_without_a_body_runs_the_derived_contracts(build):
    reads = {"supply": "supply", "TOKEN.balanceOf(address(this))": "cash"}
    extraction = extract(build, f"{VIRTUAL}:TokenPool.rate", reads)
    assert [f.name for f in extraction.model.callees] == ["cash"]
    assert evaluate(extraction.model, {"supply": 2, "cash": 3}) == (False, 3 * WAD // 2, False)
    with pytest.raises(ExtractionError, match=r"Virtual.sol:\d+:3: cash is not an implemented function"):
        extract(build, f"{VIRTUAL}:Pool.rate", {"supply": "supply"})


@pytest.mark.parametrize(
    "target, reads, message",
    [
        # Only the override calls gasleft: in its body, in its modifier, and in the function that this.exposed() runs.
        ("GasMeter.spent", {"left()": "left"}, r"the read left\(\) calls gasleft"),
        ("GatedMeter.spent", {"left()": "left"}, r"the read left\(\) calls gasleft"),
        ("GasMeter.viaThis", {"this.exposed()": "exposed"}, r"the read this.exposed\(\) calls gasleft"),
        # A modifier that the target overrides can change state, and a qualified modifier runs as named.
        ("CountedTwice.delta", {"current()": "now"}, r"the read current\(\) runs the modifier counts of CountedTwice"),
        ("Unthrottled.levels", {"level()": "level"}, r"the read level\(\) calls gasleft"),
        # super skips a declaration without a body; a pointer read from storage is not followed.
        ("Probes.delta", {"super.sense()": "sensed"}, r"the read super.sense\(\) calls gasleft"),
        ("Pointed.twice", {"indirect()": "counted"}, r"the read indirect\(\) reads the function pointer pointer from"),
        ("GasOptioned.viaPointer", {"optioned()": "left"}, r"the read optioned\(\) reads the function pointer pointer"),
        (
            "Optioned.viaGetter",
            {"gotten()": "selector"},
            r"the read gotten\(\) reads the function pointer this.pointer",
        ),
        # A function named as a value runs as named too: through an internal pointer, and on another receiver.
        ("FlatPinned.viaPinned", {"pinned()": "pinned"}, r"the read pinned\(\) calls gasleft"),
        ("FlatPinned.viaSent", {"sent(_other)": "sent"}, r"the read sent\(_other\) calls gasleft"),
        ("Prober.delta", {"read(_schema,_receiver)": "read"}, r"the read read\(_schema, _receiver\) calls gasleft"),
        # A function named through its contract as a value, and a call through (this), run the override.
        ("GasOptioned.viaEncodedByName", {"encodedByName()": "encoded"}, r"the read encodedByName\(\) calls gasleft"),
        ("GasOptioned.viaParenthesized", {"parenthesized()": "left"}, r"the read parenthesized\(\) calls gasleft"),
        # A low-level call runs the function that abi.encodeCall names, or whose selector it sends, and so does a
        # pointer that assembly builds from a selector.
        ("GasOptioned.viaEncoded", {"encoded()": "encoded"}, r"the read encoded\(\) calls gasleft"),
        ("GasOptioned.viaSentSelector", {"sentSelector()": "sent"}, r"the read sentSelector\(\) calls gasleft"),
        ("GasOptioned.viaBuilt", {"built()": "built"}, r"the read built\(\) calls gasleft"),
        ("Relays.viaSent", {"sent(_relay)": "sent"}, r"the read sent\(_relay\) reads gas\(\) in assembly"),
        # gas() in assembly, other than as the gas that a call forwards, in the override and on a receiver, and a copy
        # that a Yul function in the gas argument keeps.
        ("GasAssembled.delta", {"measure()": "measured"}, r"the read measure\(\) reads gas\(\) in assembly"),
        ("Kept.delta", {"kept()": "kept"}, r"the read kept\(\) reads gas\(\) in assembly"),
        ("Relays.viaProbe", {"_relay.probe(address(0))": "probed"}, r"the read _relay.probe\(address\(0\)\) reads gas"),
        # A receiver typed GasReader runs its override of reading.
        ("Sampler.delta", {"_other.read()": "got"}, r"the read _other.read\(\) calls gasleft"),
        # Another instance runs Gauge's reading, which calls gasleft, not the target's override.
        ("FixedGauge.twice", {"_other.get()": "got"}, r"the read _other.get\(\) calls gasleft"),
        # super.rate() is Tier's rate in lower and LowTier's in higher: one input cannot stand for both.
        ("Tiers.both", {"super.rate()": "rate"}, r"the read super.rate\(\) names other things than at its first read"),
        # Both calls run DerivedFees.weigh, but bind their named arguments by two declarations' names.
        (
            "DerivedFees.weighedTwice",
            {"weigh({_value:1,_weight:2})": "weighed"},
            r"the read weigh\(\{_value: 1, _weight: 2\}\) names other things than at its first read",
        ),
        ("Tiers.both", {}, r"two callees are named rate"),
    ],
)
def test_a_read_map_call_is_checked_as_the_function_that_runs(build, target, reads, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{VIRTUAL}:{target}", reads)
    assert extract(build, f"{VIRTUAL}:Meter.spent", {"left()": "left"}).model.reads
    assert extract(build, f"{VIRTUAL}:Meter.viaThis", {"this.exposed()": "exposed"}).model.reads
    assert extract(build, f"{VIRTUAL}:Counted.delta", {"current()": "now"}).model.reads
    # A function called in parentheses is no pointer; gas forwarded to a precompile and a comparator passed to a sort
    # are no change.
    assert extract(build, f"{VIRTUAL}:GasOptioned.viaParentheses", {"wrapped()": "seven"}).model.reads
    assert extract(build, f"{VIRTUAL}:Medians.quote", {"median()": "price"}).model.reads
    assert extract(build, f"{VIRTUAL}:Medians.power", {"Math.modExp(_b, _e, _m)": "power"}).model.reads
    assert extract(build, f"{VIRTUAL}:Medians.hashed", {"precompiled(_x)": "hashed"}).model.reads
    # A selector that no call sends runs nothing.
    assert extract(build, f"{VIRTUAL}:GasOptioned.viaSelector", {"selector()": "selector"}).model.reads
    assert extract(build, f"{VIRTUAL}:Relays.viaSelector", {"probeSelector()": "selector"}).model.reads
    # The getter of FixedRated's fixedRate runs, and no modifier.
    assert extract(build, f"{VIRTUAL}:FixedRated.total", {"this.fixedRate()": "rate"}).model.reads


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
        ("signedParameter", {}, r"OutOfScope.sol:30:12: a signed integer"),
        ("cast", {"msg.sender": "sender"}, r"OutOfScope.sol:34:12: a type conversion: uint160\(msg.sender\)"),
        (
            "tupleOfAnExternalCall",
            {"this.pair(_a)": "p"},
            r"OutOfScope.sol:38:13: the read this.pair\(_a\) gives several values, and a read map entry names one",
        ),
        ("tupleAssignmentToStorage", {}, r"OutOfScope.sol:42:6: a tuple assignment to \$total, which is not a local"),
        ("exponentiation", {}, r"OutOfScope.sol:47:12: exponentiation"),
        ("assembly_", {}, r"OutOfScope.sol:52:5: inline assembly"),
        ("unmappedRead", {}, r"reads an environment field block.timestamp, which the read map does not name"),
        ("modulo", {}, r"OutOfScope.sol:63:12: mod is not in the operator table"),
        ("narrowArithmetic", {}, r"OutOfScope.sol:67:12: add at uint128: arithmetic narrower than uint256"),
        ("mathAverage", {}, r"Math.average with 2 arguments has no lemma"),
        ("recursion", {}, r"recursion calls itself"),
        ("assignment", {}, r"OutOfScope.sol:83:5: an assignment to the input _a"),
        ("requireCall", {}, r"OutOfScope.sol:88:21: the message of require is not a string literal or an error"),
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


def test_an_unread_key_of_an_enum_type_keeps_the_import_of_the_enum(build):
    """A mutant that drops the only read of an enum keeps its key as an input of the enum type, which the emitted
    Solidity must import, as a read of it would."""
    extraction = extract(build, f"{QUOTER}._quote", {**READS, "$kind": "kind"}, unread={"$kind": enum("Kinds.Kind", 4)})
    assert extraction.unread == ("$kind",)
    assert 'import {Kinds} from "tests/fixtures/contracts/Kinds.sol";' in source(extraction.model)


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
    assert [(e.kind, e.place, e.source) for e in model.effects] == [
        ("copy", "info.lastTimestamp", "info.lastTimestamp = uint96(block.timestamp)"),
        ("copy", "info.rate", "info.rate = rate"),
        ("write", "$info", "$info = info"),
        ("event", "Updated(uint256)", "emit Updated(rate)"),
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


TUPLES = "tests/fixtures/contracts/Tuples.sol:Tuples"


def test_a_function_of_several_returns_is_a_tuple_and_a_tuple_declaration_takes_it_apart(build):
    limits = extract(build, f"{TUPLES}.limits", {}).model
    assert limits.function.returns.solidity == "uint256, uint256"
    assert [v.name for v in limits.function.named] == ["sum", "low"]
    assert evaluate_vector(limits, {"_a": 3, "_b": 5}) == (False, (8, 3))
    assert evaluate_vector(limits, {"_a": 2**256 - 1, "_b": 1}) == (True, (0, 0))
    text = source(limits)
    assert "function limits(Inputs memory i) internal pure returns (uint256 sum, uint256 low) {" in text
    assert "function run(TuplesLimitsModel.Inputs memory i) external pure returns (uint256, uint256) {" in text
    pullable = extract(build, f"{TUPLES}.pullable", {}).model
    assert [f.name for f in pullable.callees] == ["limits"]
    assert evaluate_vector(pullable, {"_a": 3, "_b": 5}) == (False, 3)
    assert "    (, uint256 low) = limits(i, i._a, i._b);\n" in source(pullable)
    forwarded = extract(build, f"{TUPLES}.forwarded", {}).model
    assert evaluate_vector(forwarded, {"_a": 7, "_b": 2}) == (False, (9, 2))


def test_a_tuple_assignment_evaluates_the_whole_tuple_before_it_assigns(build):
    model = extract(build, f"{TUPLES}.split", {"$cap": "cap"}).model
    assert model.function.returns.solidity == "uint256, bool"
    assert evaluate_vector(model, {"_a": 9, "cap": 4}) == (False, (5, True))
    assert evaluate_vector(model, {"_a": 9, "cap": 9}) == (False, (1, False))
    assert evaluate_vector(model, {"_a": 8, "cap": 9}) == (False, (0, False))
    text = source(model)
    assert "    (uint256 half, uint256 rest) = _halves(i, i._a);\n    (half, rest) = (rest, half);\n" in text
    assert "    return (half - rest, false);\n" in text


def test_a_function_of_several_returns_returns_its_named_values_and_zero_for_one_without_a_name(build):
    partly = extract(build, f"{TUPLES}.partlyNamed", {}).model
    assert evaluate_vector(partly, {"_a": 4}) == (False, (5, 0))
    assert "returns (uint256 first, uint256) {" in source(partly)
    early = extract(build, f"{TUPLES}.earlyReturn", {}).model
    assert evaluate_vector(early, {"_a": 4}) == (False, (4, True))
    assert evaluate_vector(early, {"_a": 14}) == (False, (4, False))
    assert "      return (first, true);\n" in source(early)
    with pytest.raises(ExtractionError, match=r"Tuples.sol:\d+:9: a tuple assignment to x twice"):
        extract(build, f"{TUPLES}.assignedTwice", {})


def evaluate_vector(model, values):
    from solift.vectors import evaluate as vector

    found = vector(model, values)
    return found.reverts, found.result


POINTERS = "tests/fixtures/contracts/Pointers.sol:Pointers"
SPENDER = {
    "$spenders[_who].config.activeUntil": "activeUntil",
    "block.timestamp": "now",
    "$spenders[_who].last": "last",
    "$spenders[_who].config.perPeriod": "perPeriod",
    "$spenders[_who].left": "left",
    "$main.left": "mainLeft",
}


def test_a_storage_pointer_reads_the_fields_of_its_struct_also_in_the_callees_it_is_passed_to(build):
    model = extract(build, f"{POINTERS}.available", SPENDER).model
    assert [(r.source, r.var.type.solidity) for r in model.reads] == [
        ("$spenders[_who].config.activeUntil", "uint64"),
        ("block.timestamp", "uint256"),
        ("$spenders[_who].last", "uint64"),
        ("$spenders[_who].config.perPeriod", "uint128"),
        ("$spenders[_who].left", "uint192"),
        ("$main.left", "uint192"),
    ]
    assert [(f.name, [p.name for p in f.params]) for f in model.callees] == [("_isLive", []), ("_limits", [])]
    values = {"_who": 1, "activeUntil": 100, "now": 50, "last": 40, "perPeriod": 30, "left": 5, "mainLeft": 99}
    assert evaluate_vector(model, values) == (False, 15)
    assert evaluate_vector(model, {**values, "now": 101}) == (False, 0)
    assert evaluate_vector(model, {**values, "mainLeft": 7}) == (False, 7)
    assert "    (, uint256 pullable) = _limits(i);\n" in source(model)


def test_a_storage_pointer_at_msg_sender_to_a_nested_struct_or_as_a_parameter_names_its_fields(build):
    mine = extract(build, f"{POINTERS}.mine", {"$spenders[msg.sender].left": "left"}).model
    assert [r.source for r in mine.reads] == ["$spenders[msg.sender].left"]
    twice = extract(build, f"{POINTERS}.mineTwice", {"$spenders[msg.sender].left": "left"}).model
    assert [r.source for r in twice.reads] == ["$spenders[msg.sender].left"]
    nested = extract(build, f"{POINTERS}.perPeriod", {"$spenders[_who].config.perPeriod": "perPeriod"}).model
    assert [r.source for r in nested.reads] == ["$spenders[_who].config.perPeriod"]
    reads = {"block.timestamp": "now", "_spender.last": "last", "_spender.config.perPeriod": "perPeriod"}
    limits = extract(build, f"{POINTERS}._limits", {**reads, "_spender.left": "left", "$main.left": "mainLeft"}).model
    assert [p.name for p in limits.function.params] == []
    assert "_spender.config.perPeriod" in [r.source for r in limits.reads]
    copy = {"$spenders[_who].config.perPeriod": "perPeriod", "$spenders[_who].left": "left"}
    assert extract(build, f"{POINTERS}.wholeConfig", copy).model.reads[0].source == "$spenders[_who].config.perPeriod"


def test_a_write_through_a_storage_pointer_is_an_effect_and_a_later_read_of_it_is_refused(build):
    model = extract(build, f"{POINTERS}.touch", {"$spenders[_who].left": "stored"}).model
    assert [(e.kind, e.place) for e in model.effects] == [("write", "$spenders[_who].last")]
    with pytest.raises(ExtractionError, match=r"Pointers.sol:\d+:12: reads \$spenders after a write to it"):
        extract(build, f"{POINTERS}.readAfterWrite", {"$spenders[_who].left": "left"})
    with pytest.raises(ExtractionError, match="a write through the storage parameter _spender: it could be any"):
        extract(build, f"{POINTERS}._writeThrough", {})


def test_a_pointer_or_a_read_at_a_key_that_can_change_or_a_callee_on_two_pointers_is_refused(build):
    with pytest.raises(ExtractionError, match=r"Pointers.sol:\d+:31: a storage pointer to \$spenders\[who\]: a"):
        extract(build, f"{POINTERS}.atALocalKey", {})
    with pytest.raises(ExtractionError, match=r"Pointers.sol:\d+:31: a storage pointer to \$list\[_n\]: a"):
        extract(build, f"{POINTERS}.inAnArray", {})
    with pytest.raises(ExtractionError, match=r"calls _left on other storage than its first call \(_spender = "):
        extract(build, f"{POINTERS}.twoSpenders", {"$spenders[_a].left": "a", "$spenders[_b].left": "b"})
    # A read map key names one value for the call: it changes nothing, and it is not at a local that changes, below a
    # pointer or in a callee too, nor at other declarations of the same names, nor in a callee that runs twice, also
    # through another callee, though it can be at the parameter of a callee that runs once.
    for function, key, message in [
        (
            "owedTwice",
            "$book.owed[who]",
            r"Pointers.sol:\d+:30: the read \$book.owed\[who\] names who, .* second place",
        ),
        ("slotsIncremented", "$book.slots[n++]", r"Pointers.sol:\d+:33: the read \$book.slots\[n\+\+\] changes n\+\+"),
        ("slotsAssigned", "$book.slots[_n = _m]", r"the read \$book.slots\[_n = _m\] changes _n = _m: a read map"),
        ("owedInBranches", "$book.owed[who]", r"the read \$book.owed\[who\] names other things than at its"),
        ("slotsOfTwoLibraries", "$book.slots[K]", r"the read \$book.slots\[K\] names other things than at its"),
        ("sameValue", "level", r"the read level names other things than at its first read"),
        ("samePick", "picks[c.key]", r"the read picks\[c.key\] names other things than at its first read"),
        ("sameSpenders", "_valueOf(_p)", r"the read _valueOf\(_p\) names other things than at its first read"),
        ("sameScores", '$scores["ab"]', r'the read \$scores\["ab"\] names other things than at its first read'),
        ("sameSender", "msg.sender", r"the read msg.sender names other things than at its first read"),
        ("samePair", "picks[p.key]", r"the read picks\[p.key\] names other things than at its first read"),
        ("flipTwice", "_flip(c)", r"the read _flip\(c\) passes memory to _flip, which can change it"),
        ("gasTwice", "gasleft()", r"the read gasleft\(\) calls gasleft, whose value changes during the call"),
        ("sameChoice", "choice.key", r"calls keyOf on other storage than its first call \(_choice = choice\)"),
        ("owedNested", "$book.owed[_who]", r"Pointers.sol:\d+:3: _owedThrough runs 2 times in the call: a read map"),
    ]:
        with pytest.raises(ExtractionError, match=message):
            extract(build, f"{POINTERS}.{function}", {key: "owed"})
    assert [r.source for r in extract(build, f"{POINTERS}.owedOnce", {"$book.owed[_who]": "owed"}).model.reads] == [
        "$book.owed[_who]"
    ]
    # A callee that runs once reads its parameter after an assignment, at one place: the input is the read there.
    reset = extract(build, f"{POINTERS}.owedReset", {"$book.owed[_who]": "owed"}).model
    assert [r.source for r in reset.reads] == ["$book.owed[_who]"]
    # A parameter with the name of a storage variable in the read map is the parameter.
    shadowed = extract(build, f"{POINTERS}.valueIs", {"level": "owed"}).model
    assert [v.name for v in shadowed.inputs] == ["level", "owed"]


EFFECTS = "tests/fixtures/contracts/Effects.sol:Effects"
PULL_READS = {"$allowances[msg.sender].left": "stored", "block.timestamp": "time"}
PULL_OUTPUTS = {"$allowances[msg.sender].left": "remaining", "$allowances[msg.sender].last": "last"}


def test_an_external_call_argument_is_an_output_of_a_function_without_a_value(build):
    model = extract(
        build, f"{EFFECTS}.buy", {"SWAPPER.fee()": "fee"}, {"SWAPPER.buy(msg.sender,amount)": (None, "bought")}
    ).model
    assert model.function.returns.solidity == "uint256" and model.results == ()
    assert [(o.var.name, o.kind, o.index, o.source) for o in model.outputs] == [
        ("bought", "call", 1, "SWAPPER.buy(msg.sender,amount)")
    ]
    assert evaluate_vector(model, {"_amountIn": 3 * 10**18, "fee": 0}) == (False, 3)
    assert evaluate_vector(model, {"_amountIn": 1, "fee": 2**256 - 1}) == (True, 0)
    text = source(model)
    assert "function buy(Inputs memory i) internal pure returns (uint256 bought) {" in text
    assert "    bought = amount;\n" in text


def test_a_write_is_an_output_whose_value_can_revert_and_a_narrowing_conversion_keeps_the_low_bits(build):
    model = extract(build, f"{EFFECTS}.pull", PULL_READS, PULL_OUTPUTS).model
    assert model.function.returns.solidity == "uint256, uint192, uint64"
    assert [(e.kind, e.place) for e in model.effects] == [("event", "Pulled(address,uint256)")]
    values = {"_amount": 3, "_refill": 10, "stored": 5, "time": 2**64 + 7}
    assert evaluate_vector(model, values) == (False, (3, 12, 7))
    assert evaluate_vector(model, {**values, "_refill": 2**192}) == (False, (3, 2, 7))
    assert evaluate_vector(model, {**values, "_amount": 16}) == (True, (0, 0, 0))
    assert "    remaining = uint192(left - i._amount);\n    last = uint64(i.time);\n" in source(model)


def test_an_output_with_its_value_before_the_call_is_written_on_some_paths_and_read_after_its_write(build):
    model = extract(build, f"{EFFECTS}.add", {"$total": "before"}, {"$total": "total"}).model
    assert [r.source for r in model.reads] == ["$total"]
    assert evaluate_vector(model, {"_x": 0, "_y": 9, "before": 4}) == (False, (False, 4))
    assert evaluate_vector(model, {"_x": 5, "_y": 2, "before": 4}) == (False, (True, 7))
    assert evaluate_vector(model, {"_x": 5, "_y": 10, "before": 4}) == (True, (False, 0))
    assert "    total = i.before;\n" in source(model)
    # The place of an output names one storage variable, as a read map key does.
    with pytest.raises(ExtractionError, match=r"the read level names other things than at its first read"):
        extract(build, f"{POINTERS}.setLevel", {"level": "before"}, {"level": "updated"})
    # A memory copy keeps the value from before the write.
    place = "$allowances[msg.sender].left"
    replaced = extract(build, f"{EFFECTS}.replace", {place: "stored"}, {place: "left"}).model
    assert evaluate_vector(replaced, {"_x": 9, "stored": 5}) == (False, (5, 9))


@pytest.mark.parametrize(
    "function, reads, outputs, message",
    [
        ("writeOnSomePaths", {}, {"$count": "count"}, r"can end without a write of the output count \(\$count\)"),
        ("skip", {}, {"$count": "count"}, r"Effects.sol:\d+:7: returns without a write of the output count \(\$coun"),
        (
            "skip",
            {"$count": "before"},
            {"$count": "count", "SWAPPER.buy(msg.sender,_x)": (None, "bought")},
            r"returns without a write of the output bought \(SWAPPER.buy\(msg.sender,_x\)\): make the call on every",
        ),
        (
            "otherWriteToAnOutput",
            {},
            {"$allowances[msg.sender].left": "left", "$allowances[_a].left": "other"},
            r"the outputs \$allowances\[msg.sender\].left and \$allowances\[_a\].left are one place where their keys",
        ),
        (
            "otherWriteToAnOutput",
            {},
            {"$allowances[msg.sender].left": "left"},
            r"Effects.sol:\d+:5: writes \$allowances\[_a\].left, which could change the output \$allowances\[msg",
        ),
        ("readAfterACall", {"$count": "count"}, {}, r"reads \$count after an external call, which could change it"),
        ("nothing", {}, {}, r"Effects.nothing returns no value: name a write or an external call in the output map"),
        (
            "boundCallStatement",
            {"SWAPPER": "swapper"},
            {},
            r"Effects.sol:\d+:5: buyAll, which a statement calls, writes storage, emits or calls out",
        ),
        ("add", {}, {"$total": "total"}, r"\+= to the output \$total needs its value before the call"),
        ("buy", {"SWAPPER.fee()": "fee"}, {"SWAPPER.buy(msg.sender,amount)": "x"}, r"names 1 values of SWAPPER.buy"),
        ("truncate", {}, {"$total": "total"}, r"the output map names \$total, which Effects.truncate does not write"),
        (
            "outputAtALocalKey",
            {},
            {"$allowances[who].left": "left"},
            r"Effects.sol:\d+:5: the output \$allowances\[who\].left is at a key that can change during the call",
        ),
    ],
)
def test_the_extractor_refuses_an_output_that_the_model_cannot_have(build, function, reads, outputs, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{EFFECTS}.{function}", reads, outputs)


def test_an_immutable_is_read_after_an_external_call_which_cannot_change_it(build):
    model = extract(build, f"{EFFECTS}.swapperAfterACall", {"SWAPPER": "swapper"}).model
    assert [(e.kind, e.place) for e in model.effects] == [("call", "SWAPPER.buy(msg.sender,1)")]
    assert evaluate_vector(model, {"swapper": 7}) == (False, 7)


ROUNDED = "tests/fixtures/contracts/Rounded.sol:Rounded"


def test_math_max_and_a_mulDiv_rounded_down_are_operators_with_their_lemmas(build):
    model = extract(build, f"{ROUNDED}.share", {}).model
    assert sorted({e.op for e in walk(model.function) if isinstance(e, Op)}) == ["max", "mulDiv"]
    # Either value can be the larger.
    assert evaluate_vector(model, {"_x": 7, "_y": 3, "_d": 2}) == (False, 10)
    assert evaluate_vector(model, {"_x": 1, "_y": 3, "_d": 2}) == (False, 4)
    assert evaluate_vector(model, {"_x": 6, "_y": 3, "_d": 0}) == (True, 0)
    assert "Math.max(Math.mulDiv(i._x, i._y, i._d), Math.mulDiv(i._y, i._y, i._d))" in source(model)
    with pytest.raises(ExtractionError, match=r"Math.mulDiv with the rounding Math.Rounding.Ceil has no lemma"):
        extract(build, f"{ROUNDED}.roundedUp", {})
    with pytest.raises(ExtractionError, match=r"Math.mulDiv with the rounding _rounding has no lemma"):
        extract(build, f"{ROUNDED}.roundedBy", {})


def test_a_signed_parameter_that_only_an_effect_takes_is_no_input(build):
    model = extract(
        build, f"{ROUNDED}.swap", {}, {"POOL.exchange(0,_out,_dx,minDy)": (None, None, None, "minOut")}
    ).model
    assert [v.name for v in model.inputs] == ["_dx"]
    assert evaluate_vector(model, {"_dx": 1000}) == (False, (990, 990))
    assert evaluate_vector(model, {"_dx": 101}) == (False, (99, 99))
    with pytest.raises(ExtractionError, match=r"Rounded.sol:\d+:12: a signed integer"):
        extract(build, f"{ROUNDED}.signedValue", {})


BALANCES = "tests/fixtures/contracts/Balances.sol:Balances"


def test_the_balance_of_an_address_is_a_read_and_the_bounds_of_a_type_are_constants(build):
    model = extract(build, f"{BALANCES}.share", {"address(this).balance": "held"}).model
    assert [(r.source, r.var.type.solidity) for r in model.reads] == [("address(this).balance", "uint256")]
    assert evaluate_vector(model, {"_amount": 2**256 - 1, "held": 7}) == (False, 7)
    assert evaluate_vector(model, {"_amount": 9, "held": 7}) == (False, 2**128 - 1)
    text = source(model)
    assert "uint256 internal constant ALL = type(uint256).max;" in text
    assert ": type(uint128).max" in text
    who = extract(build, f"{BALANCES}.balanceOf", {"_who.balance": "held"}).model
    assert evaluate_vector(who, {"_who": 3, "held": 5}) == (False, 5)


def test_a_balance_after_an_external_call_or_of_a_local_that_changes_is_refused_and_an_interface_cannot_revert(build):
    with pytest.raises(ExtractionError, match=r"reads address\(this\).balance after an external call"):
        extract(build, f"{BALANCES}.balanceAfterACall", {"address(this).balance": "held"})
    with pytest.raises(
        ExtractionError,
        match=r"Balances.sol:\d+:20: the read who.balance names who, which an assignment changes, at a second place",
    ):
        extract(build, f"{BALANCES}.balancesOfTwo", {"who.balance": "held"})
    with pytest.raises(ExtractionError, match=r"calls pot, which reads a balance, after an external call"):
        extract(build, f"{BALANCES}.potAfterACall", {"address(this).balance": "held"})
    model = extract(build, f"{BALANCES}.approveAll", {}).model
    assert [(e.kind, e.place) for e in model.effects] == [("call", "_payer.approve(IERC20(_token),type(uint256).max)")]


BITS = "tests/fixtures/contracts/Bits.sol:Bits"


@pytest.mark.parametrize(
    "function, reads, values, result",
    [
        ("high", {"packed": "packed"}, {"packed": 5 * 2**128 + 7}, 5),
        ("low", {"packed": "packed"}, {"packed": 5 * 2**128 + 7}, 7),
        ("maskFirst", {}, {"_x": 0x1234}, 0x34),
        ("narrow", {}, {"_x": 3 * 2**64 + 1}, 3),
        ("fullMask", {}, {"_x": 2**256 - 5}, 2**256 - 5),
        ("addressWideMask", {}, {"_x": 2**200 + 9}, 9),
        ("wrappedNarrow", {}, {"_x": 7}, 3),
        ("wrappedLiteral", {}, {"_x": 255}, 256),
        ("constantMaskFirst", {}, {"_x": 1}, 0x1235),
    ],
)
def test_a_shift_and_a_mask_by_constants_are_a_division_and_a_remainder(build, function, reads, values, result):
    model = extract(build, f"{BITS}.{function}", reads).model
    assert evaluate(model, values) == (False, result, False)


@pytest.mark.parametrize(
    "function, message",
    [
        ("byParameter", r"shr by a value that is not a constant"),
        ("shiftedLeft", r"the operator <<"),
        ("notLowBits", r"mask with 0xf0, which is not a mask of low bits"),
        ("everyBitOut", r"shr by 256, which shifts every bit out"),
        ("userOperator", r"the user-defined operator \+"),
        ("valueTypeOverload", r"Math.min with 2 arguments has no lemma"),
        ("valueTypeRounded", r"Math.mulDiv with 4 arguments has no lemma"),
    ],
)
def test_a_shift_or_mask_that_is_not_by_a_constant_of_low_bits_is_refused(build, function, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{BITS}.{function}", {"packed": "packed"} if function == "userOperator" else {})


CEIL = "tests/fixtures/contracts/Ceil.sol:Ceil"


@pytest.mark.parametrize(
    "function, values, outcome",
    [
        ("half", {"_x": 5}, (False, 3, False)),
        ("ceil", {"_a": 0, "_b": 3}, (False, 0, False)),
        ("ceil", {"_a": 7, "_b": 3}, (False, 3, False)),
        ("ceil", {"_a": 0, "_b": 0}, True),
        ("olderCeil", {"_a": 0, "_b": 0}, (False, 0, False)),
        ("olderCeil", {"_a": 1, "_b": 0}, True),
    ],
)
def test_ceil_div_is_the_quotient_rounded_up_and_reverts_as_its_copy_does(build, function, values, outcome):
    model = extract(build, f"{CEIL}.{function}", {}).model
    if outcome is True:
        reverts = terms(model, {v.name: z3.IntVal(values[v.name]) for v in model.inputs}).reverts
        assert z3.is_true(z3.simplify(reverts))
    else:
        assert evaluate(model, values) == outcome


STRUCTS = "tests/fixtures/contracts/Structs.sol:Structs"


@pytest.mark.parametrize(
    "function, reads",
    [
        ("smashedThroughCallee", {"_smash()": "smash"}),
        ("smashedBefore", {"_smash()": "smash"}),
        ("smashedCommented", {"_commented()": "smash"}),
        ("externalSmashed", {"params.window().end": "end", "_smash()": "smash"}),
    ],
)
def test_a_cut_with_assembly_that_is_not_memory_safe_is_refused_wherever_it_runs(build, function, reads):
    with pytest.raises(ExtractionError, match=r"runs inline assembly that is not marked memory-safe"):
        extract(build, f"{STRUCTS}.{function}", reads)


def test_the_fields_of_a_zero_struct_are_zeros_that_solidity_takes(build):
    model = extract(build, f"{STRUCTS}.zeroOwner", {}).model
    assert evaluate(model, {"_x": 3}) == (False, 3, False)
    assert "address o_who = address(0);" in source(model)


def test_a_generated_local_with_the_name_of_a_function_is_refused(build):
    with pytest.raises(ExtractionError, match=r"the local q_start for a field of a memory struct has the name of a"):
        extract(build, f"{STRUCTS}.shadowsAFunction", {})


def test_an_alias_of_a_struct_parameter_reads_the_parameter_s_fields(build):
    model = extract(build, f"{STRUCTS}.aliasedParameter", {"_w.start": "start"}).model
    assert evaluate(model, {"start": 2, "_x": 1}) == (False, 5, False)


@pytest.mark.parametrize(
    "function, reads, values, result",
    [
        ("built", {}, {"_t": 5}, 1),
        ("aliased", {}, {"_a": 2, "_b": 3}, 5),
        ("zero", {}, {"_x": 4}, 4),
        ("copyAliased", {"config.start": "start", "config.end": "end"}, {"start": 1, "end": 7}, 6),
    ],
)
def test_a_memory_struct_that_the_function_builds_is_a_local_for_each_field(build, function, reads, values, result):
    model = extract(build, f"{STRUCTS}.{function}", reads).model
    assert evaluate(model, values) == (False, result, False)


def test_the_fields_of_a_built_struct_are_locals_that_theorems_can_name(build):
    model = extract(build, f"{STRUCTS}.aliased", {}).model
    assert [v.name for v in declared_locals(model.function.body)] == ["w_start", "w_end"]


@pytest.mark.parametrize(
    "function, reads, message",
    [
        (
            "copyAliasedAfterWrite",
            {"config.end": "end"},
            r"reads config.end after a write to its memory copy",
        ),
        ("fieldWritten", {}, r"a write to the memory struct w: memory structs alias"),
        ("nested", {}, r"the memory struct Nested has the field window of type struct Window"),
        ("clash", {}, r"two locals are named w_end"),
        ("quotedTwice", {"feed.quote(w.end)": "quote"}, r"_quoted runs 2 times in the call"),
        ("smashed", {"_smash()": "smash"}, r"runs inline assembly that is not marked memory-safe"),
    ],
)
def test_a_memory_struct_that_an_alias_or_assembly_could_change_unseen_is_refused(build, function, reads, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{STRUCTS}.{function}", reads)


def test_a_cut_whose_assembly_is_memory_safe_is_taken_beside_a_memory_struct(build):
    model = extract(build, f"{STRUCTS}.safe", {"_safe()": "cut"}).model
    assert [r.source for r in model.reads] == ["_safe()"]


CONFIG = {"config.start": "start", "config.end": "end"}


@pytest.mark.parametrize(
    "function, reads, values, result",
    [
        ("openedBuilt", {}, {"_s": 3, "_t": 10}, 7),
        ("openedParameter", {"_w.start": "start", "_w.end": "end"}, {"start": 3, "end": 9, "_t": 2}, 0),
        ("openedConfig", CONFIG, {"start": 3, "end": 9, "_t": 5}, 2),
        ("openedCopy", CONFIG, {"start": 3, "end": 9, "_t": 5}, 11),
        ("openedConstructed", {}, {"_s": 4, "_t": 9}, 5),
    ],
)
def test_a_struct_passed_to_a_callee_is_a_value_for_each_field(build, function, reads, values, result):
    model = extract(build, f"{STRUCTS}.{function}", reads).model
    assert evaluate(model, values) == (False, result, False)
    (opened,) = model.callees
    assert [p.name for p in opened.params] == ["_w_start", "_w_end", "_t"]


@pytest.mark.parametrize(
    "function, reads, message",
    [
        ("openedParameter", {"_w.start": "start"}, r"reads a field of the struct parameter _w.end, which the read map"),
        ("writesParameter", {}, r"a write to the memory struct _w: memory structs alias"),
        ("quotedParameterTwice", {"feed.quote(_w.end)": "quote"}, r"_quotedParameter runs 2 times in the call"),
    ],
)
def test_a_struct_parameter_of_a_callee_that_the_model_could_get_wrong_is_refused(build, function, reads, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{STRUCTS}.{function}", reads)


START = {"config.start": "start"}


@pytest.mark.parametrize(
    "function, reads, values, result",
    [
        ("fromInternal", START, {"start": 3, "_t": 12}, 0),
        ("fromInternal", START, {"start": 3, "_t": 13}, 1),
        ("windowEnd", START, {"start": 3, "_x": 1}, 14),
        ("owed", CONFIG, {"start": 3, "end": 10, "_t": 5}, 8),
        ("nestedReturn", START, {"start": 3, "_t": 1}, 14),
        ("copyReturned", CONFIG, {"start": 3, "end": 9, "_t": 1}, 4),
    ],
)
def test_a_struct_that_an_internal_call_returns_is_its_fields(build, function, reads, values, result):
    model = extract(build, f"{STRUCTS}.{function}", reads).model
    assert evaluate(model, values) == (False, result, False)


def test_a_field_of_a_constructed_struct_keeps_the_reverts_of_its_other_fields(build):
    model = extract(build, f"{STRUCTS}.projected", {}).model
    assert evaluate(model, {"_x": 3, "_y": 1}) == (False, 3, False)
    assert evaluate(model, {"_x": 3, "_y": 0})[0] is True


@pytest.mark.parametrize("x, result", [(0, 0), (4, 5)])
def test_a_named_struct_return_starts_at_zero(build, x, result):
    model = extract(build, f"{STRUCTS}.namedWindowEnd", {}).model
    assert evaluate(model, {"_x": x}) == (False, result, False)


def test_a_one_field_struct_is_one_value_and_a_target_returns_a_struct_as_its_fields(build):
    one = extract(build, f"{STRUCTS}.oneField", {}).model
    assert evaluate(one, {"_x": 4}) == (False, 5, False)
    window = extract(build, f"{STRUCTS}.window", {"config.start": "start"}).model
    assert window.function.returns.solidity == "uint256, uint256"


def test_a_struct_valued_call_in_an_expression_is_unpacked_just_before_its_statement(build):
    model = extract(build, f"{STRUCTS}.windowEnd", {"config.start": "start"}).model
    first, second = model.function.body
    assert type(first).__name__ == "Unpack" and [v.name for v in first.vars] == ["window_start", "window_end"]
    assert type(second).__name__ == "Return"


@pytest.mark.parametrize(
    "function, reads, message",
    [
        ("returnsParameter", {}, r"returns its struct parameter _w, or an alias of it"),
        ("guardedCall", START, r"the struct of window\(\), under &&, \|\| or \?:"),
    ],
)
def test_a_returned_struct_that_the_model_could_get_wrong_is_refused(build, function, reads, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{STRUCTS}.{function}", reads)


PAYABLE = "tests/fixtures/contracts/Payable.sol:Payable"


@pytest.mark.parametrize(
    "function, reads",
    [
        ("paidToPayee", {"payee.to": "to", "payee.cap": "cap"}),
        ("paidToExternal", {"payees.payee().to": "to"}),
    ],
)
def test_an_address_payable_field_of_a_struct_is_declared_payable(build, function, reads):
    """A field that a callee reads of a storage struct it takes, or of a struct from an external call, is an input
    that the link test declares `address payable`, as for a read of the field in an expression."""
    model = extract(build, f"{PAYABLE}.{function}", reads).model
    assert model.reads[0].var.declared == "address payable"
    assert [r.var.declared for r in model.reads[1:]] == [""] * (len(reads) - 1)


EXTERNAL_WINDOW = {"params.window().start": "start", "params.window().end": "end"}


@pytest.mark.parametrize(
    "function, reads, values, result",
    [
        ("fromExternal", {"params.window().end": "end"}, {"end": 5, "_t": 4}, 0),
        ("externalPassed", EXTERNAL_WINDOW, {"start": 2, "end": 5, "_t": 4}, 2),
        ("externalPart", {"params.triple()[1]": "second"}, {"second": 7, "_x": 1}, 8),
    ],
)
def test_a_struct_field_or_a_tuple_value_of_an_external_call_is_a_read(build, function, reads, values, result):
    model = extract(build, f"{STRUCTS}.{function}", reads).model
    assert [r.source for r in model.reads] == list(reads)
    assert evaluate(model, values) == (False, result, False)


@pytest.mark.parametrize(
    "function, reads, message",
    [
        (
            "fromExternal",
            {},
            r"reads a value of an external call params.window\(\).end, which the read map does not name",
        ),
        ("externalPart", {}, r"reads a value of an external call params.triple\(\)\[1\]"),
        ("externalWritten", {"params.window().end": "end"}, r"a write to w, which is not a field of a memory copy"),
    ],
)
def test_a_value_of_an_external_call_needs_its_key_and_stays_as_read(build, function, reads, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{STRUCTS}.{function}", reads)


@pytest.mark.parametrize(
    "function, reads",
    [
        ("openedConstructed", {"Windows.opened(Window(_s,_s),_t)": "opened"}),
        ("owed", {"config.end": "end", "window().opened(_t)": "opened"}),
        ("cutNewArray", {"_length(new uint256[](_n))": "opened"}),
        ("cutFactoryOfAFunction", {"Windows.opened(_made(_startOf),_x)": "opened"}),
    ],
)
def test_a_cut_may_take_new_memory_that_nothing_reaches_after_the_call(build, function, reads):
    model = extract(build, f"{STRUCTS}.{function}", reads).model
    assert "opened" in [r.var.name for r in model.reads]
    assert not model.callees


@pytest.mark.parametrize(
    "function, reads, message",
    [
        ("openedBuilt", {"w.opened(_t)": "opened"}, r"passes memory to opened, which can change it"),
        ("cutNested", {"_nestedStart(Nested(held))": "start"}, r"passes memory to _nestedStart, which can change it"),
        ("cutFake", {"Windows.opened(_fakeWindow(),_t)": "opened"}, r"passes memory to opened, which can change it"),
        (
            "cutSmashed",
            {"_smashWindow(Window(_x,_x))": "smashed"},
            r"passes memory to _smashWindow, and runs inline assembly",
        ),
        ("cutSmashedNext", {"_smashNext(Window(0,0))": "smashed"}, r"passes memory to _smashNext, and runs inline"),
    ],
)
def test_a_cut_whose_memory_argument_could_be_reached_or_written_unseen_is_refused(build, function, reads, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{STRUCTS}.{function}", reads)


KEYS = "tests/fixtures/contracts/Keys.sol:Keys"


@pytest.mark.parametrize(
    "function, values, result",
    [
        ("quoted", {"_who": 1, "_byShares": 0, "_byAmount": 3, "quote": 7}, 7),
        ("quotedThenAssigned", {"_who": 1, "_a": 3, "quote": 7}, 11),
    ],
)
def test_a_key_on_an_assigned_local_with_one_place_is_the_value_there(build, function, values, result):
    model = extract(build, f"{KEYS}.{function}", {"gate.quote(_who,amount)": "quote"}).model
    assert evaluate(model, values) == (False, result, False)


def test_two_fields_of_one_external_call_text_at_two_places_are_each_the_value_there(build):
    model = extract(build, f"{KEYS}.twoFields", {"pairs.get(n).x": "x", "pairs.get(n).y": "y"}).model
    assert evaluate(model, {"x": 3, "y": 4}) == (False, 7, False)


@pytest.mark.parametrize(
    "function, message",
    [
        ("quotedTwice", r"names amount, which an assignment changes, at a second place"),
        ("quotedInACalleeTwice", r"_quoteOf runs 2 times in the call"),
        ("fieldThenDirect", r"the read pairs.get\(n\).x names n, which an assignment changes, at a second place"),
    ],
)
def test_a_key_on_an_assigned_local_at_two_places_or_runs_is_refused(build, function, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{KEYS}.{function}", {"gate.quote(_who,amount)": "quote", "pairs.get(n).x": "x"})


ALLOWED = "gate.allowed(_who,_amount)"


def test_a_call_that_is_not_a_view_may_be_assigned_and_leaves_immutables_as_they_are(build):
    assigned = extract(build, f"{KEYS}.assigned", {ALLOWED: "answer"}).model
    assert evaluate(assigned, {"_who": 1, "_amount": 2, "answer": 5}) == (False, 5, False)
    around = extract(build, f"{KEYS}.immutableAround", {ALLOWED: "answer", "START": "start"}).model
    assert evaluate(around, {"_who": 1, "_amount": 2, "answer": 5, "start": 7}) == (False, 19, False)


@pytest.mark.parametrize(
    "function, values, result",
    [
        ("take", {"_who": 1, "_amount": 5, "answer": 0}, (False, 5, False)),
        ("takeByShares", {"_who": 1, "_byShares": 2, "_byAmount": 9, "answer": 0}, (False, 4, False)),
    ],
)
def test_the_result_of_a_call_that_is_not_a_view_is_an_input(build, function, values, result):
    reads = {"gate.allowed(_who,_amount)" if function == "take" else "gate.allowed(_who,amount)": "answer"}
    model = extract(build, f"{KEYS}.{function}", reads).model
    assert evaluate(model, values) == result
    assert evaluate(model, {**values, "answer": 1})[0] is True
    assert [e.kind for e in model.effects] == ["read"]


@pytest.mark.parametrize(
    "function, reads, message",
    [
        ("takeThenRead", {"gate.allowed(_who,_amount)": "ok", "total": "total"}, r"reads total after an external call"),
        ("takeInAnExpression", {"gate.allowed(_who,_amount)": "answer"}, r"must be the whole value of its statement"),
        (
            "takeTwice",
            {"gate.allowed(_who,_amount)": "answer"},
            r"can change state: a call that can change state, at a",
        ),
        (
            "takeAfterAWrite",
            {"gate.allowed(_who,_amount)": "answer"},
            r"follows a write, and its call could read the change",
        ),
        ("bumped", {"_bump()": "b"}, r"calls _bump, which can change state: an internal call that can change state"),
        ("selfCall", {"this.bumpItself()": "answer"}, r"a call to the contract itself, which can change its storage"),
        ("libraryCall", {"Counter.bump(box)": "answer"}, r"a library call, which runs in the contract's own storage"),
        ("selfCallStatement", {}, r"a call to the contract itself, this.bumpItself\(\), which can change its storage"),
        (
            "balanceAfter",
            {ALLOWED: "answer", "address(gate).balance+0": "cash"},
            r"reads address\(gate\).balance after an external call",
        ),
    ],
)
def test_a_call_that_is_not_a_view_whose_result_could_be_two_values_or_hide_a_change_is_refused(
    build, function, reads, message
):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{KEYS}.{function}", reads)


CHECKS = "tests/fixtures/contracts/Checks.sol:Checks"


@pytest.mark.parametrize(
    "function, reads, values, reverts",
    [
        ("checked", {}, {"_to": 0, "_a": 3}, True),
        ("checked", {}, {"_to": 1, "_a": 3}, False),
        ("checkedTwice", {}, {"_to": 1, "_from": 0, "_a": 3}, True),
        ("ranged", {}, {"_a": 3, "_b": 2}, True),
        ("ranged", {}, {"_a": 2, "_b": 3}, False),
        ("rangedConstructed", {}, {"_a": 3, "_b": 2}, True),
        (
            "rangedFromOutside",
            {"quotes.range().low": "low", "quotes.range().high": "high"},
            {"low": 3, "high": 2},
            True,
        ),
        (
            "owned",
            {"owner": "owner", "msg.sender": "caller", "total": "total"},
            {"owner": 1, "caller": 2, "total": 0, "_a": 1},
            True,
        ),
        (
            "owned",
            {"owner": "owner", "msg.sender": "caller", "total": "total"},
            {"owner": 2, "caller": 2, "total": 0, "_a": 1},
            False,
        ),
    ],
)
def test_a_check_called_as_a_statement_is_inlined_as_its_requires(build, function, reads, values, reverts):
    model = extract(build, f"{CHECKS}.{function}", reads).model
    assert not model.callees
    assert evaluate(model, values)[0] is reverts


def test_a_check_whose_argument_emits_is_inlined_and_the_event_is_the_caller_s(build):
    model = extract(build, f"{CHECKS}.loggedCheck", {}).model
    assert [e.kind for e in model.effects] == ["event"]
    assert evaluate(model, {"_x": 0})[0] is True
    assert evaluate(model, {"_x": 3}) == (False, 3, False)


@pytest.mark.parametrize(
    "function, reads, message",
    [
        ("early", {}, r"_early, which a statement calls, returns early"),
        ("writes", {}, r"_write, which a statement calls, writes storage, emits or calls out"),
        ("emits", {}, r"_emitted, which a statement calls, writes storage, emits or calls out"),
        ("keyedTwice", {"quotes.quote(_n)": "quote"}, r"_keyed runs 2 times in the call"),
        ("guardedInACheck", {}, r"the struct of _range\(_x\), under &&, \|\| or \?:"),
    ],
)
def test_a_statement_call_that_returns_early_writes_or_runs_a_key_twice_is_refused(build, function, reads, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{CHECKS}.{function}", reads)


def test_a_statement_call_of_a_public_library_function_is_refused_as_it_runs_by_delegatecall(build):
    with pytest.raises(ExtractionError, match=r"a call to requirePositive as a statement"):
        extract(build, "tests/fixtures/contracts/Checks.sol:DelegatedChecks.delegated", {})


GATED = "tests/fixtures/contracts/Gated.sol:Gated"
KEEPER = {"keeper": "keeper", "msg.sender": "caller", "total": "total"}


@pytest.mark.parametrize(
    "function, reads, values, reverts",
    [
        ("keeperAdd", KEEPER, {"keeper": 1, "caller": 2, "total": 0, "_a": 1}, True),
        ("keeperAdd", KEEPER, {"keeper": 2, "caller": 2, "total": 0, "_a": 1}, False),
        (
            "ownerAdd",
            {"_owner": "owner", "msg.sender": "caller", "total": "total"},
            {"owner": 1, "caller": 2, "total": 0, "_a": 1},
            True,
        ),
        (
            "ownerAdd",
            {"_owner": "owner", "msg.sender": "caller", "total": "total"},
            {"owner": 2, "caller": 2, "total": 0, "_a": 1},
            False,
        ),
        ("bounded", {"keeper": "keeper", "msg.sender": "caller"}, {"keeper": 2, "caller": 2, "_a": 9}, True),
        ("bounded", {"keeper": "keeper", "msg.sender": "caller"}, {"keeper": 2, "caller": 2, "_a": 11}, False),
        ("boundedByCall", {}, {"_a": 4}, True),
        ("boundedByCall", {}, {"_a": 5}, False),
    ],
)
def test_the_modifiers_of_the_target_run_before_its_body(build, function, reads, values, reverts):
    model = extract(build, f"{GATED}.{function}", reads).model
    assert evaluate(model, values)[0] is reverts


@pytest.mark.parametrize(
    "function, reads, message",
    [
        ("resetting", {}, r"the modifier resetAfter runs code after `_;`"),
        ("runTwice", {}, r"the modifier twice runs code after `_;`, or has no single `_;`"),
        ("quoted", {"quotes.quote(n)": "quote"}, r"names n, which an assignment changes, at a second place"),
        ("gated", {"quotes.quote(_n)": "quote"}, r"_get runs 2 times in the call"),
        ("keeperTwice", {"keeper": "keeper", "msg.sender": "caller"}, r"the modifier onlyKeeper is invoked twice"),
        ("boundedIf", {}, r"the struct of _bound\(5\), under &&, \|\| or \?:"),
    ],
)
def test_a_modifier_that_runs_code_after_its_body_or_reads_a_key_twice_is_refused(build, function, reads, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{GATED}.{function}", reads)


KEPT = "tests/fixtures/contracts/Kept.sol:Kept"


@pytest.mark.parametrize(
    "function, reads, values, reverts",
    [
        ("kept", {"keeper": "keeper", "msg.sender": "caller"}, {"keeper": 1, "caller": 2, "_a": 3}, True),
        ("kept", {"keeper": "keeper", "msg.sender": "caller"}, {"keeper": 2, "caller": 2, "_a": 3}, False),
        ("kept", {"currentKeeper()": "keeper", "msg.sender": "caller"}, {"keeper": 2, "caller": 2, "_a": 3}, False),
        ("sameArgument", {}, {"_n": 1}, False),
    ],
)
def test_a_call_in_an_error_argument_that_the_condition_makes_too_is_left_out(build, function, reads, values, reverts):
    model = extract(build, f"{KEPT}.{function}", reads).model
    assert evaluate(model, values)[0] is reverts


@pytest.mark.parametrize(
    "function",
    ["skipped", "otherArgument"],
)
def test_a_call_in_an_error_argument_that_the_condition_may_not_make_is_refused(build, function):
    with pytest.raises(ExtractionError, match=r"which solc evaluates whether or not the condition holds"):
        extract(build, f"{KEPT}.{function}", {})
