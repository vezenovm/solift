"""A mutant of Resupply's solvency check (acceptance/mutants.py)."""

from acceptance.mutants import Mutant

LTV = "((_borrowerAmount * _exchangeRate * LTV_PRECISION) / EXCHANGE_PRECISION) / _collateralAmount"

MUTANTS = [
    Mutant(
        "resupply-ltv-of-half-the-debt",
        f"uint256 _ltv = {LTV};",
        f"uint256 _ltv = {LTV} / 2;",
        breaks="solvent-debt-within-twice-the-ltv-up-to-a-price-of-1e36",
        spec="resupply-solvent",
        path="src/protocol/pair/ResupplyPairCore.sol",
        function="_isSolvent",
        link=True,
    ),
]
