import dataclasses

from examples.pricer.spec import SPECS as PRICER_SPECS
from examples.quoter.spec import QUOTER, SPEC
from z3sol import forge
from z3sol.extract import extract
from z3sol.link import generate
from z3sol.run import run, summary, vector_failures
from z3sol.workspace import Workspace


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
    assert {"halmos", "yices-solver", "cvc5", "z3 (theorems)", "forge", "python", "z3sol commit"} <= set(
        record["versions"]
    )
    assert QUOTER in spec["sources"]
    assert record["schema"] == 2


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
    assert {r["contract"] for r in record["link"]["shared"]} == {"Z3solOperators"}
    assert len(record["link"]["shared"]) == 8
    assert "Z3solOperators.t.sol" in record["generated"]


def test_two_specs_whose_generated_contracts_have_the_same_names_fail_the_run(tmp_path):
    twin = dataclasses.replace(SPEC, name="quoter-twin", theorems=[])
    record = run([SPEC, twin], Workspace(tmp_path / "work"), link=False, vectors=False)
    assert record["failures"] == ["quoter-twin: extract: QuoterQuote names the generated contracts of quoter too"]
    assert dataclasses.replace(twin, stem="QuoterTwin").stem == "QuoterTwin"


def test_the_vectors_of_a_model_are_exactly_its_own_tests_in_its_own_file():
    path = "test/z3sol/QVectors.t.sol"
    mine = {(path, "QVectors", f"test_vector_{n:03d}"): "Success" for n in range(2)}
    assert vector_failures("QVectors", path, 2, mine) == []
    old = {("old/QVectors.t.sol", "QVectors", f"test_old_{n}"): "Success" for n in range(2)}
    assert vector_failures("QVectors", path, 2, old) == [
        "vectors: the results come from old/QVectors.t.sol, not test/z3sol/QVectors.t.sol",
        "vectors: 0 of 2 agree with the emitted Solidity",
    ]
