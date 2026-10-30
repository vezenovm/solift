"""Mutants of the subsidy functions, of Caps and of MigrationSubsidy (acceptance/mutants.py)."""

from functools import partial

from acceptance import mutants

DEPOSIT = "src/periphery/DepositSubsidy.sol"
WITHDRAWAL = "src/periphery/PlainWithdrawalSubsidy.sol"
CAPS = "src/core/Caps.sol"
MIGRATION = "src/periphery/migration/MigrationSubsidy.sol"

# A mutant of DepositSubsidy._quoteSubsidy, unless it names another spec, path and function.
Mutant = partial(mutants.Mutant, spec="deposit-quote", path=DEPOSIT, function="_quoteSubsidy")


GAS_PRICE = "Math.min(tx.gasprice, block.basefee + OxideConstants.MAX_PRIORITY_FEE_WEI)"
PRICED_GAS = {"spec": "deposit-priced-gas", "function": "_pricedGas"}
WITHDRAWAL_QUOTE = {"spec": "withdrawal-quote", "path": WITHDRAWAL, "function": "quoteSubsidy"}
AVAILABLE = {"spec": "caps-available", "path": CAPS, "function": "getCurrentAvailable"}
MIGRATION_PRICED_GAS = {"spec": "migration-priced-gas", "path": MIGRATION, "function": "_pricedGas"}
MIGRATION_QUOTE = {"spec": "migration-quote", "path": MIGRATION, "function": "_quoteSubsidy"}

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
    Mutant(
        "caps-refill-at-half-the-rate",
        "Math.min($cachedAvailable + increase, GLOBAL_LIMIT)",
        "Math.min($cachedAvailable + increase / 2, GLOBAL_LIMIT)",
        breaks="available-refills-to-the-global-limit",
        holds=("available-at-most-the-global-limit", "available-does-not-revert"),
        **AVAILABLE,
    ),
    Mutant(
        "caps-elapsed-time-reversed",
        "(block.timestamp - $lastUpdatedTime) * RATE",
        "($lastUpdatedTime - block.timestamp) * RATE",
        breaks="available-does-not-revert",
        holds=("available-at-most-the-global-limit",),
        link=True,
        **AVAILABLE,
    ),
    Mutant(
        "migration-refund-cap-a-sixth",
        "Math.min(refund, gross / 5)",
        "Math.min(refund, gross / 6)",
        breaks="migration-priced-gas-at-most-gas-used",
        holds=("migration-priced-gas-at-most-the-largest-ceiling", "migration-priced-gas-does-not-revert"),
        **MIGRATION_PRICED_GAS,
    ),
    Mutant(
        "migration-fresh-balance-twice-in-the-ceiling",
        "ceiling += FRESH_BALANCE_GAS",
        "ceiling += 2 * FRESH_BALANCE_GAS",
        breaks="migration-priced-gas-at-most-the-largest-ceiling",
        holds=("migration-priced-gas-at-most-gas-used",),
        **MIGRATION_PRICED_GAS,
    ),
    Mutant(
        "migration-profit-twice",
        "Math.min(gasCost + cfg.targetRelayerProfit, cfg.maxSubsidyPerOperation)",
        "Math.min(gasCost + cfg.targetRelayerProfit + cfg.targetRelayerProfit, cfg.maxSubsidyPerOperation)",
        breaks="migration-pays-at-most-gas-and-profit",
        holds=("migration-quote-does-not-revert",),
        link=True,
        **MIGRATION_QUOTE,
    ),
    Mutant(
        "migration-gas-price-not-capped-by-tx-gasprice",
        GAS_PRICE,
        "block.basefee + OxideConstants.MAX_PRIORITY_FEE_WEI",
        breaks="migration-pays-at-most-gas-and-profit",
        holds=("migration-quote-does-not-revert",),
        spec="migration-quote",
        path=MIGRATION,
        function="_cappedGasPrice",
    ),
]
