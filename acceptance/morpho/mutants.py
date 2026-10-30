"""Mutants of Morpho Blue's health check and share math (acceptance/mutants.py)."""

from acceptance.mutants import Mutant

MORPHO = "src/Morpho.sol"
SHARES = "src/libraries/SharesMathLib.sol"

MUTANTS = [
    Mutant(
        "morpho-debt-rounded-down",
        "uint256 borrowed = uint256(position[id][borrower].borrowShares).toAssetsUp(",
        "uint256 borrowed = uint256(position[id][borrower].borrowShares).toAssetsDown(",
        breaks="healthy-debt-within-the-lltv",
        holds=("mul-div-down-rounds-down",),
        spec="morpho-healthy",
        path=MORPHO,
        function="_isHealthy",
        link=True,
    ),
    Mutant(
        "morpho-supply-shares-rounded-up",
        "return assets.mulDivDown(totalShares + VIRTUAL_SHARES, totalAssets + VIRTUAL_ASSETS);",
        "return assets.mulDivUp(totalShares + VIRTUAL_SHARES, totalAssets + VIRTUAL_ASSETS);",
        breaks="supply-then-withdraw-pays-at-most-the-supply",
        spec="morpho-supply-withdraw",
        path=SHARES,
        function="toSharesDown",
    ),
]
