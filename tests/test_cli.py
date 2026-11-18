import dataclasses
import json
import pathlib
import sys

import pytest
import z3

from examples.quoter.spec import SPEC
from solift import cli, forge
from solift.cli import SpecError, entrypoint, load_specs, main
from solift.link import Harness
from solift.run import _prepared, load, run
from solift.workspace import Project, Workspace, WorkspaceError

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture
def specs_dir(tmp_path, monkeypatch):
    """A directory of spec modules, as the current directory of the command line."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    yield tmp_path
    for name in [n for n in sys.modules if n.startswith("userspecs")]:
        del sys.modules[name]


def write(directory: pathlib.Path, name: str, text: str) -> None:
    (directory / "userspecs").mkdir(exist_ok=True)
    (directory / "userspecs" / "__init__.py").write_text("")
    (directory / "userspecs" / f"{name}.py").write_text(text)


def test_a_module_gives_its_spec_its_specs_or_the_attribute_it_names(specs_dir):
    write(specs_dir, "one", "from examples.quoter.spec import SPEC\n")
    write(specs_dir, "many", "from examples.pricer.spec import SPECS\nOTHER = SPECS[:1]\n")
    write(specs_dir, "both", "from examples.pricer.spec import SPECS\nSPEC = SPECS[0]\n")
    assert [s.name for s in load_specs("userspecs.one")] == ["quoter"]
    assert [s.name for s in load_specs("userspecs.many")] == ["pricer-priced", "pricer-quote-for"]
    assert [s.name for s in load_specs("userspecs.many:OTHER")] == ["pricer-priced"]
    with pytest.raises(SpecError, match="must have exactly one of SPEC and SPECS"):
        load_specs("userspecs.both")
    with pytest.raises(SpecError, match="has no MISSING"):
        load_specs("userspecs.one:MISSING")
    assert not list(specs_dir.rglob("__pycache__"))


def test_a_module_that_fails_to_import_or_names_no_spec_is_an_error(specs_dir):
    write(specs_dir, "broken", "import no_such_module\n")
    write(specs_dir, "notspec", "SPEC = 3\n")
    other = "dataclasses.replace(SPEC, project=dataclasses.replace(SPEC.project, profile='other'))"
    write(specs_dir, "split", f"import dataclasses\nfrom examples.quoter.spec import SPEC\nSPECS = [SPEC, {other}]\n")
    with pytest.raises(SpecError, match="cannot import userspecs.broken: ModuleNotFoundError"):
        load_specs("userspecs.broken")
    with pytest.raises(SpecError, match="is not a Spec or a nonempty list of them"):
        load_specs("userspecs.notspec")
    with pytest.raises(SpecError, match="do not share one project"):
        load_specs("userspecs.split:SPECS")
    with pytest.raises(SystemExit):
        entrypoint(["run", "userspecs.broken"])


def test_the_command_line_runs_a_module_and_prints_the_reads_of_a_target(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(ROOT)
    work = tmp_path / "work"
    assert entrypoint(["run", "examples.quoter.spec", "--no-link", "--workspace", str(work)]) == 0
    record = json.loads((work / "results.json").read_text())
    assert record["ok"] and record["link"] is None
    capsys.readouterr()
    target = "tests/fixtures/contracts/Quoter.sol:Quoter._quote"
    assert entrypoint(["reads", target, "--workspace", str(tmp_path / "reads"), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["complete"] and len(data["reads"]) == 6


def test_the_root_is_the_option_else_the_specs_else_the_current_directory_and_the_profile_replaces_the_specs(
    specs_dir, tmp_path, monkeypatch
):
    seen = []
    monkeypatch.setattr(cli, "run", lambda specs, workspace, **options: seen.append(specs[0].project) or {"ok": True})
    monkeypatch.setattr(cli, "summary", lambda record: "")
    other = tmp_path / "other"
    other.mkdir()

    def spec(project: str) -> str:
        harness = "harness=Harness.from_project('test/A.sol:A')"
        return f"from solift import Harness, Project, Spec\n\nSPEC = Spec('a', 'src/A.sol:A.f', {harness}{project})\n"

    write(specs_dir, "rootless", spec(""))
    write(specs_dir, "rooted", spec(f", project=Project({str(other)!r}, profile='deploy')"))
    work = ["--workspace", str(tmp_path / "work")]
    assert entrypoint(["run", "userspecs.rootless", *work]) == 0
    assert entrypoint(["run", "userspecs.rooted", *work]) == 0
    assert entrypoint(["run", "userspecs.rooted", "--root", str(specs_dir), "--profile", "ci", *work]) == 0
    (rooted,) = load_specs("userspecs.rooted")
    assert main(rooted, ["--profile", "ci", *work]) == 0
    here, other = specs_dir.resolve(), other.resolve()
    assert [(p.root, p.profile) for p in seen] == [(here, None), (other, "deploy"), (here, "ci"), (other, "ci")]
    with pytest.raises(SystemExit):
        entrypoint(["run", "userspecs.rootless", "--root", str(tmp_path / "missing"), *work])


def test_a_run_of_a_project_without_a_root_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="has no root"):
        run(dataclasses.replace(SPEC, project=Project()), Workspace(tmp_path / "work"))


def test_reads_takes_the_overlay_entries_and_prints_a_failed_build_without_a_traceback(tmp_path, capsys):
    config = '[profile.default]\nsrc = "src"\nremappings = ["@mylib/=.deps/mylib/src/"]\n'
    (tmp_path / "foundry.toml").write_text(config)
    (tmp_path / "src").mkdir()
    (tmp_path / ".deps/mylib/src").mkdir(parents=True)
    header = "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.0;\n"
    (tmp_path / ".deps/mylib/src/Lib.sol").write_text(
        header + "library Lib {\n  function twice(uint256 x) internal pure returns (uint256) {\n"
        "    return 2 * x;\n  }\n}\n"
    )
    (tmp_path / "src/Vault.sol").write_text(
        header + 'import {Lib} from "@mylib/Lib.sol";\n\ncontract Vault {\n  uint256 public total;\n\n'
        "  function quote(uint256 a) public view returns (uint256) {\n    return Lib.twice(a) + total;\n  }\n}\n"
    )
    reads = ["reads", "src/Vault.sol:Vault.quote", "--root", str(tmp_path), "--workspace", str(tmp_path / "work")]
    assert entrypoint(reads) == 0
    assert "1 reads, complete" in capsys.readouterr().out
    assert entrypoint([*reads, "--link", "foundry.toml"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "Traceback" not in err
    assert "the overlay links foundry.toml and copies src: pass --link or --copy" in err
    assert entrypoint([*reads, "--link", "foundry.toml", "--link", ".deps"]) == 0
    assert entrypoint([*reads, "--copy", "src", "--copy", ".deps"]) == 0


def test_a_missing_forge_fails_the_build_with_a_record(tmp_path, monkeypatch):
    monkeypatch.setenv("SOLIFT_FORGE", "no-such-forge")
    record = run(SPEC, Workspace(tmp_path / "work"), link=False)
    assert not record["ok"]
    assert record["failures"][0].startswith("build: ") and "no-such-forge" in record["failures"][0]
    assert "tracebacks" not in record


def test_a_foundry_project_infers_its_overlay_from_the_profile(tmp_path):
    (tmp_path / "foundry.toml").write_text(
        '[profile.default]\nsrc = "src"\n\n[profile.deploy]\nsrc = "contracts"\ntest = "checks"\nlibs = ["deps"]\n'
    )
    (tmp_path / "remappings.txt").write_text(
        "dep/=deps/dep/\n@own/=src/own/\nctx:@vendor/=.vendor/v/\n@up/=../up/\n@gone/=gone/x/\n"
    )
    for directory in ("src", "contracts", "checks", "deps", ".vendor"):
        (tmp_path / directory).mkdir()
    default = Project(root=tmp_path).resolve()
    assert (default.link, default.copy, default.generated) == (
        ("foundry.toml", "remappings.txt", "deps", ".vendor"),
        ("src",),
        "test/solift",
    )
    deploy = Project(root=tmp_path, profile="deploy").resolve()
    assert deploy.link == ("foundry.toml", "remappings.txt", "deps", "src", ".vendor")
    assert (deploy.copy, deploy.generated) == (("contracts",), "checks/solift")
    kept = Project(root=tmp_path, copy=("src", "contracts"), link=()).resolve()
    assert (kept.link, kept.copy) == ((), ("src", "contracts"))
    copied = Project(root=tmp_path, copy=("src", ".vendor")).resolve()
    assert copied.link == ("foundry.toml", "remappings.txt", "deps")
    explicit = Project(root=tmp_path, link=(), copy=(), generated="test/gen", add={"x.sol": tmp_path / "x.sol"})
    assert explicit.resolve() is explicit
    assert Project(root=tmp_path, add=explicit.add).resolve().add == explicit.add
    assert Project(root=pathlib.Path(".")).root == pathlib.Path(".").resolve()
    with pytest.raises(WorkspaceError, match="the project has no root"):
        Project().resolve()


def test_a_harness_goes_into_the_overlay_once_and_two_harnesses_at_one_place_fail(tmp_path):
    harness = tmp_path / "VaultSetup.sol"
    harness.write_text("// the harness\n")
    project = Project(
        root=ROOT, link=("foundry.toml", "lib"), copy=("tests/fixtures",), generated="tests/fixtures/test/solift"
    )
    shared = Harness(f"{harness}:VaultSetup")
    a, b = (dataclasses.replace(SPEC, name=name, harness=shared, project=project) for name in ("a", "b"))
    resolved, setups = _prepared([a, b])
    at = "tests/fixtures/test/solift/harness/VaultSetup.sol"
    assert resolved.add == {at: harness.resolve()}
    assert setups == [f"{at}:VaultSetup"] * 2 and resolved.link == project.link
    other = tmp_path / "other"
    other.mkdir()
    (other / "VaultSetup.sol").write_text("// another harness\n")
    c = dataclasses.replace(a, harness=Harness(f"{other / 'VaultSetup.sol'}:VaultSetup"))
    with pytest.raises(WorkspaceError, match="two harnesses go to"):
        _prepared([a, c])
    linked = dataclasses.replace(a, harness=Harness.from_project("lib/forge-std/src/Test.sol:Test"))
    with pytest.raises(WorkspaceError, match="the harness lib/forge-std/src/Test.sol is not under a path that the"):
        _prepared([linked])
    assert _prepared([SPEC])[1] == ["tests/fixtures/test/QuoterSetup.sol:QuoterSetup"]
    copied = dataclasses.replace(project, copy=(*project.copy, "lib/forge-std/src/Test.sol"), link=("foundry.toml",))
    assert _prepared([dataclasses.replace(linked, project=copied)])[1] == ["lib/forge-std/src/Test.sol:Test"]


def test_the_standalone_vault_example_runs_from_its_own_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(ROOT / "examples" / "vault")
    monkeypatch.setattr(sys, "path", list(sys.path))
    work = tmp_path / "work"
    try:
        assert entrypoint(["run", "vault_specs.spec", "--workspace", str(work)]) == 0
    finally:
        for name in [n for n in sys.modules if n.startswith("vault_specs")]:
            del sys.modules[name]
    record = json.loads((work / "results.json").read_text())
    verdicts = {t["id"]: (t["verdict"], t["lemma_domain"]) for s in record["specs"] for t in s["theorems"]}
    assert verdicts == {
        "redeem-rounds-down": ("PROVED", "established"),
        "redeem-pays-at-most-the-assets": ("PROVED", "established"),
        "redeem-pays-the-exact-part": ("COUNTEREXAMPLE", "established"),
        "deposit-mints-at-most-the-exact-shares": ("PROVED", "established"),
    }
    assert record["project"]["link"] == ["foundry.toml", "lib"] and record["project"]["copy"] == ["src"]
    assert not list((ROOT / "examples" / "vault").rglob("__pycache__"))


def test_a_missing_added_file_fails_the_run_with_a_record(tmp_path):
    project = dataclasses.replace(SPEC.project, add={"tests/fixtures/test/Gone.sol": tmp_path / "Gone.sol"})
    record = run(dataclasses.replace(SPEC, project=project), Workspace(tmp_path / "work"), link=False)
    assert not record["ok"] and record["failures"][0].startswith("build: ") and "Gone.sol" in record["failures"][0]


def test_a_foundry_config_that_forge_cannot_read_is_a_build_error(tmp_path):
    (tmp_path / "foundry.toml").write_text("[profile.default\nsrc = \n")
    with pytest.raises(forge.BuildError, match="config --json"):
        Project(root=tmp_path).resolve()


def test_load_adds_a_harness_that_holds_the_target(tmp_path):
    harness = tmp_path / "Doubler.sol"
    harness.write_text(
        "// SPDX-License-Identifier: Apache-2.0\npragma solidity ^0.8.27;\n\ncontract Doubler {\n"
        "  function twice(uint256 _x) external pure returns (uint256) {\n    return _x * 2;\n  }\n}\n"
    )
    at = "tests/fixtures/test/solift/harness/Doubler.sol"
    doubler = Harness(f"{harness}:Doubler")
    spec = dataclasses.replace(
        SPEC, name="doubler", target=f"{at}:Doubler.twice", reads={}, harness=doubler, theorems=[]
    )
    (q,) = load([spec], Workspace(tmp_path / "work")).values()
    assert z3.simplify(q.at(_x=21).result).as_long() == 42


def test_reads_takes_a_target_in_a_library_that_no_source_imports(tmp_path, capsys):
    """forge compiles a library's file only when a source imports it, as a run's harness does. reads imports it too."""
    (tmp_path / "foundry.toml").write_text('[profile.default]\nsrc = "src"\nlibs = ["lib"]\n')
    (tmp_path / "src").mkdir()
    (tmp_path / "lib/dep/src").mkdir(parents=True)
    header = "// SPDX-License-Identifier: MIT\npragma solidity ^0.8.0;\n"
    (tmp_path / "src/Other.sol").write_text(header + "contract Other {}\n")
    (tmp_path / "lib/dep/src/Lib.sol").write_text(
        header + "contract Dep {\n  uint256 internal total;\n\n"
        "  function doubled() external view returns (uint256) {\n    return 2 * total;\n  }\n}\n"
    )
    reads = ["reads", "lib/dep/src/Lib.sol:Dep.doubled", "--root", str(tmp_path), "--workspace", str(tmp_path / "w")]
    assert entrypoint(reads) == 0
    assert "1 reads, complete" in capsys.readouterr().out
