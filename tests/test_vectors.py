import dataclasses

import pytest
import z3

from tests.projects import QUOTER, QUOTER_READS, fixtures
from z3sol import forge, ops
from z3sol.extract import extract
from z3sol.ir import ADDRESS, UINT256, Const, Function, If, Model, Return, Var, enum
from z3sol.solidity import names, source
from z3sol.vectors import VectorError, boundary, sample, vectors_source
from z3sol.workspace import Workspace


@pytest.fixture(scope="module")
def quoter(tmp_path_factory):
    workspace = Workspace(tmp_path_factory.mktemp("vectors") / "work")
    project = fixtures()
    root = project.overlay(workspace)
    infos = forge.build(root, workspace.fresh("ast-out"), workspace.fresh("ast-cache"))
    return workspace, project, root, extract(infos, f"{QUOTER}:Quoter._quote", QUOTER_READS).model


def run_vectors(workspace, project, root, model, vectors, name):
    (root / project.generated / "QuoterQuote.sol").write_text(source(model))
    (root / project.generated / "QuoterQuoteVectors.t.sol").write_text(vectors_source(model, vectors))
    out, cache = workspace.fresh(f"{name}-out"), workspace.fresh(f"{name}-cache")
    statuses = forge.test(root, out, cache, ("QuoterQuoteVectors",))
    return {test: status for (_, _, test), status in statuses.items()}


def test_the_vectors_reach_each_outcome_and_the_solidity_gives_the_z3_result_on_each(quoter):
    workspace, project, root, model = quoter
    vectors = sample(model)
    assert {v.outcome for v in vectors} == {"pays", "zero", "reverts"}
    statuses = run_vectors(workspace, project, root, model, vectors, "right")
    assert len(statuses) == len(vectors)
    assert set(statuses.values()) == {"Success"}


def test_the_vectors_find_a_wrong_row_of_the_operator_table(quoter, monkeypatch):
    workspace, project, root, model = quoter
    monkeypatch.setitem(ops.TABLE, "sub", dataclasses.replace(ops.TABLE["sub"], value=lambda a, b: a - b + 1))
    vectors = sample(model)
    statuses = run_vectors(workspace, project, root, model, vectors, "wrong")
    assert "Failure" in set(statuses.values())


def test_an_outcome_that_no_input_reaches_must_be_declared():
    a = Var("a", input=True)
    model = Model("C", Function("f", (a,), UINT256, (Return(ops.make("add", ops.make("div", a, Const(4)), Const(1))),)))
    with pytest.raises(VectorError, match="neither random inputs nor z3 reached zero"):
        sample(model)
    assert {v.outcome for v in sample(model, impossible=("zero", "reverts"))} == {"pays"}
    with pytest.raises(VectorError, match="the spec calls pays impossible, but"):
        sample(model, impossible=("pays", "zero", "reverts"))


def test_z3_finds_an_input_for_an_outcome_that_no_random_input_reaches():
    """if (a != 123456789) return 1; return a + (2**256 - 123456789): only a = 123456789 reverts."""
    a = Var("a", input=True)
    body = (
        If(ops.make("ne", a, Const(123456789)), (Return(Const(1)),)),
        Return(ops.make("add", a, Const(2**256 - 123456789))),
    )
    vectors = sample(Model("C", Function("f", (a,), UINT256, body)), impossible=("zero",))
    assert [v.values["a"] for v in vectors if v.reverts] == [123456789]


def test_an_unknown_from_z3_never_counts_as_an_unreachable_outcome(monkeypatch):
    a = Var("a", input=True)
    model = Model("C", Function("f", (a,), UINT256, (Return(ops.make("add", ops.make("div", a, Const(4)), Const(1))),)))
    monkeypatch.setattr(z3.Solver, "check", lambda self, *args: z3.unknown)
    with pytest.raises(VectorError, match="z3 could not decide whether an input reaches zero"):
        sample(model, impossible=("zero", "reverts"))
    with pytest.raises(VectorError, match="z3 gave unknown at the boundary of a comparison"):
        boundary(Model("C", Function("f", (a,), UINT256, (Return(ops.make("min", a, Const(4))),))))


def test_the_vectors_compare_an_address_or_an_enum_result(quoter):
    workspace, project, root, _ = quoter
    kind = Var("kind", enum("Kinds.Kind", 4), input=True)
    who = Var("who", ADDRESS, input=True)
    kinds = ('import {Kinds} from "tests/fixtures/contracts/Kinds.sol";',)
    models = [
        Model("Identity", Function("_address", (who,), ADDRESS, (Return(who),)), pragma="^0.8.27"),
        Model("Identity", Function("_kind", (kind,), kind.type, (Return(kind),)), imports=kinds, pragma="^0.8.27"),
    ]
    contracts = []
    for model in models:
        stem = names(model)[0].removesuffix("Model")
        (root / project.generated / f"{stem}.sol").write_text(source(model))
        vectors = sample(model, impossible=("reverts",))
        (root / project.generated / f"{stem}Vectors.t.sol").write_text(vectors_source(model, vectors))
        contracts.append(f"{stem}Vectors")
    statuses = forge.test(root, workspace.fresh("typed-out"), workspace.fresh("typed-cache"), tuple(contracts))
    assert {c for _, c, _ in statuses} == set(contracts)
    assert set(statuses.values()) == {"Success"}
