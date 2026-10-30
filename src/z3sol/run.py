"""A run: extract the model, prove its theorems, check its vectors, link it to the bytecode, and record each result with
the versions it rests on.

Each stage runs even when an earlier one fails, except that nothing runs without a model, so one run shows every
failure. The run fails when a result is not the expected one, and when the project changed during the run.
"""

from __future__ import annotations

import dataclasses
import datetime
import hashlib
import importlib.metadata
import json
import pathlib
import platform
import subprocess
from dataclasses import dataclass, field

import z3

from z3sol import forge
from z3sol.extract import ExtractionError, extract
from z3sol.halmos import HalmosEngine
from z3sol.link import Link, LinkError, generate
from z3sol.solidity import names
from z3sol.theorem import Terms, TheoremError, prove, theorem
from z3sol.vectors import VectorError, sample, vectors_source
from z3sol.workspace import Project, Workspace

MATH_PATH = "utils/math/Math.sol"


@dataclass
class Spec:
    """What a run checks: the target function, its read map, its link, and its theorems. `impossible` names the
    outcomes (pays, zero, reverts) that no input reaches, which the vectors then do not require."""

    name: str
    target: str
    reads: dict[str, str]
    link: Link
    project: Project
    theorems: list = field(default_factory=list)
    impossible: tuple[str, ...] = ()

    def theorem(self, **options):
        def wrap(goal):
            declared = theorem(**options)(goal)
            if any(t.id == declared.id for t in self.theorems):
                raise TheoremError(f"two theorems are named {declared.id}")
            self.theorems.append(declared)
            return declared

        return wrap


def run(
    spec: Spec,
    workspace: Workspace,
    replace: dict[str, str] | None = None,
    link: bool = True,
    vectors: bool = True,
    emit: pathlib.Path | None = None,
    only: tuple[str, ...] | None = None,
    engine: HalmosEngine | None = None,
) -> dict:
    """Runs the spec and returns its record. `replace` gives new content for project files, such as a mutant. Without
    `link` Halmos does not run, and without `vectors` neither do the vectors, and the record says so. `emit` is a
    directory to write the model and its link tests to, for review. `only` names the theorems to prove, when a run
    needs only some of them, as a mutant does."""
    engine = engine or HalmosEngine()
    project = spec.project
    record = {
        "spec": spec.name,
        "target": spec.target,
        "started": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
        "project": _project(project.root),
        "replaced": sorted(replace or {}),
        "failures": [],
    }
    guard = project.guard(workspace, exclude=(emit,) if emit is not None else ())
    allow = (project.root,)
    try:
        root = project.overlay(workspace, replace=replace)
        infos = forge.build(root, workspace.fresh("ast-out"), workspace.fresh("ast-cache"), project.profile, allow)
        extraction = extract(infos, spec.target, spec.reads)
        model = extraction.model
        generated = generate(model, spec.link)
    except (forge.BuildError, ExtractionError, LinkError) as err:
        record["failures"].append(f"extract: {err}")
        return _finish(record, guard)
    stem = names(model)[0].removesuffix("Model")
    info = next(i for i in infos if spec.target.rpartition(":")[0] in i["output"]["sources"])
    record["versions"] = _versions(info, spec.target, extraction.sources, engine)
    record["sources"] = extraction.sources
    record["model"] = {
        "inputs": [{"name": v.name, "type": v.type.solidity} for v in model.inputs],
        "reads": {r.var.name: r.source for r in model.reads},
        "callees": [f.name for f in model.callees],
    }
    files = dict(generated.files)
    if emit is not None:
        emit.mkdir(parents=True, exist_ok=True)
        for name, text in generated.files.items():
            (emit / name).write_text(text)

    sampled = None
    if vectors:
        try:
            sampled = sample(model, spec.impossible)
            files[f"{stem}Vectors.t.sol"] = vectors_source(model, sampled, spec.link.test_import)
        except VectorError as err:
            record["failures"].append(f"vectors: {err}")
    for name, text in files.items():
        (root / project.generated / name).write_text(text)
    record["generated"] = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in files.items()}

    q = Terms(model)
    record["theorems"] = []
    chosen = spec.theorems
    if only is not None:
        unknown = set(only) - {t.id for t in spec.theorems}
        if unknown:
            record["failures"].append(f"theorems: no theorem {', '.join(sorted(unknown))}")
        chosen = [t for t in spec.theorems if t.id in only]
        record["only"] = list(only)
    for t in chosen:
        try:
            result = prove(t, q)
        except TheoremError as err:
            record["failures"].append(f"theorem {t.id}: {err}")
            continue
        record["theorems"].append(dataclasses.asdict(result))
        if not result.ok:
            witness = f", witness {result.witness}" if result.witness not in (None, "WITNESS") else ""
            expected = "PROVED" if result.expected == "proved" else "COUNTEREXAMPLE"
            record["failures"].append(f"theorem {t.id}: {result.verdict}{witness}, expected {expected}")

    record["vectors"] = None
    if sampled is not None:
        statuses = forge.test(
            root,
            workspace.fresh("vectors-out"),
            workspace.fresh("vectors-cache"),
            f"{stem}Vectors",
            project.profile,
            allow,
        )
        agree = sum(status == "Success" for status in statuses.values())
        record["vectors"] = {"count": len(sampled), "agree": agree}
        if agree != len(sampled) or len(statuses) != len(sampled):
            record["failures"].append(f"vectors: {agree} of {len(sampled)} agree with the emitted Solidity")

    if link:
        linked = engine.run(root, workspace, generated.checks, project.profile, allow)
        record["link"] = {
            "engine": engine.name,
            "results": [
                {
                    "contract": r.check.contract,
                    "check": r.check.function,
                    "verdict": r.verdict,
                    "time": r.time,
                    "paths": r.paths,
                }  # fmt: skip
                for r in linked.results
            ],
            "problems": list(linked.problems),
        }
        record["failures"] += [f"link: {p}" for p in linked.problems]
        record["failures"] += [
            f"link: {r.check.contract}.{r.check.function} {r.verdict}" for r in linked.results if not r.ok
        ]
    else:
        record["link"] = None
    return _finish(record, guard)


def _finish(record: dict, guard) -> dict:
    record["failures"] += [f"project: {change}" for change in guard.changes()]
    record["ok"] = not record["failures"]
    return record


def _project(root: pathlib.Path) -> dict:
    def git(*args):
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        return result.stdout.strip() if result.returncode == 0 else None

    commit = git("rev-parse", "HEAD")
    return {"root": str(root), "commit": commit, "dirty": bool(git("status", "--porcelain")) if commit else None}


def _versions(info: dict, target: str, sources: dict[str, str], engine) -> dict:
    path, _, qualified = target.rpartition(":")
    contract = qualified.partition(".")[0]
    metadata = json.loads(info["output"]["contracts"][path][contract]["metadata"])
    versions = {
        "solc": metadata["compiler"]["version"],
        "python": platform.python_version(),
        "z3sol": importlib.metadata.version("z3sol"),
        "z3 (theorems)": z3.get_full_version(),
        **engine.versions(),
    }
    for source in sources:
        if source.endswith(MATH_PATH):
            content = info["input"]["sources"][source]["content"]
            header = next((line for line in content.splitlines() if "OpenZeppelin Contracts" in line), "")
            blob = hashlib.sha1(f"blob {len(content.encode())}\0".encode() + content.encode()).hexdigest()
            versions["OpenZeppelin"] = {
                "header": header.lstrip("/ ").strip(),
                "path": source,
                "sha256": sources[source],
                "git blob": blob,
            }
    return versions


def summary(record: dict) -> str:
    """The record as lines for a terminal."""
    lines = [f"== {record['spec']}: {record['target']}"]
    if "model" in record:
        model = record["model"]
        reads = ", ".join(f"{name} = {source}" for name, source in model["reads"].items())
        lines.append(f"model: {len(model['inputs'])} inputs; reads {reads}")
    for t in record.get("theorems", []):
        mark = "" if t["ok"] else "   <-- unexpected"
        witness = f" {t['witness']}" if t["witness"] else ""
        shown = " ".join(f"{k}={v}" for k, v in t["counterexample"].items())
        lines.append(f"{t['verdict']}{witness} [{t['id']}] {t['statement']} [{t['time']:.2f}s] {shown}{mark}".rstrip())
    if record.get("vectors"):
        lines.append(f"vectors: {record['vectors']['agree']} of {record['vectors']['count']} agree")
    elif "vectors" in record:
        lines.append("vectors: not run")
    if record.get("link"):
        for r in record["link"]["results"]:
            time_ = f" [{r['time']:.2f}s]" if r["time"] is not None else ""
            lines.append(f"{r['verdict']} {r['contract']}.{r['check']}{time_}")
    elif "link" in record:
        lines.append("link: not run")
    lines += [f"FAILED {failure}" for failure in record["failures"]]
    lines.append("ok" if record["ok"] else f"{len(record['failures'])} failure(s)")
    return "\n".join(lines)
