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
