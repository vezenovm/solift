import pathlib

import pytest

from examples.quoter.spec import READS as QUOTER_READS
from solift import forge
from solift.discover import _propose, as_json, discover, report, skeleton
from solift.extract import ExtractionError, extract

ROOT = pathlib.Path(__file__).resolve().parent.parent
QUOTE = "tests/fixtures/contracts/Quoter.sol:Quoter._quote"


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    work = tmp_path_factory.mktemp("forge")
    return forge.build(ROOT, work / "out", work / "cache")


def test_the_reads_of_the_quote_are_the_read_map_of_its_example(build):
    found = discover(build, QUOTE)
    assert found.complete and found.diagnostic == ""
    assert set(found.read_map) == set(QUOTER_READS)
    assert extract(build, QUOTE, found.read_map).model.name == "Quoter._quote"
    assert found.cuts == ["_withMargin"]
    assert [m.what for m, _ in found.reads][:2] == ["an environment field", "an environment field"]


def test_a_mapping_entry_and_a_struct_parameter_field_are_reads_and_effects_are_listed(build):
    writes = discover(build, "tests/fixtures/contracts/Writes.sol:Writes.updateThenRead")
    assert writes.complete
    assert writes.read_map == {"$info.rate": "infoRate", "$balances[_who]": "balances"}
    assert [(e.kind, e.place) for e in writes.effects] == [
        ("copy", "info.lastTimestamp"),
        ("copy", "info.rate"),
        ("write", "$info"),
        ("event", "Updated(uint256)"),
    ]
    priced = discover(build, "tests/fixtures/contracts/Pricer.sol:Pricer._priced")
    assert priced.complete
    assert {"_meter.used", "_meter.paid", "_meter.fresh", "TOKEN", "TOKEN.balanceOf(tx.origin)"} == set(priced.read_map)


def test_a_construct_that_the_extractor_refuses_ends_the_discovery(build):
    found = discover(build, "tests/fixtures/contracts/OutOfScope.sol:OutOfScope.loop")
    assert not found.complete
    assert found.diagnostic == "tests/fixtures/contracts/OutOfScope.sol:12:5: a loop"
    assert "incomplete: tests/fixtures/contracts/OutOfScope.sol:12:5: a loop" in report(found)


def test_a_target_in_a_file_that_the_build_lacks_ends_the_discovery_with_the_path(build):
    found = discover(build, "tests/fixtures/contracts/Quoterr.sol:Quoter._quote")
    assert not found.complete
    assert found.diagnostic.startswith("the build has no source tests/fixtures/contracts/Quoterr.sol: check the path")
    assert report(found) == f"== {found.target}: 0 reads, incomplete: {found.diagnostic}"
    with pytest.raises(ExtractionError, match="the build has no source tests/fixtures/contracts/Quoterr.sol"):
        extract(build, "tests/fixtures/contracts/Quoterr.sol:Quoter._quote", {})
    assert discover(build, "Quoter._quote").diagnostic == "Quoter._quote names no function as path:Contract.function"


def test_the_skeleton_sets_an_environment_field_with_its_cheatcode_and_observes_the_gas_price(build):
    found = discover(build, QUOTE)
    text = skeleton(found, "_quote")
    assert "'blockBasefee': 'vm.fee(blockBasefee);'," in text
    assert "'txGasprice': 'tx.gasprice'," in text
    assert "'margin': '/* set margin, which the target reads as $config.margin */'," in text
    assert "runs _quote>(_gas, _price)" in text
    data = as_json(found)
    assert data["complete"] and data["cuts"] == ["_withMargin"]
    assert {r["key"]: r["type"] for r in data["reads"]}["$config.cap"] == "uint128"


def test_a_proposed_name_is_the_last_name_of_the_read_and_avoids_the_names_in_use():
    assert _propose("$config.cap", set()) == "cap"
    assert _propose("$info.rate", {"rate"}) == "infoRate"
    assert _propose("$balances[_who]", set()) == "balances"
    assert _propose("position[id][borrower].collateral", {"collateral"}) == "positionCollateral"
    assert _propose("TOKEN.balanceOf(address(this))", set()) == "balanceOf"
    assert _propose("TOKEN", set()) == "token"
    assert _propose("block.timestamp", set()) == "blockTimestamp"
    assert _propose("block.timestamp", {"blockTimestamp"}) == "blockTimestamp2"


def test_the_given_reads_stay_in_the_read_map_and_a_reserved_word_is_never_a_name(build):
    found = discover(build, QUOTE, reads=QUOTER_READS)
    assert found.complete and found.reads == []
    assert found.read_map == QUOTER_READS
    assert [r["what"] for r in as_json(found)["reads"]] == ["given"] * 6
    assert "'margin': '/* set margin, which the target reads as $config.margin */'," in skeleton(found, "_quote")
    assert "'txGasPrice': 'tx.gasprice'," in skeleton(found, "_quote")
    assert _propose("__type", set()) == "type_"
    assert _propose("$config.uint", set()) == "uint_"
    assert _propose("$config.block", set()) == "block_"
    assert _propose("__1234", set()) == "v1234"
    assert _propose("$config.error", set()) == "error"
    assert extract(build, QUOTE, {**QUOTER_READS, "$floor": "error"}).model.reads[2].var.name == "error"
    with pytest.raises(ExtractionError, match="the input name type of \\$floor is a word that Solidity reserves"):
        extract(build, QUOTE, {**QUOTER_READS, "$floor": "type"})


EFFECTS = "tests/fixtures/contracts/Effects.sol:Effects"


def test_a_function_without_a_value_gets_its_call_arguments_as_outputs(build):
    found = discover(build, f"{EFFECTS}.buy")
    assert found.complete
    assert found.outputs == {"SWAPPER.buy(msg.sender,amount)": (None, "amount2")}
    assert found.read_map == {"SWAPPER.fee()": "fee"}
    text = report(found)
    assert "outputs={\n    'SWAPPER.buy(msg.sender,amount)': (None, 'amount2'),  # an output\n}," in text
    assert "        'amount2': '/* read amount2 after the call */'," in text
    assert as_json(found)["outputs"] == [{"key": "SWAPPER.buy(msg.sender,amount)", "names": (None, "amount2")}]


def test_a_write_that_can_revert_is_an_output_and_one_on_some_paths_gets_its_value_before_the_call(build):
    pull = discover(build, f"{EFFECTS}.pull")
    assert pull.complete
    assert set(pull.outputs) == {"$allowances[msg.sender].left", "$allowances[msg.sender].last"}
    add = discover(build, f"{EFFECTS}.add")
    assert add.complete
    assert (add.outputs, add.read_map) == ({"$total": "total"}, {"$total": "totalBefore"})
    nothing = discover(build, f"{EFFECTS}.nothing")
    assert not nothing.complete and "returns no value" in nothing.diagnostic
    assert [e.kind for e in nothing.effects] == ["event"]
