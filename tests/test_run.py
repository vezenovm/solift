import dataclasses

import z3

from examples.pricer.spec import SPECS as PRICER_SPECS
from examples.quoter.spec import QUOTER, SPEC
from solift import forge
from solift.extract import extract
from solift.link import Link, generate
from solift.run import Spec, run, summary, vector_failures
from solift.workspace import Workspace


def test_a_run_proves_vectors_and_links_the_quoter_and_records_each_version(tmp_path):
    record = run(SPEC, Workspace(tmp_path / "work"))
    assert record["ok"], summary(record)
    (spec,) = record["specs"]
    assert {t["id"]: t["verdict"] for t in spec["theorems"]} == {
        "quote-at-most-gas-plus-margin": "PROVED",
        "quote-at-most-gas": "COUNTEREXAMPLE",
    }
    assert spec["vectors"]["agree"] == spec["vectors"]["count"] > 100
    assert {r["verdict"] for r in spec["link"]} == {"PASS"}
    assert len(spec["link"]) == 8
    assert spec["versions"]["solc"].startswith("0.8.30+commit.")
    assert spec["versions"]["OpenZeppelin"]["git blob"] == "cd55ff13085414af44d7bd049a19847d8f614a48"
    assert {"halmos", "yices-solver", "cvc5", "z3 (theorems)", "forge", "python", "solift commit"} <= set(
        record["versions"]
    )
    assert QUOTER in spec["sources"]
    assert record["schema"] == 3


def test_the_record_holds_the_scope_of_each_theorem_and_what_the_results_rest_on(tmp_path):
    record = run(SPEC, Workspace(tmp_path / "work"))
    (spec,) = record["specs"]
    for t in spec["theorems"]:
        assert (t["link"], t["lemma_domain"], t["callees"]) == ("passed", "established", [])
        assert len(t["query_sha256"]) == 64
    assert spec["vectors"]["outcomes"] == {"pays": "reached", "zero": "reached", "reverts": "reached"}
    assert record["build"]["inputs"] and all(len(h) == 64 for h in record["build"]["inputs"])
    assert {"examples/quoter/spec.py", "examples/__init__.py"} <= set(record["spec files"])
    assert record["engine"] == {"name": "halmos", "options": {"solver": "yices", "loop": 4, "extra_args": []}}
    assert record["project"]["profile"] is None and record["project"]["added"] == {}
    unlinked = run(SPEC, Workspace(tmp_path / "unlinked"), link=False, vectors=False)
    assert {t["link"] for t in unlinked["specs"][0]["theorems"]} == {"not-run"}


def test_an_error_in_a_theorem_fails_that_theorem_and_the_run_goes_on(tmp_path):
    def typo(q):
        """a theorem that names an input the model does not have"""
        return q.result <= q.margn

    broken = SPEC.theorem(witness=lambda q: q.result > 0)(typo)
    try:
        record = run(SPEC, Workspace(tmp_path / "work"), link=False, vectors=False)
    finally:
        SPEC.theorems.remove(broken)
    assert record["failures"] == [
        "quoter: theorem typo: AttributeError: Quoter._quote has no input, local or free variable margn"
    ]
    assert [t["id"] for t in record["specs"][0]["theorems"]] == ["quote-at-most-gas-plus-margin", "quote-at-most-gas"]
    (failure,) = record["tracebacks"]
    assert failure["stage"] == "theorem typo" and "AttributeError" in failure["traceback"]


def test_a_mutant_that_breaks_a_theorem_fails_the_run_and_its_link_still_passes(tmp_path):
    source = (SPEC.project.root / QUOTER).read_text()
    mutated = source.replace("return _cost + _margin;", "return _cost + _margin + _margin;")
    record = run(SPEC, Workspace(tmp_path / "work"), replace={QUOTER: mutated})
    assert not record["ok"]
    assert record["failures"] == ["quoter: theorem quote-at-most-gas-plus-margin: COUNTEREXAMPLE, expected PROVED"]
    assert {r["verdict"] for r in record["specs"][0]["link"]} == {"PASS"}
    assert record["replaced"] == [QUOTER]


def test_an_extraction_error_fails_the_run(tmp_path):
    spec = dataclasses.replace(SPEC, reads={k: v for k, v in SPEC.reads.items() if k != "tx.gasprice"})
    record = run(spec, Workspace(tmp_path / "work"), link=False)
    assert not record["ok"]
    assert "reads an environment field tx.gasprice, which the read map does not name" in record["failures"][0]


def test_the_committed_generated_files_of_the_example_are_current(tmp_path):
    """The example commits its model and link tests, so a review shows a change to the formula. Regenerate them with
    `python -m examples.quoter --emit examples/quoter/generated`."""
    workspace = Workspace(tmp_path / "work")
    root = SPEC.project.overlay(workspace)
    infos = forge.build(root, workspace.fresh("out"), workspace.fresh("cache"))
    generated = generate(extract(infos, SPEC.target, SPEC.reads).model, SPEC.link)
    committed = SPEC.project.root / "examples" / "quoter" / "generated"
    assert sorted(p.name for p in committed.iterdir()) == sorted(generated.files)
    for name, text in generated.files.items():
        assert (committed / name).read_text() == text, f"{name} is stale"


def test_one_run_of_several_specs_links_every_model_in_one_halmos_run_with_one_operators_test(tmp_path):
    record = run([SPEC, *PRICER_SPECS], Workspace(tmp_path / "work"))
    assert record["ok"], summary(record)
    assert [s["spec"] for s in record["specs"]] == ["quoter", "pricer-priced", "pricer-quote-for"]
    for spec in record["specs"]:
        assert [r["check"] for r in spec["link"]] == ["check_link"]
        assert all(t["verdict"] == "PROVED" or t["expected"] == "counterexample" for t in spec["theorems"])
        assert spec["vectors"]["agree"] == spec["vectors"]["count"] > 0
    assert {r["contract"] for r in record["link"]["shared"]} == {"SoliftOperators"}
    assert len(record["link"]["shared"]) == 8
    assert "SoliftOperators.t.sol" in record["generated"]


def test_two_specs_whose_generated_contracts_have_the_same_names_fail_the_run(tmp_path):
    twin = dataclasses.replace(SPEC, name="quoter-twin", theorems=[])
    record = run([SPEC, twin], Workspace(tmp_path / "work"), link=False, vectors=False)
    assert record["failures"] == ["quoter-twin: extract: QuoterQuote names the generated contracts of quoter too"]
    assert dataclasses.replace(twin, stem="QuoterTwin").stem == "QuoterTwin"


def test_the_vectors_of_a_model_are_exactly_its_own_tests_in_its_own_file():
    path = "test/solift/QVectors.t.sol"
    mine = {(path, "QVectors", f"test_vector_{n:03d}"): "Success" for n in range(2)}
    assert vector_failures("QVectors", path, 2, mine) == []
    old = {("old/QVectors.t.sol", "QVectors", f"test_old_{n}"): "Success" for n in range(2)}
    assert vector_failures("QVectors", path, 2, old) == [
        "vectors: the results come from old/QVectors.t.sol, not test/solift/QVectors.t.sol",
        "vectors: 0 of 2 agree with the emitted Solidity",
    ]


def test_a_vector_build_that_fails_is_a_failure_and_the_run_goes_on(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise forge.BuildError("forge test failed: Compiler run failed")

    monkeypatch.setattr(forge, "test", broken)
    record = run(SPEC, Workspace(tmp_path / "work"), link=False)
    assert record["failures"] == ["quoter: vectors: forge test failed: Compiler run failed"]
    assert record["specs"][0]["vectors"] is None
    assert "guard" in record["times"] and record["specs"][0]["theorems"]


def test_a_function_that_writes_storage_links_to_its_model_and_its_vectors_agree(tmp_path):
    spec = Spec(
        name="writes",
        target="tests/fixtures/contracts/Writes.sol:Writes.updateThenRead",
        reads={"$info.rate": "stored", "$balances[_who]": "balance"},
        link=Link(
            setup="tests/fixtures/test/WritesSetup.sol:WritesSetup",
            call="writes.updateThenRead(_price, _who)",
            adapters={("stored", "balance"): "writes.set(stored, _who, balance);"},
        ),
        project=SPEC.project,
    )
    record = run(spec, Workspace(tmp_path / "work"))
    assert record["ok"], summary(record)
    (result,) = record["specs"]
    assert {r["verdict"] for r in result["link"]} == {"PASS"}
    assert result["vectors"]["agree"] == result["vectors"]["count"] > 0
    assert len(result["model"]["effects"]) == 4


def test_a_function_of_several_returns_links_value_by_value_and_a_theorem_reads_each_value(tmp_path):
    setup = "tests/fixtures/test/TuplesSetup.sol:TuplesSetup"
    split = Spec(
        name="tuples-split",
        target="tests/fixtures/contracts/Tuples.sol:Tuples.split",
        reads={"$cap": "cap"},
        link=Link(setup=setup, call="tuples.split(_a)", adapters={"cap": "tuples.setCap(cap);"}),
        project=SPEC.project,
    )

    @split.theorem(assume=("no-revert",), witness=lambda q: q.result[1])
    def over_the_cap_pays_the_excess(q):
        """Above the cap, the first value is the excess and the second is true."""
        return z3.Implies(q._a > q.cap, z3.And(q.result[0] == q._a - q.cap, q.result[1]))

    pullable = Spec(
        name="tuples-pullable",
        target="tests/fixtures/contracts/Tuples.sol:Tuples.pullable",
        reads={},
        link=Link(setup=setup, call="tuples.pullable(_a, _b)"),
        project=SPEC.project,
    )
    record = run([split, pullable], Workspace(tmp_path / "work"))
    assert record["ok"], summary(record)
    for result in record["specs"]:
        assert {r["verdict"] for r in result["link"]} == {"PASS"}
        assert result["vectors"]["agree"] == result["vectors"]["count"] > 0
    assert record["specs"][0]["theorems"][0]["verdict"] == "PROVED"


def test_a_storage_pointer_passed_to_callees_links_to_its_model(tmp_path):
    spec = Spec(
        name="pointers-available",
        target="tests/fixtures/contracts/Pointers.sol:Pointers.available",
        reads={
            "$spenders[_who].config.activeUntil": "activeUntil",
            "block.timestamp": "now",
            "$spenders[_who].last": "last",
            "$spenders[_who].config.perPeriod": "perPeriod",
            "$spenders[_who].left": "left",
            "$main.left": "mainLeft",
        },
        link=Link(
            setup="tests/fixtures/test/PointersSetup.sol:PointersSetup",
            call="pointers.available(_who)",
            adapters={
                ("activeUntil", "perPeriod", "left", "last"): "pointers.set(_who, activeUntil, perPeriod, left, last);",
                "mainLeft": "pointers.setMain(mainLeft);",
                "now": "vm.warp(now);",
            },
        ),
        project=SPEC.project,
    )

    @spec.theorem(witness=lambda q: q.result > 0)
    def available_at_most_the_main_allowance(q):
        """What a spender can pull is at most the main allowance that is left."""
        return z3.Or(q.reverts, q.result <= q.mainLeft)

    record = run(spec, Workspace(tmp_path / "work"))
    assert record["ok"], summary(record)
    (result,) = record["specs"]
    assert {r["verdict"] for r in result["link"]} == {"PASS"}
    assert result["vectors"]["agree"] == result["vectors"]["count"] > 0


def test_writes_and_call_arguments_are_outputs_that_the_link_reads_after_the_call(tmp_path):
    setup = "tests/fixtures/test/EffectsSetup.sol:EffectsSetup"
    target = "tests/fixtures/contracts/Effects.sol:Effects"
    buy = Spec(
        name="effects-buy",
        target=f"{target}.buy",
        reads={"SWAPPER.fee()": "fee"},
        outputs={"SWAPPER.buy(msg.sender,amount)": (None, "bought")},
        link=Link(
            setup=setup,
            call="effects.buy(_amountIn)",
            adapters={"fee": "swapper.setFee(fee);"},
            post={"bought": "swapper.lastAmount()"},
        ),
        project=SPEC.project,
    )

    @buy.theorem(assume=("no-revert",), witness=lambda q: q.post.bought > 0)
    def buys_at_most_the_amount_in(q):
        """The amount bought, at its price with the fee, is at most the amount in."""
        return q.post.bought * (10**18 + q.fee) <= q._amountIn

    pull = Spec(
        name="effects-pull",
        target=f"{target}.pull",
        reads={"$allowances[msg.sender].left": "stored", "block.timestamp": "time"},
        outputs={"$allowances[msg.sender].left": "remaining", "$allowances[msg.sender].last": "last"},
        link=Link(
            setup=setup,
            call="effects.pull(_amount, _refill)",
            adapters={"stored": "effects.setLeft(address(this), stored);", "time": "vm.warp(time);"},
            post={"remaining": "effects.leftOf(address(this))", "last": "effects.lastOf(address(this))"},
        ),
        project=SPEC.project,
    )

    @pull.theorem(
        assume=("no-revert",),
        expect="counterexample",
        reason="the refill is not capped, so the allowance left can pass 2**192 and its conversion truncates it",
    )
    def the_allowance_left_is_the_refilled_allowance_less_the_amount(q):
        """The allowance left is the stored allowance with the refill, less the amount pulled."""
        return q.post.remaining == q.stored + q._refill - q._amount

    def capped(h):
        """the stored allowance and the refill stay below 2**192"""
        return h.stored + h._refill < 2**192

    @pull.theorem(assume=("no-revert",), given=capped, witness=lambda q: q.post.remaining > 0)
    def a_capped_refill_does_not_truncate(q):
        """With the refill capped, the allowance left is the refilled allowance less the amount pulled."""
        return q.post.remaining == q.stored + q._refill - q._amount

    add = Spec(
        name="effects-add",
        target=f"{target}.add",
        reads={"$total": "before"},
        outputs={"$total": "total"},
        link=Link(
            setup=setup,
            call="effects.add(_x, _y)",
            adapters={"before": "effects.setTotal(before);"},
            post={"total": "effects.total()"},
        ),
        project=SPEC.project,
    )

    @add.theorem(assume=("no-revert",), witness=lambda q: q.result)
    def the_total_moves_by_the_difference(q):
        """The total after the call is the total before it with x added and y taken away, when x is not zero."""
        return q.post.total == z3.If(q._x == 0, q.before, q.before + q._x - q._y)

    record = run([buy, pull, add], Workspace(tmp_path / "work"))
    assert record["ok"], summary(record)
    for result in record["specs"]:
        assert {r["verdict"] for r in result["link"]} == {"PASS"}, result["spec"]
        assert result["vectors"]["agree"] == result["vectors"]["count"] > 0
    verdicts = [t["verdict"] for result in record["specs"] for t in result["theorems"]]
    assert verdicts == ["PROVED", "COUNTEREXAMPLE", "PROVED", "PROVED"]
    assert record["specs"][1]["model"]["outputs"][0] == {
        "name": "remaining",
        "type": "uint192",
        "kind": "write",
        "source": "$allowances[msg.sender].left",
        "argument": None,
    }


def test_max_and_a_mulDiv_rounded_down_prove_their_lemmas_and_a_signed_parameter_reaches_the_call(tmp_path):
    setup = "tests/fixtures/test/RoundedSetup.sol:RoundedSetup"
    share = Spec(
        name="rounded-share",
        target="tests/fixtures/contracts/Rounded.sol:Rounded.share",
        reads={},
        link=Link(setup=setup, call="rounded.share(_x, _y, _d)"),
        project=SPEC.project,
    )

    @share.theorem(assume=("no-revert", "inside"), witness=lambda q: q.result > 1)
    def the_share_is_at_least_each_floor(q):
        """The share is at least each of the two floors."""
        return z3.And(q.result >= q._x * q._y / q._d, q.result >= q._y * q._y / q._d)

    swap = Spec(
        name="rounded-swap",
        target="tests/fixtures/contracts/Rounded.sol:Rounded.swap",
        reads={},
        outputs={"POOL.exchange(0,_out,_dx,minDy)": (None, None, None, "minOut")},
        link=Link(
            setup=setup,
            call="rounded.swap(_out, _dx)",
            parameters={"_out": "int128"},
            post={"minOut": "pool.lastMinDy()"},
        ),
        project=SPEC.project,
    )
    record = run([share, swap], Workspace(tmp_path / "work"))
    assert record["ok"], summary(record)
    checks = {r["check"]: r["verdict"] for r in record["link"]["shared"]}
    assert {"check_lemma_maxIsTheLargerValue", "check_lemma_mulDivOfHalfWidthOperandsIsTheFloor"} <= set(checks)
    assert set(checks.values()) == {"PASS"}
    for result in record["specs"]:
        assert {r["verdict"] for r in result["link"]} == {"PASS"}
    assert record["specs"][0]["theorems"][0]["lemma_domain"] == "established"
