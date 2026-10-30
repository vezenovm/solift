import dataclasses

import pytest

from tests.projects import QUOTER, QUOTER_READS, fixtures
from z3sol import forge, ops
from z3sol.extract import extract
from z3sol.ir import UINT256, Const, Function, If, Model, Return, Var
from z3sol.solidity import source
from z3sol.vectors import VectorError, sample, vectors_source
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
    return forge.test(root, workspace.fresh(f"{name}-out"), workspace.fresh(f"{name}-cache"), "QuoterQuoteVectors")


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
