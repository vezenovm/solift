import json
import pathlib

import pytest

from tests.projects import QUOTER, QUOTER_LINK, QUOTER_READS, fixtures
from z3sol import forge
from z3sol.extract import extract
from z3sol.forge import BuildError, statuses
from z3sol.halmos import HalmosEngine, judge
from z3sol.link import Check, generate, placed
from z3sol.workspace import Workspace

GUARDS = "Guards"


def result(name: str, exitcode: int = 0, bounded: int = 0) -> dict:
    return {"name": f"{name}(uint256)", "exitcode": exitcode, "num_paths": [1, 1, 0], "time": [0.1, 0.1, 0.0],
            "num_bounded_loops": bounded}  # fmt: skip


def report(*results: dict, contract: str = "C") -> dict:
    return {"exitcode": 0, "test_results": {f"test/C.t.sol:{contract}": list(results)}}


def test_each_declared_check_must_pass():
    checks = (Check("C", "check_a"), Check("C", "check_b"), Check("C", "check_c"), Check("C", "check_d"))
    run = judge(checks, report(result("check_a"), result("check_b", 1), result("check_c", 2), result("check_d", 4)), "")
    assert [r.verdict for r in run.results] == ["PASS", "COUNTEREXAMPLE", "TIMEOUT", "REVERT_ALL"]
    assert not run.ok


def test_a_pass_with_a_path_at_the_loop_bound_fails():
    run = judge((Check("C", "check_a"),), report(result("check_a", 0, bounded=1)), "")
    assert [r.verdict for r in run.results] == ["BOUNDED"]
    assert not run.ok


def test_a_declared_check_without_a_result_fails_and_so_does_an_undeclared_one():
    run = judge((Check("C", "check_a"),), report(result("check_b")), "")
    assert [(r.check.function, r.verdict) for r in run.results] == [("check_a", "MISSING"), ("check_b", "UNDECLARED")]
    assert not run.ok


def test_the_log_warnings_fail_the_run_even_when_every_check_passes():
    checks = (Check("C", "check_a"),)
    skipped = judge(checks, report(result("check_a")), "WARNING  Skipped Foo.json due to parsing\n  failure: KeyError")
    bounded = judge(
        checks,
        report(result("check_a")),
        "check_a: paths have not been fully explored due to the loop unrolling bound: 4",
    )
    assert skipped.problems == ("Halmos skipped a contract, so its checks did not run",)
    assert bounded.problems == ("a path stopped at the loop bound, so the proof is not complete",)
    assert not skipped.ok and not bounded.ok
    assert not judge(checks, None, "").ok


@pytest.fixture
def workspace(tmp_path) -> Workspace:
    return Workspace(tmp_path / "work")


def test_halmos_gives_each_guard_its_verdict(workspace):
    root = fixtures().overlay(workspace)
    checks = tuple(
        Check(GUARDS, f) for f in ("check_passes", "check_fails", "check_stopsAtTheLoopBound", "check_missing")
    )
    run = HalmosEngine().run(root, workspace, checks)
    assert [r.verdict for r in run.results] == ["PASS", "COUNTEREXAMPLE", "BOUNDED", "MISSING"]
    assert run.problems == ("a path stopped at the loop bound, so the proof is not complete", "Halmos exited with 1")


@pytest.mark.parametrize("limit", [("--depth", "80"), ("--width", "1")])
def test_a_pass_cut_at_the_depth_or_width_limit_fails_the_run(workspace, limit):
    """Halmos reports PASS for a check that it stopped at --depth or --width, with only a warning."""
    root = fixtures().overlay(workspace)
    run = HalmosEngine(extra_args=limit).run(root, workspace, (Check("Limits", "check_branches"),))
    assert [r.verdict for r in run.results] == ["PASS"]
    assert run.problems == ("a path stopped at the --depth or --width limit, so the proof is not complete",)
    assert not run.ok


def test_a_cached_build_without_the_ast_fails_the_run(workspace):
    """Halmos skips, with only a warning, a contract whose cached build has no AST. The engine builds from empty
    directories, so the test keeps a build and removes the AST of one artifact, which forge then leaves as it is."""
    root = fixtures().overlay(workspace)
    checks = (Check(GUARDS, "check_passes"),)
    HalmosEngine().run(root, workspace, checks)
    artifact = workspace.path / "halmos-out" / "Guards.t.sol" / "Guards.json"
    data = json.loads(artifact.read_text())
    del data["ast"]
    artifact.write_text(json.dumps(data))
    run = HalmosEngine().run(root, workspace, checks, fresh=False)
    assert "Halmos skipped a contract, so its checks did not run" in run.problems
    assert [r.verdict for r in run.results] == ["MISSING"]
    fresh = HalmosEngine().run(root, workspace, checks)
    assert "Halmos skipped a contract, so its checks did not run" not in fresh.problems
    assert fresh.results[0].verdict == "PASS"


def quoter_link(workspace: Workspace, contract: str | None = None):
    """Generates the Quoter link from the fixture source, and runs it against `contract`, a replacement source."""
    project = fixtures()
    build_root = project.overlay(workspace, "build-root")
    infos = forge.build(build_root, workspace.fresh("ast-out"), workspace.fresh("ast-cache"))
    model = extract(infos, f"{QUOTER}:Quoter._quote", QUOTER_READS).model
    generated = generate(model, QUOTER_LINK)
    replace = {QUOTER: contract} if contract is not None else None
    root = project.overlay(workspace, replace=replace)
    for name, text in generated.files.items():
        (root / project.generated / name).write_text(text)
    return HalmosEngine().run(root, workspace, placed(generated.checks, project.generated))


def test_the_quoter_link_passes(workspace):
    run = quoter_link(workspace)
    assert run.ok, [(r.check.function, r.verdict) for r in run.results]
    assert len(run.results) == 8


def test_the_link_finds_a_contract_that_no_longer_matches_its_model(workspace):
    source = (pathlib.Path(fixtures().root) / QUOTER).read_text()
    mutated = source.replace("return _cost + _margin;", "return _cost + _margin + 1;")
    assert mutated != source
    run = quoter_link(workspace, mutated)
    verdicts = {r.check.function: r.verdict for r in run.results}
    assert verdicts["check_link"] == "COUNTEREXAMPLE"


def test_two_results_for_one_check_fail_the_run_even_when_one_passes():
    checks = (Check("C", "check_link"),)
    two_paths = {
        "exitcode": 1,
        "test_results": {
            "gen/C.t.sol:C": [result("check_link", 1)],
            "other/C.t.sol:C": [{**result("check_link"), "name": "check_link()"}],
        },
    }
    run = judge(checks, two_paths, "")
    assert not run.ok
    assert any(p.startswith("two results for C.check_link") for p in run.problems)
    overloaded = report(result("check_link"), {**result("check_link", 1), "name": "check_link(uint8)"})
    assert not judge(checks, overloaded, "").ok


def test_a_failed_halmos_exit_fails_the_run_even_when_each_declared_check_passes():
    data = {**report(result("check_a")), "exitcode": 1}
    run = judge((Check("C", "check_a"),), data, "")
    assert run.problems == ("Halmos exited with 1",)
    assert not run.ok


def test_two_forge_results_for_one_test_are_refused():
    line = '{"a/V.t.sol:V": {"test_results": {"test_vector_000()": {"status": "Success"}}}, '
    line += '"b/V.t.sol:V": {"test_results": {"test_vector_000()": {"status": "Failure"}}}}'
    with pytest.raises(BuildError, match="two results for V.test_vector_000"):
        statuses(line, ("V",))
    assert statuses(line.replace("b/V.t.sol:V", "b/W.t.sol:W"), ("V",)) == {
        ("a/V.t.sol", "V", "test_vector_000"): "Success"
    }


def test_a_check_from_another_file_does_not_stand_in_for_the_generated_one():
    declared = (Check("C", "check_link", path="gen/C.t.sol"),)
    elsewhere = {"exitcode": 0, "test_results": {"other/C.t.sol:C": [result("check_link")]}}
    run = judge(declared, elsewhere, "")
    assert [(r.check.path, r.verdict) for r in run.results] == [
        ("gen/C.t.sol", "MISSING"),
        ("other/C.t.sol", "UNDECLARED"),
    ]
    assert not run.ok
    here = {"exitcode": 0, "test_results": {"gen/C.t.sol:C": [result("check_link")]}}
    assert judge(declared, here, "").ok


def test_a_halmos_process_that_fails_after_a_passing_report_fails_the_run():
    checks = (Check("C", "check_a"),)
    run = judge(checks, report(result("check_a")), "", returncode=137)
    assert run.problems == ("the Halmos process exited with 137",)
    assert not run.ok
    assert judge(checks, {**report(result("check_a", 1)), "exitcode": 1}, "", returncode=1).problems == (
        "Halmos exited with 1",
    )
