"""The spec of the check that Resupply's borrow makes, and the theorem that finds the exploit of 2025-06-26.

borrow stores the exchange rate that _updateExchangeRate returns, and its isSolvent modifier then checks the borrower
with _isSolvent at the stored rate. The harness's solventAfterUpdate makes the two calls in that order
(harness/ResupplySetup.sol), so its model is the two functions of the pair: _updateExchangeRate takes the price from
the oracle and computes the rate as 1e36 / price, and _isSolvent compares the borrower's loan-to-value at that rate
with maxLTV. That the modifier reads the rate that _updateExchangeRate stored is a hypothesis: the tool does not model
storage writes. The borrower's debt, `toAmount` of their borrow shares, is a cut point.

In the exploit, the collateral was a share of an empty vault, and a donation to the vault raised its price above 1e36.
The rate rounded down to 0, and at a rate of 0 one wei of collateral backs any debt. The fix, resupplyfi/resupply
d84b3bf631, reverts on a price above 1e22 (fix.py).
"""

import z3

from acceptance.resupply.project import PROJECT
from solift.link import Link
from solift.run import Spec

LTV_PRECISION = 10**5
EXCHANGE_PRECISION = 10**18

SOLVENT = Spec(
    name="resupply-solvent",
    target="_/ResupplySetup.sol:ResupplyPairHarness.solventAfterUpdate",
    reads={
        "IOracle(_exchangeRateInfo.oracle).getPrices(address(collateral))": "price",
        "exchangeRateInfo.exchangeRate": "storedRate",
        "maxLTV": "maxLTV",
        "_userCollateralBalance[_borrower]": "collateral",
        "totalBorrow.toAmount(_userBorrowShares[_borrower], true)": "borrowed",
    },
    link=Link(
        setup="_/ResupplySetup.sol:ResupplySetup",
        call="pair.solventAfterUpdate(_borrower)",
        adapters={
            "price": "stub.setPrice(price);",
            ("storedRate", "maxLTV", "collateral", "totalAmount", "totalShares", "shares"): (
                "pair.set(_borrower, maxLTV, storedRate, collateral, totalAmount, totalShares, shares);"
            ),
        },
        observed={"borrowed": "pair.borrowerAmount(_borrower)"},
        parameters={"totalAmount": "uint128", "totalShares": "uint128", "shares": "uint256"},
    ),
    project=PROJECT,
)


def lent(q):
    """the pair has a loan-to-value limit (maxLTV 0 turns the check off) and the borrower has debt"""
    return z3.And(q.maxLTV > 0, q.borrowed > 0)


def lent_below_a_price_of_1e36(q):
    """the pair has a loan-to-value limit, the borrower has debt, and the oracle price is at most 1e36"""
    return z3.And(lent(q), q.price <= 10**36)


def solvent(q):
    return q.result


def within_twice_the_ltv(q):
    """debt * LTV_PRECISION < 2 * (maxLTV + 1) * collateral * price / 1e18, where the result is solvent"""
    value = q.collateral * q.price
    return z3.Implies(q.result, q.borrowed * LTV_PRECISION * EXCHANGE_PRECISION < 2 * (q.maxLTV + 1) * value)


@SOLVENT.theorem(
    assume=("no-revert",),
    given=lent,
    witness=solvent,
    expect="counterexample",
    reason="the exploit of 2025-06-26: a price above 1e36 rounds the exchange rate down to 0, and at a rate of 0 one "
    "wei of collateral backs any debt",
    issue="resupplyfi/resupply@d84b3bf631",
    show=("price", "collateral", "borrowed", "maxLTV"),
)
def solvent_debt_within_twice_the_ltv(q):
    """a borrower that the pair finds solvent has debt below twice (maxLTV + 1) / LTV_PRECISION of the collateral's
    value at the oracle price"""
    return within_twice_the_ltv(q)


@SOLVENT.theorem(assume=("no-revert",), given=lent_below_a_price_of_1e36, witness=solvent)
def solvent_debt_within_twice_the_ltv_up_to_a_price_of_1e36(q):
    """up to an oracle price of 1e36, a borrower that the pair finds solvent has debt below twice (maxLTV + 1) /
    LTV_PRECISION of the collateral's value at that price"""
    return within_twice_the_ltv(q)
