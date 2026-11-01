import os
import re
import subprocess
import time

import pytest

from acceptance.resupply import direct
from acceptance.resupply.direct import CHECKS, PARAMETERS, counterexample, problems, queries, result
from acceptance.resupply.solvency import SOLVENT

TEST = (direct.HARNESS / direct.TEST).read_text()


def test_each_check_is_a_function_of_the_test_with_the_links_parameters():
    link = [name for name in SOLVENT.reads.values() if name not in SOLVENT.link.observed]
    assert [name for name, _ in PARAMETERS] == ["_borrower", *link, *SOLVENT.link.parameters]
    assert {name: type_ for name, type_ in PARAMETERS if name in SOLVENT.link.parameters} == SOLVENT.link.parameters
    for check in CHECKS.values():
        match = re.search(rf"function {check.function}\(([^)]*)\)", TEST)
        assert match, check.function
        declared = [tuple(p.split()[::-1]) for p in match.group(1).split(",")]
        assert declared == list(PARAMETERS)
    assert {c.theorem for c in CHECKS.values()} == {t.id for t in SOLVENT.theorems}
    assert [c.cap for c in CHECKS.values()] == [2**256 - 1, 10**36, 2**256 - 1]
    assert [c.solift for c in CHECKS.values()] == ["COUNTEREXAMPLE", "PROVED", "PROVED"]


def test_a_counterexample_takes_each_parameter_from_the_name_of_its_variable():
    def variable(full_name, value):
        return {"full_name": full_name, "variable_name": "", "solidity_type": "", "smt_type": "BitVec 256",
                "size_bits": 256, "value": value}  # fmt: skip

    model = {
        "p__borrower_address_1852395_00": variable("p__borrower_address_1852395_00", 9),
        "p_price_uint256_8e8e310_00": variable("p_price_uint256_8e8e310_00", 5),
        "p_totalShares_uint128_d2b5cfb_00": variable("p_totalShares_uint128_d2b5cfb_00", 7),
        "halmos_x_00": variable("halmos_x_00", 3),
    }
    found = counterexample({"model": model, "is_valid": True})
    assert found["valid"] is True
    assert found["values"] == {"_borrower": 9, "price": 5, "storedRate": 0, "maxLTV": 0, "collateral": 0,
                               "totalAmount": 0, "totalShares": 7, "shares": 0}  # fmt: skip


def test_the_queries_count_the_answers_of_first_and_refined_queries(tmp_path):
    directory = tmp_path / "check_x"
    directory.mkdir()
    for name, out in (("1", "sat\n(model)"), ("1.refined", "unsat\n"), ("2", ""), ("3", None)):
        (directory / f"{name}.smt2").write_text("(check-sat)")
        if out is not None:
            (directory / f"{name}.smt2.out").write_text(out)
    stopped = tmp_path / "check_x-error"
    stopped.mkdir()
    (stopped / "3.smt2").write_text("(check-sat)")
    assert queries(tmp_path) == {"first": {"sat": 1, "unanswered": 2}, "refined": {"unsat": 1}}


def record(verdict, replays=()):
    return {"verdict": verdict, "replays": [{"holds": holds} for holds in replays], "command": ["halmos.json"]}


def test_a_result_is_a_counterexample_only_when_one_holds_and_it_fails_when_it_contradicts_solift():
    exploit, fixed = CHECKS["exploit"], CHECKS["fix"]
    for found, expected in (
        (record("PASS"), "PROVED"),
        (record("COUNTEREXAMPLE", (False, True)), "COUNTEREXAMPLE"),
        (record("COUNTEREXAMPLE", (False,)), "INVALID COUNTEREXAMPLE"),
        (record("TIMEOUT"), "TIMEOUT"),
        (record("EXCEPTION"), "EXCEPTION"),
    ):
        found["result"] = result(found)
        assert found["result"] == expected
    assert problems(exploit, {"result": "COUNTEREXAMPLE"}) == []
    assert problems(fixed, {"result": "PROVED"}) == []
    assert problems(fixed, {"result": "TIMEOUT"}) == []
    assert problems(fixed, {"result": "INVALID COUNTEREXAMPLE"}) == []
    assert problems(fixed, {"result": "COUNTEREXAMPLE"}) == ["Halmos gives COUNTEREXAMPLE and solift PROVED"]
    assert problems(exploit, {"result": "PROVED"}) == ["Halmos gives PROVED and solift COUNTEREXAMPLE"]
    assert problems(fixed, record("EXCEPTION") | {"result": "EXCEPTION"})


def test_the_budget_ends_the_process_group_with_its_children():
    process = subprocess.Popen(["sh", "-c", "sleep 60 & sleep 60 & wait"], start_new_session=True)
    time.sleep(0.5)
    direct._kill(process)
    assert process.poll() is not None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    pytest.fail("a child of the process outlived the budget")
