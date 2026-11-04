import json
import pathlib
import subprocess

import pytest

from solift import forge
from solift.extract import extract
from solift.forge import BuildError, statuses
from solift.halmos import HalmosEngine, judge
from solift.link import Check, Link, generate, placed
from solift.workspace import Workspace
from tests.projects import QUOTER, QUOTER_LINK, QUOTER_READS, fixtures

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


def link_run(workspace: Workspace, target: str, reads: dict, link: Link, replace: dict | None = None, outputs=None):
    """Generates the link of `target` from the fixture source, and runs it against the fixture contracts, or against
    `replace`, the text of a source file by its path."""
    project = fixtures()
    build_root = project.overlay(workspace, "build-root")
    infos = forge.build(build_root, workspace.fresh("ast-out"), workspace.fresh("ast-cache"))
    model = extract(infos, target, reads, outputs).model
    generated = generate(model, link)
    root = project.overlay(workspace, replace=replace)
    for name, text in generated.files.items():
        (root / project.generated / name).write_text(text)
    return HalmosEngine().run(root, workspace, placed(generated.checks, project.generated))


def test_the_quoter_link_passes(workspace):
    run = link_run(workspace, f"{QUOTER}:Quoter._quote", QUOTER_READS, QUOTER_LINK)
    assert run.ok, [(r.check.function, r.verdict) for r in run.results]
    assert len(run.results) == 8


QUOTE = (QUOTER_READS, None, QUOTER_LINK)
TUPLES = "tests/fixtures/contracts/Tuples.sol"
LIMITS = ({}, None, Link(setup="tests/fixtures/test/TuplesSetup.sol:TuplesSetup", call="tuples.limits(_a, _b)"))
POINTERS = "tests/fixtures/contracts/Pointers.sol"
PER_PERIOD = (
    {"$spenders[_who].config.perPeriod": "perPeriod"},
    None,
    Link(
        setup="tests/fixtures/test/PointersSetup.sol:PointersSetup",
        call="pointers.perPeriod(_who)",
        adapters={"perPeriod": "pointers.set(_who, 0, perPeriod, 0, 0);"},
    ),
)
PARAMETERS = "tests/fixtures/contracts/Parameters.sol"
PARAMETERS_SETUP = "tests/fixtures/test/ParametersSetup.sol:ParametersSetup"
CAPPED = (
    {"$bonus": "bonus"},
    None,
    Link(
        setup=PARAMETERS_SETUP,
        call="parameters.capped(_ceiling, _gas, _fresh)",
        adapters={"bonus": "parameters.setBonus(bonus);"},
    ),
)
SPREAD = ({}, None, Link(setup=PARAMETERS_SETUP, call="parameters.spread(_a, _b)"))
EARLY_RETURN = ({}, None, Link(setup=PARAMETERS_SETUP, call="parameters.earlyReturn(_a, _small)"))
RECORDED = (
    {"$bonus": "bonus"},
    {"$last": "last"},
    Link(
        setup=PARAMETERS_SETUP,
        call="parameters.recorded(_amount)",
        adapters={"bonus": "parameters.setBonus(bonus);"},
        post={"last": "parameters.last()"},
    ),
)
NESTED = ({}, None, Link(setup=PARAMETERS_SETUP, call="parameters.nested(_a)"))
EFFECTS = "tests/fixtures/contracts/Effects.sol"
EFFECTS_SETUP = "tests/fixtures/test/EffectsSetup.sol:EffectsSetup"
BUY = (
    {"SWAPPER.fee()": "fee"},
    {"SWAPPER.buy(msg.sender,amount)": (None, "bought")},
    Link(
        setup=EFFECTS_SETUP,
        call="effects.buy(_amountIn)",
        adapters={"fee": "swapper.setFee(fee);"},
        post={"bought": "swapper.lastAmount()"},
    ),
)
PULL = (
    {"$allowances[msg.sender].left": "stored", "block.timestamp": "time"},
    {"$allowances[msg.sender].left": "remaining", "$allowances[msg.sender].last": "last"},
    Link(
        setup=EFFECTS_SETUP,
        call="effects.pull(_amount, _refill)",
        adapters={"stored": "effects.setLeft(address(this), stored);", "time": "vm.warp(time);"},
        post={"remaining": "effects.leftOf(address(this))", "last": "effects.lastOf(address(this))"},
    ),
)
# Changes to a contract after the extraction of its model, which the link must find, each to a value and not to where
# the contract reverts: a result, each value of several returns, an assignment to a parameter, the key of a storage
# pointer, and outputs: an argument of a call, a written value, and a write at another key.
CHANGES = {
    "result": (f"{QUOTER}:Quoter._quote", QUOTE, "_cost + _margin;", "(_cost + _margin) / 2;"),
    "first-value": (f"{TUPLES}:Tuples.limits", LIMITS, "sum = _a + _b;", "sum = (_a + _b) / 2;"),
    "second-value": (f"{TUPLES}:Tuples.limits", LIMITS, "_a < _b ? _a : _b;", "_a < _b ? _b : _a;"),
    "assigned-parameter": (f"{PARAMETERS}:Parameters.capped", CAPPED, "_ceiling += $bonus;", "_ceiling += $bonus / 2;"),
    "pointer-key": (f"{POINTERS}:Pointers.perPeriod", PER_PERIOD, "[_who].config;", "[msg.sender].config;"),
    "call-argument": (f"{EFFECTS}:Effects.buy", BUY, "(msg.sender, amount);", "(msg.sender, amount / 2);"),
    "written-value": (f"{EFFECTS}:Effects.pull", PULL, "uint192(left - _amount);", "uint192(left - _amount / 2);"),
    "written-key": (f"{EFFECTS}:Effects.pull", PULL, "allowance.last =", "$allowances[address(0)].last ="),
}


@pytest.mark.parametrize(
    "function, spec",
    [("capped", CAPPED), ("spread", SPREAD), ("earlyReturn", EARLY_RETURN), ("recorded", RECORDED), ("nested", NESTED)],
)
def test_functions_that_assign_their_parameters_link_to_their_models(workspace, function, spec):
    reads, outputs, link = spec
    run = link_run(workspace, f"{PARAMETERS}:Parameters.{function}", reads, link, outputs=outputs)
    assert run.ok, [(r.check.function, r.verdict) for r in run.results]


@pytest.mark.parametrize("change", CHANGES)
def test_the_link_finds_a_contract_that_no_longer_matches_its_model(workspace, change):
    target, (reads, outputs, link), old, new = CHANGES[change]
    path = target.partition(":")[0]
    source = (pathlib.Path(fixtures().root) / path).read_text()
    assert source.count(old) == 1
    run = link_run(workspace, target, reads, link, {path: source.replace(old, new)}, outputs)
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


def test_a_failed_forge_test_exit_is_an_error_even_when_every_reported_test_passes(monkeypatch, tmp_path):
    """forge test exits with a failure when a test fails. An exit with a failure after only successes means that forge
    stopped for another reason, such as a crash or a kill, so its results do not count."""
    line = '{"a/V.t.sol:V": {"test_results": {"test_vector_000()": {"status": "Success"}}}}\n'
    for returncode, failing in ((137, line), (1, line.replace("Success", "Failure"))):
        done = subprocess.CompletedProcess([], returncode, stdout=failing, stderr="Killed")
        monkeypatch.setattr(forge.subprocess, "run", lambda *args, done=done, **kwargs: done)
        if returncode == 137:
            with pytest.raises(BuildError, match="exited with 137, but every test it reported passed"):
                forge.test(tmp_path, tmp_path / "out", tmp_path / "cache", ("V",))
        else:
            assert forge.test(tmp_path, tmp_path / "out", tmp_path / "cache", ("V",)) == {
                ("a/V.t.sol", "V", "test_vector_000"): "Failure"
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


def test_forge_builds_without_dynamic_test_linking(tmp_path):
    """forge 1.8 compiles `new C()` in a test as vm.deployCode when dynamic test linking is on, which Halmos 0.3.3
    does not support, so every build of a run turns it off."""
    assert forge.environment(tmp_path / "out", tmp_path / "cache")["FOUNDRY_DYNAMIC_TEST_LINKING"] == "false"
