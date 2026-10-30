"""The spec of Morpho Blue's health check, _isHealthy at a given collateral price, and its theorems.

_isHealthy values the debt with toAssetsUp of the borrower's shares and the collateral with mulDivDown and wMulDown, so
both round against the borrower. The theorem is the one that fails for Resupply (acceptance/resupply/solvency.py): a
healthy position's debt, valued exactly, is at most lltv of the collateral's value at the oracle price. Morpho takes
the price as the oracle gives it, so no rate rounds to 0.

The two theorems on MathLib are Certora's checkSummaryMulDivUp and checkSummaryMulDivDown (certora/specs/
LibSummary.spec), which Morpho's Certora specs of health use as summaries of mulDivUp and mulDivDown. Here they are
theorems of the integer model of the two functions on any uint256 operands. The link proves the functions equal to the
bytecode only where _isHealthy calls them, so the record lists them as callees.
"""

import z3

from acceptance.morpho.project import PROJECT
from solift.link import Link
from solift.run import Spec

WAD = 10**18
ORACLE_PRICE_SCALE = 10**36
VIRTUAL_SHARES = 10**6
VIRTUAL_ASSETS = 1

HEALTHY = Spec(
    name="morpho-healthy",
    target="src/Morpho.sol:Morpho._isHealthy(MarketParams,Id,address,uint256)",
    reads={
        "marketParams.lltv": "lltv",
        "position[id][borrower].borrowShares": "borrowShares",
        "position[id][borrower].collateral": "collateral",
        "market[id].totalBorrowAssets": "totalBorrowAssets",
        "market[id].totalBorrowShares": "totalBorrowShares",
    },
    link=Link(
        setup="test/MorphoSetup.sol:MorphoSetup",
        call="morpho.exposedIsHealthy(marketParams, id, borrower, collateralPrice)",
        adapters={
            "lltv": "MarketParams memory marketParams = MarketParams(NONE, NONE, NONE, NONE, lltv);",
            ("borrowShares", "collateral", "totalBorrowAssets", "totalBorrowShares"): (
                "morpho.setPosition(id, borrower, borrowShares, collateral, totalBorrowAssets, totalBorrowShares);"
            ),
        },
        parameters={"id": "Id"},
        imports=('import {Id, MarketParams} from "src/interfaces/IMorpho.sol";',),
    ),
    project=PROJECT,
)


def borrowing(q):
    return z3.And(q.result, q.borrowShares > 0)


@HEALTHY.theorem(assume=("no-revert",), witness=borrowing)
def healthy_debt_within_the_lltv(q):
    """a position that Morpho finds healthy has debt, borrowShares * (totalBorrowAssets + 1) / (totalBorrowShares +
    1e6), at most lltv / 1e18 of the collateral's value at the oracle price, collateral * price / 1e36"""
    debt = q.borrowShares * (q.totalBorrowAssets + VIRTUAL_ASSETS) * ORACLE_PRICE_SCALE * WAD
    limit = q.collateral * q.collateralPrice * q.lltv * (q.totalBorrowShares + VIRTUAL_SHARES)
    return z3.Implies(q.result, debt <= limit)


def runs(name):
    def witness(q):
        return z3.And(q.free("d") > 0, z3.Not(q.call(name, q.free("x"), q.free("y"), q.free("d")).reverts))

    return witness


@HEALTHY.theorem(witness=runs("mulDivUp"))
def mul_div_up_rounds_up(q):
    """where it does not revert, mulDivUp(x, y, d) * d >= x * y: Certora's checkSummaryMulDivUp"""
    x, y, d = (q.free(name, "uint256") for name in ("x", "y", "d"))
    call = q.call("mulDivUp", x, y, d)
    return z3.Or(call.reverts, call.value * d >= x * y)


@HEALTHY.theorem(witness=runs("mulDivDown"))
def mul_div_down_rounds_down(q):
    """where it does not revert, mulDivDown(x, y, d) * d <= x * y: Certora's checkSummaryMulDivDown"""
    x, y, d = (q.free(name, "uint256") for name in ("x", "y", "d"))
    call = q.call("mulDivDown", x, y, d)
    return z3.Or(call.reverts, call.value * d <= x * y)
