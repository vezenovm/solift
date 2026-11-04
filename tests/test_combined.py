"""The extractor's features together, across the PRs that added them: memory structs, keys on assigned locals, cuts
that take new memory, checks called as statements and modifiers."""

import pathlib

import pytest

from solift import forge
from solift.extract import ExtractionError, extract
from tests.test_smt import evaluate

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMBINED = "tests/fixtures/contracts/Combined.sol:Combined"


@pytest.fixture(scope="module")
def build(tmp_path_factory):
    work = tmp_path_factory.mktemp("forge")
    return forge.build(ROOT, work / "out", work / "cache")


@pytest.mark.parametrize(
    "function, reads, values, result",
    [
        ("quotedOnce", {"quotes.quote(amount)": "quote"}, {"_a": 1, "_b": 2, "quote": 9}, 9),
        ("inlinedAndCut", {"Spans.opened(Span(_t,_t),_s)": "cut"}, {"_s": 3, "_t": 5, "cut": 4}, 6),
        ("guarded", {"quotes.quote(_n)": "quote"}, {"_x": 3, "quote": 0}, 3),
    ],
)
def test_the_features_together_extract(build, function, reads, values, result):
    model = extract(build, f"{COMBINED}.{function}", reads).model
    assert evaluate(model, values) == (False, result, False)


@pytest.mark.parametrize(
    "function, reads, message",
    [
        # A key on a local that the callee assigns, in a callee that takes a struct and runs twice.
        ("quotedTwice", {"quotes.quote(amount)": "quote"}, r"_quoteFor runs 2 times in the call"),
        # The check runs in the modifier and in the body, with a key on its parameter.
        ("guardedAndChecked", {"quotes.quote(_n)": "quote"}, r"_checkQuote runs 2 times in the call"),
    ],
)
def test_the_features_together_keep_their_refusals(build, function, reads, message):
    with pytest.raises(ExtractionError, match=message):
        extract(build, f"{COMBINED}.{function}", reads)
