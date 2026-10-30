"""Mutants of the subsidy functions. Each changes one expression of one function, and a theorem must reject it: the run
of the mutant fails on that theorem, and the theorems in `holds` still prove. The model follows the contract, so the
link of a mutant still passes. The mutants marked `link` show that with a Halmos run."""

from dataclasses import dataclass

DEPOSIT = "src/periphery/DepositSubsidy.sol"
WITHDRAWAL = "src/periphery/PlainWithdrawalSubsidy.sol"


@dataclass(frozen=True)
class Mutant:
    """`spec` names the spec whose run must reject the mutant, `function` the function of `path` that it changes,
    `breaks` the theorem that must fail, and `holds` the theorems that must still prove."""

    id: str
    old: str
    new: str
    breaks: str
    holds: tuple[str, ...] = ()
    link: bool = False
    spec: str = "deposit-quote"
    path: str = DEPOSIT
    function: str = "_quoteSubsidy"


GAS_PRICE = "Math.min(tx.gasprice, block.basefee + OxideConstants.MAX_PRIORITY_FEE_WEI)"
PRICED_GAS = {"spec": "deposit-priced-gas", "function": "_pricedGas"}
WITHDRAWAL_QUOTE = {"spec": "withdrawal-quote", "path": WITHDRAWAL, "function": "quoteSubsidy"}

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
    Mutant(
        "refund-cap-a-sixth",
        "Math.min(refund, gross / 5)",
        "Math.min(refund, gross / 6)",
        breaks="priced-gas-at-most-gas-used",
        link=True,
        **PRICED_GAS,
    ),
    Mutant(
        "refund-not-subtracted",
        "Math.min(gross - refund, ceiling)",
        "Math.min(gross, ceiling)",
        breaks="priced-gas-at-most-gas-used",
        **PRICED_GAS,
    ),
    Mutant(
        "withdrawal-gas-price-not-capped-by-tx-gasprice",
        GAS_PRICE,
        "block.basefee + OxideConstants.MAX_PRIORITY_FEE_WEI",
        breaks="withdrawal-no-farming-against-the-model",
        **WITHDRAWAL_QUOTE,
    ),
    Mutant(
        "withdrawal-token-price-ten-times",
        "_tokenWeiPerEth(), 1e18)",
        "_tokenWeiPerEth(), 1e17)",
        breaks="withdrawal-no-farming-against-the-model",
        link=True,
        **WITHDRAWAL_QUOTE,
    ),
    Mutant(
        "withdrawal-one-gas-more",
        "modeledTxGas(_flow) * (gasPrice",
        "(modeledTxGas(_flow) + 1) * (gasPrice",
        breaks="withdrawal-no-farming-against-the-model",
        **WITHDRAWAL_QUOTE,
    ),
]


def apply(source: str, mutant: Mutant) -> str:
    """The source with the mutant applied. The changed text must occur exactly once in the file, inside the function."""
    start = source.index(f"function {mutant.function}(")
    end = source.find("\n  function ", start + 1)
    body = source[start : end if end != -1 else len(source)]
    if source.count(mutant.old) != 1 or body.count(mutant.old) != 1:
        raise ValueError(f"{mutant.id}: {mutant.old!r} does not occur exactly once, inside {mutant.function}")
    return source.replace(mutant.old, mutant.new)


def problems(mutant: Mutant, record: dict) -> list[str]:
    """Why the run of a mutant does not show that its theorem rejects it. The run must fail on `breaks` with a complete
    counterexample and a witness, and on nothing else: each theorem in `holds` proves with a witness, and every other
    check passes."""
    (spec,) = record["specs"]
    theorems = {t["id"]: t for t in spec.get("theorems", [])}
    expected = f"{spec['spec']}: theorem {mutant.breaks}: COUNTEREXAMPLE, expected PROVED"
    found = [f for f in record["failures"] if f != expected]
    broken = theorems.get(mutant.breaks, {})
    if broken.get("verdict") != "COUNTEREXAMPLE":
        found.append(f"{mutant.breaks} is {broken.get('verdict')}, not COUNTEREXAMPLE")
    elif broken.get("detail"):
        found.append(f"{mutant.breaks}: {broken['detail']}")
    for held in mutant.holds:
        if not theorems.get(held, {}).get("ok"):
            found.append(f"{held} is not ok")
    if mutant.link and spec.get("link") is None:
        found.append("link: not run")
    return found
