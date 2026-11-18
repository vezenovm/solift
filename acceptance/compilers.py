"""The same specs under other compilers and compiler settings, with the theorems unchanged.

A variant is a profile that the overlay adds to a copy of the project's foundry.toml: the settings of the project's
profile, with the variant's changes. A variant with another solc also rewrites each exact pragma of the copied sources,
`pragma solidity 0.8.28;`, to it, as a project does when it moves to a new compiler. For each variant the run builds,
extracts the model, proves the theorems, checks the vectors and links the model to that variant's bytecode again.

The check passes when every run passes, when each theorem's query (its sha256) is the same in every variant, so that
the solvers proved it on the same terms, and when each variant gives each target a bytecode of its own. A variant with
no changes runs the project as it is.

    python -m acceptance.compilers    # the vault example (examples/vault) under six compilers and settings
"""

from __future__ import annotations

import dataclasses
import importlib
import json
import pathlib
import re
import sys
import time
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass, field

from solift.run import Spec, run
from solift.workspace import Workspace

ROOT = pathlib.Path(__file__).resolve().parent.parent
VAULT = ROOT / "examples" / "vault"
EXACT_PRAGMA = re.compile(r"^pragma solidity (\d+\.\d+\.\d+);", re.MULTILINE)


@dataclass(frozen=True)
class Variant:
    """`settings` are foundry profile keys that change the project's profile, and `solc` the compiler version."""

    name: str
    settings: dict = field(default_factory=dict, hash=False)
    solc: str | None = None

    @property
    def profile(self) -> str:
        return f"solift-{self.name}"


VAULT_VARIANTS = (
    Variant("as-configured"),
    Variant("optimizer-off", {"optimizer": False}),
    Variant("optimizer-runs-1m", {"optimizer_runs": 1_000_000}),
    Variant("via-ir", {"via_ir": True}),
    Variant("solc-0.8.27", {"evm_version": "cancun"}, solc="0.8.27"),
    Variant("solc-0.8.28-via-ir", {"evm_version": "cancun", "via_ir": True}, solc="0.8.28"),
)


def across(
    specs: Sequence[Spec], variants: Sequence[Variant], workspace: Workspace, prefix: str = ""
) -> tuple[dict, list[str]]:
    """Runs the specs under each variant, in the workspace's child `<prefix><variant>`, and returns the record of each
    and the failures of the check. The children sit beside a run's other children, not one level below them: forge
    1.4 detects another remapping for a library under node_modules when the overlay is deeper (Resupply's
    `@openzeppelin/`, from frax-standard-solidity's own remappings, to a path that does not exist)."""
    project = specs[0].project.resolve()
    text = (project.root / "foundry.toml").read_text()
    base = {}
    if project.profile:
        profiles = tomllib.loads(text).get("profile", {})
        if project.profile not in profiles:
            raise ValueError(f"the project's foundry.toml has no profile {project.profile} for the variants to change")
        base = profiles[project.profile]
    records, failures = {}, []
    for variant in variants:
        replace = None
        changed = project
        if variant.settings or variant.solc:
            settings = {**base, **variant.settings, **({"solc": variant.solc} if variant.solc else {})}
            replace = {"foundry.toml": text + _profile(variant.profile, settings)}
            if variant.solc:
                replace |= _pragmas(project.root, project.copy, variant.solc)
            changed = dataclasses.replace(
                project,
                profile=variant.profile,
                link=tuple(p for p in project.link if p != "foundry.toml"),
                copy=tuple(dict.fromkeys((*project.copy, "foundry.toml"))),
            )
        start = time.time()
        changed_specs = [dataclasses.replace(s, project=changed) for s in specs]
        record = run(changed_specs, workspace.child(f"{prefix}{variant.name}"), replace)
        record["variant"] = {"name": variant.name, "settings": variant.settings, "solc": variant.solc}
        record["seconds"] = round(time.time() - start, 1)
        records[variant.name] = record
        failures += [f"{variant.name}: {f}" for f in record["failures"]]
        for spec in record["specs"]:
            solc = spec.get("versions", {}).get("solc", "")
            if variant.solc and not solc.startswith(f"{variant.solc}+"):
                failures.append(f"{variant.name}: {spec['spec']} was built with solc {solc}, not {variant.solc}")
    return records, failures + _differences(records)


def _differences(records: dict) -> list[str]:
    """A theorem whose query is not the same in every variant, and two variants that give a target one bytecode."""
    failures = []
    queries, bytecodes = {}, {}
    for name, record in records.items():
        for spec in record["specs"]:
            if "bytecode" in spec:
                bytecodes.setdefault(spec["spec"], {}).setdefault(spec["bytecode"]["sha256"], []).append(name)
            for t in spec.get("theorems", []):
                queries.setdefault((spec["spec"], t["id"]), {}).setdefault(t["query_sha256"], []).append(name)
    for (spec, theorem), found in queries.items():
        if len(found) > 1:
            groups = "; ".join(", ".join(names) for names in found.values())
            failures.append(f"{spec}: the query of {theorem} is not the same in every variant ({groups})")
    for spec, found in bytecodes.items():
        failures += [f"{spec}: {', '.join(names)} give one bytecode" for names in found.values() if len(names) > 1]
    return failures


def _profile(name: str, settings: dict) -> str:
    lines = [f"\n[profile.{json.dumps(name)}]"] + [f"{key} = {_toml(value)}" for key, value in settings.items()]
    return "\n".join(lines) + "\n"


def _toml(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{json.dumps(k)} = {_toml(v)}" for k, v in value.items()) + "}"
    raise TypeError(f"no TOML for the profile setting {value!r}")


def _pragmas(root: pathlib.Path, copied: Sequence[str], solc: str) -> dict[str, str]:
    """Each copied source that pins one compiler version, with the pin moved to `solc`."""
    found = {}
    for entry in copied:
        paths = [root / entry] if (root / entry).is_file() else sorted((root / entry).rglob("*.sol"))
        for path in paths:
            if path.suffix != ".sol":
                continue
            source = path.read_text()
            pinned = EXACT_PRAGMA.sub(f"pragma solidity {solc};", source)
            if pinned != source:
                found[path.relative_to(root).as_posix()] = pinned
    return found


def table(records: dict) -> str:
    """The records as a Markdown table: one row for each variant and spec."""
    rows = ["| Variant | Spec | solc | Bytecode | Theorems | Vectors | Link | Run |", "|---" * 8 + "|"]
    for name, record in records.items():
        for spec in record["specs"]:
            code = spec.get("bytecode") or {"size": "?", "sha256": "?"}
            verdicts = [t["verdict"] for t in spec.get("theorems", [])]
            theorems = ", ".join(f"{verdicts.count(v)} {v.lower()}" for v in dict.fromkeys(verdicts)) or "none"
            vectors = spec.get("vectors") or {}
            agree = f"{vectors.get('agree')} of {vectors.get('count')}" if vectors else "not run"
            link = ", ".join(sorted({r["verdict"] for r in spec.get("link") or []})) or "not run"
            solc = spec.get("versions", {}).get("solc", "?").partition("+")[0]
            sha = code["sha256"][:12]
            rows.append(
                f"| {name} | {spec['spec']} | {solc} | {code['size']} B, `{sha}` | {theorems} | {agree} | {link} | "
                f"{record['seconds']}s |"
            )
    return "\n".join(rows)


def queries(records: dict) -> str:
    """The sha256 of each theorem's query, which the check requires to be the same in every variant."""
    first = next(iter(records.values()))
    lines = [f"{s['spec']} {t['id']}: {t['query_sha256'][:16]}" for s in first["specs"] for t in s.get("theorems", [])]
    return "\n".join(lines)


def vault_specs() -> list[Spec]:
    """The vault example's specs, at its root in this repo, imported without writing bytecode into it."""
    sys.path.insert(0, str(VAULT))
    writes, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        module = importlib.import_module("vault_specs.spec")
    finally:
        sys.dont_write_bytecode = writes
        sys.path.remove(str(VAULT))
    return [dataclasses.replace(s, project=dataclasses.replace(s.project, root=VAULT)) for s in module.SPECS]


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="The vault example under other compilers and compiler settings.")
    parser.add_argument("--workspace", type=pathlib.Path, default=pathlib.Path("build") / "compilers")
    parser.add_argument("--results", type=pathlib.Path, help="where to write the record (default: in the workspace)")
    args = parser.parse_args(argv)
    workspace = Workspace(args.workspace)
    records, failures = across(vault_specs(), VAULT_VARIANTS, workspace)
    print(table(records) + "\n\n" + queries(records))
    path = args.results or workspace.path / "compilers.json"
    path.write_text(json.dumps({"ok": not failures, "failures": failures, "variants": records}, indent=2) + "\n")
    for failure in failures:
        print(f"FAILED {failure}")
    print(f"{'ok' if not failures else f'{len(failures)} failure(s)'}; record: {path}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
