import tomllib

from acceptance.compilers import VAULT_VARIANTS, Variant, _differences, _pragmas, _profile, across, vault_specs
from acceptance.resupply.project import VARIANTS as RESUPPLY_VARIANTS
from solift.workspace import Workspace


def test_the_vault_example_proves_the_same_queries_on_the_bytecode_of_each_compiler_and_setting(tmp_path):
    chosen = [v for v in VAULT_VARIANTS if v.name in ("as-configured", "via-ir", "solc-0.8.27")]
    records, failures = across(vault_specs(), chosen, Workspace(tmp_path / "work"))
    assert failures == []
    solcs = [r["specs"][0]["versions"]["solc"].partition("+")[0] for r in records.values()]
    assert solcs == ["0.8.30", "0.8.30", "0.8.27"]
    assert records["via-ir"]["project"]["profile"] == "solift-via-ir"
    assert records["as-configured"]["project"]["profile"] is None


def record(**specs) -> dict:
    return {
        "specs": [
            {"spec": name, "bytecode": {"sha256": code}, "theorems": [{"id": "t", "query_sha256": query}]}
            for name, (code, query) in specs.items()
        ]
    }


def test_a_query_that_changes_or_a_bytecode_that_does_not_fails_the_check():
    assert _differences({"a": record(s=("1", "q")), "b": record(s=("2", "q"))}) == []
    assert _differences({"a": record(s=("1", "q")), "b": record(s=("2", "r"))}) == [
        "s: the query of t is not the same in every variant (a; b)"
    ]
    assert _differences({"a": record(s=("1", "q")), "b": record(s=("1", "q"))}) == ["s: a, b give one bytecode"]


def test_a_variant_profile_keeps_the_base_settings_and_changes_its_own(tmp_path):
    text = '[profile.default]\nsrc = "src"\n\n[profile.deploy]\nsrc = "_"\nvia_ir = true\noptimizer_runs = 200\n'
    variant = Variant("solc-0.8.30", {"via_ir": False, "libraries": ["a:b:0x1"]}, solc="0.8.30")
    base = tomllib.loads(text)["profile"]["deploy"]
    added = tomllib.loads(text + _profile(variant.profile, {**base, **variant.settings, "solc": variant.solc}))
    assert added["profile"]["solift-solc-0.8.30"] == {
        "src": "_",
        "via_ir": False,
        "optimizer_runs": 200,
        "libraries": ["a:b:0x1"],
        "solc": "0.8.30",
    }


def test_a_new_solc_moves_each_exact_pin_of_the_copied_sources_and_leaves_ranges(tmp_path):
    (tmp_path / "src" / "lib").mkdir(parents=True)
    (tmp_path / "src" / "A.sol").write_text("// x\npragma solidity 0.8.28;\ncontract A {}\n")
    (tmp_path / "src" / "lib" / "B.sol").write_text("pragma solidity ^0.8.19;\ncontract B {}\n")
    (tmp_path / "Other.sol").write_text("pragma solidity 0.8.28;\n")
    assert _pragmas(tmp_path, ("src",), "0.8.30") == {"src/A.sol": "// x\npragma solidity 0.8.30;\ncontract A {}\n"}


def test_the_resupply_variants_change_the_compiler_the_pipeline_and_the_optimizer():
    assert [v.name for v in RESUPPLY_VARIANTS] == [
        "as-deployed",
        "optimizer-off",
        "optimizer-runs-1m",
        "legacy-pipeline",
        "solc-0.8.30",
    ]
    assert not RESUPPLY_VARIANTS[0].settings and RESUPPLY_VARIANTS[0].solc is None
