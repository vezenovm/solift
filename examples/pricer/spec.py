"""Example specs: Pricer._priced and Pricer.quoteFor from tests/fixtures/contracts/Pricer.sol, with the harness in
tests/fixtures/test/PricerSetup.sol. Run them with the Quoter from the repo root with `python -m examples.pricer`."""

import z3

from examples.quoter.spec import PROJECT
from solift.link import Link
from solift.run import Spec

PRICER = "tests/fixtures/contracts/Pricer.sol"
SETUP = "tests/fixtures/test/PricerSetup.sol:PricerSetup"

PRICED = Spec(
    name="pricer-priced",
    target=f"{PRICER}:Pricer._priced",
    reads={
        "_meter.used": "used",
        "_meter.fresh": "fresh",
        "_meter.paid": "paid",
        "TOKEN": "token",
        "TOKEN.balanceOf(tx.origin)": "originBalance",
    },
    link=Link(
        setup=SETUP,
        call="pricer.exposedPriced(_kind, _token, meter)",
        adapters={
            ("used", "fresh", "paid"): (
                "Pricer.Meter memory meter = Pricer.Meter({startGas: 0, used: used, fresh: fresh, paid: paid});"
            ),
            "originBalance": "token.set(tx.origin, originBalance);",
        },
        observed={"token": "address(pricer.TOKEN())"},
        imports=(f'import {{Pricer}} from "{PRICER}";',),
    ),
    project=PROJECT,
)

QUOTE_FOR = Spec(
    name="pricer-quote-for",
    target=f"{PRICER}:Pricer.quoteFor",
    reads={"$rates[_kind].floor": "floor", "$rates[_kind].cap": "cap", "_price()": "price"},
    link=Link(
        setup=SETUP,
        call="pricer.quoteFor(_kind)",
        adapters={("floor", "cap"): "pricer.setRate(_kind, floor, cap);", "price": "token.set(address(0xFEE), price);"},
    ),
    project=PROJECT,
)

SPECS = [PRICED, QUOTE_FOR]


def priced(q):
    return q.result > 0


@PRICED.theorem(witness=priced)
def priced_at_most_the_gross(q):
    """the priced gas is at most the gross gas, and never reverts below 2**200 gas"""
    return z3.And(z3.Implies(q.used < 2**200, z3.Not(q.reverts)), z3.Implies(z3.Not(q.reverts), q.result <= q.gross))


@PRICED.theorem(assume=("no-revert",), witness=priced)
def priced_refund_is_at_most_a_fifth(q):
    """the refund is at most a fifth of the gross gas, and the estimate is at least 400 gas"""
    return z3.And(q.refund * 5 <= q.gross, q.declared("refund") >= 400)


@QUOTE_FOR.theorem(assume=("no-revert",), witness=priced)
def quote_at_most_the_units_at_the_price(q):
    """the quote is at most the units of the kind at the price"""
    return q.result <= q.call("units", q._kind).value * q.price
