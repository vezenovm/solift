import dataclasses

from examples.quoter.spec import QUOTER, SPEC
from z3sol import forge
from z3sol.extract import extract
from z3sol.link import generate
from z3sol.run import run, summary
from z3sol.workspace import Workspace


def test_a_run_proves_vectors_and_links_the_quoter_and_records_each_version(tmp_path):
    record = run(SPEC, Workspace(tmp_path / "work"))
    assert record["ok"], summary(record)
    assert {t["id"]: t["verdict"] for t in record["theorems"]} == {
        "quote-at-most-gas-plus-margin": "PROVED",
        "quote-at-most-gas": "COUNTEREXAMPLE",
    }
    assert record["vectors"]["agree"] == record["vectors"]["count"] > 100
    assert {r["verdict"] for r in record["link"]["results"]} == {"PASS"}
    versions = record["versions"]
    assert versions["solc"].startswith("0.8.30+commit.")
    assert versions["OpenZeppelin"]["git blob"] == "cd55ff13085414af44d7bd049a19847d8f614a48"
    assert {"halmos", "yices-solver", "z3 (theorems)", "forge", "python"} <= set(versions)
    assert QUOTER in record["sources"]


def test_a_mutant_that_breaks_a_theorem_fails_the_run_and_its_link_still_passes(tmp_path):
    source = (SPEC.project.root / QUOTER).read_text()
    mutated = source.replace("return _cost + _margin;", "return _cost + _margin + _margin;")
    record = run(SPEC, Workspace(tmp_path / "work"), replace={QUOTER: mutated})
    assert not record["ok"]
    assert record["failures"] == ["theorem quote-at-most-gas-plus-margin: COUNTEREXAMPLE, expected PROVED"]
    assert {r["verdict"] for r in record["link"]["results"]} == {"PASS"}
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
