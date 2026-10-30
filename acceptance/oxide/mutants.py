"""Mutants of _quoteSubsidy. Each changes one expression of the function, and a theorem must reject it: the run of the
mutant fails on that theorem. The model follows the contract, so the link of a mutant still passes. The mutants marked
`link` show that with a Halmos run."""

from dataclasses import dataclass

PATH = "src/periphery/DepositSubsidy.sol"
FUNCTION = "function _quoteSubsidy("


@dataclass(frozen=True)
class Mutant:
    """`breaks` is the theorem that must fail, and `holds` the theorems that must still prove."""

    id: str
    old: str
    new: str
    breaks: str
    holds: tuple[str, ...] = ()
    link: bool = False


GAS_PRICE = "Math.min(tx.gasprice, block.basefee + OxideConstants.MAX_PRIORITY_FEE_WEI)"

MUTANTS = [
    Mutant(
        "profit-not-capped-by-the-fee",
        "Math.min(cfg.targetRelayerProfit, _fee)",
        "cfg.targetRelayerProfit",
        breaks="deposit-no-farming",
        link=True,
    ),
    Mutant(
        "fee-not-subtracted",
        "Math.min(targetPayout - _fee, cfg.maxSubsidyPerOperation)",
        "Math.min(targetPayout, cfg.maxSubsidyPerOperation)",
        breaks="deposit-no-farming",
    ),
    Mutant(
        "gas-price-not-capped-by-tx-gasprice",
        GAS_PRICE,
        "block.basefee + OxideConstants.MAX_PRIORITY_FEE_WEI",
        breaks="deposit-no-farming",
    ),
    Mutant(
        "token-price-ten-times",
        "Math.mulDiv(_reimbursableGas * gasPrice, _tokenPerEth, 1e18)",
        "Math.mulDiv(_reimbursableGas * gasPrice, _tokenPerEth, 1e17)",
        breaks="deposit-no-farming",
    ),
    Mutant(
        "one-gas-more",
        "_reimbursableGas * gasPrice",
        "(_reimbursableGas + 1) * gasPrice",
        breaks="deposit-no-farming",
    ),
    Mutant(
        "half-the-base-fee",
        GAS_PRICE,
        "Math.min(tx.gasprice, block.basefee / 2 + OxideConstants.MAX_PRIORITY_FEE_WEI)",
        breaks="deposit-liveness",
        holds=("deposit-no-farming",),
        link=True,
    ),
]


def apply(source: str, mutant: Mutant) -> str:
    """The source with the mutant applied. The changed text must occur exactly once, inside _quoteSubsidy."""
    start = source.index(FUNCTION)
    end = source.find("\n  function ", start + 1)
    body = source[start : end if end != -1 else len(source)]
    if source.count(mutant.old) != 1 or body.count(mutant.old) != 1:
        raise ValueError(f"{mutant.id}: {mutant.old!r} does not occur exactly once, inside _quoteSubsidy")
    return source.replace(mutant.old, mutant.new)
