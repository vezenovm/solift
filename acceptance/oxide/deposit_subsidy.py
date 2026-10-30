"""The specs of DepositSubsidy: _quoteSubsidy, the public quoteSubsidy cut at _tokenWeiPerEth(), and _pricedGas. Each
has its read map and its link to Oxide's harness, with the theorems of Oxide's formulas/prove.py on the extracted
model: the depositQuote theorems on both quotes, and the depositPricedGas theorems on _pricedGas."""

import z3

from z3sol.link import Link
from z3sol.run import Spec
from z3sol.workspace import Project

MAX_PRIORITY_FEE_WEI = 100_000_000
WAD = 10**18

DEPOSIT_SUBSIDY = "src/periphery/DepositSubsidy.sol"
SETUP = "proofs/subsidies/test/SubsidySetup.sol:DepositSubsidySetup"
CONFIG = {
    "$depositConfig.targetRelayerProfit": "targetRelayerProfit",
    "$depositConfig.maxSubsidyPerOperation": "maxSubsidyPerOperation",
    "TOKEN.balanceOf(address(this))": "pot",
    "tx.gasprice": "txGasPrice",
    "block.basefee": "baseFee",
}
CONFIG_ADAPTERS = {
    ("targetRelayerProfit", "maxSubsidyPerOperation"): "_configure(targetRelayerProfit, maxSubsidyPerOperation);",
    "pot": "token.set(address(subsidy), pot, 1);",
    "baseFee": "vm.fee(baseFee);",
}

PROJECT = Project(
    profile="subsidy-proofs",
    link=("foundry.toml", "node_modules", ".deps"),
    copy=("src", "proofs/subsidies/src/SubsidyHarnesses.sol", "proofs/subsidies/test/SubsidySetup.sol"),
    generated="proofs/subsidies/test/z3sol",
)

SPEC = Spec(
    name="deposit-quote",
    target=f"{DEPOSIT_SUBSIDY}:DepositSubsidy._quoteSubsidy",
    reads=CONFIG,
    link=Link(
        setup=SETUP,
        call="subsidy.exposedQuote(_fee, _reimbursableGas, _tokenPerEth)",
        adapters=CONFIG_ADAPTERS,
        observed={"txGasPrice": "tx.gasprice"},
    ),
    project=PROJECT,
)

# The public quote reads its token price from _tokenWeiPerEth(), a cut point: the link sets the feed to a symbolic
# answer, and reads the price that results.
PUBLIC_QUOTE = Spec(
    name="deposit-public-quote",
    target=f"{DEPOSIT_SUBSIDY}:DepositSubsidy.quoteSubsidy",
    reads={**CONFIG, "_tokenWeiPerEth()": "tokenPerEth"},
    link=Link(
        setup=SETUP,
        call="subsidy.quoteSubsidy(_fee, _reimbursableGas)",
        adapters={**CONFIG_ADAPTERS, "usdPerEth": "feed.set(int256(uint256(usdPerEth)), block.timestamp);"},
        observed={"txGasPrice": "tx.gasprice", "tokenPerEth": "subsidy.exposedTokenWeiPerEth()"},
        parameters={"usdPerEth": "uint128"},
    ),
    project=PROJECT,
    stem="DepositSubsidyPublicQuote",
)

METER = ("deployed", "relayerTransaction", "executorStartedEmpty", "frameGas", "calldataGas", "executorHeadGas")

# _pricedGas reads the meter, a struct parameter, field by field. TOKEN is the token's address, which the link reads
# from the deployed contract. The relayer is tx.origin, which must not be the subsidy, whose balance is the pot.
PRICED_GAS = Spec(
    name="deposit-priced-gas",
    target=f"{DEPOSIT_SUBSIDY}:DepositSubsidy._pricedGas",
    reads={
        **{f"_meter.{name}": name for name in METER},
        "TOKEN": "token",
        "TOKEN.balanceOf(tx.origin)": "relayerBalance",
    },
    link=Link(
        setup=SETUP,
        call="subsidy.exposedPricedGas(_intent, _token, meter)",
        adapters={
            METER: "DepositSubsidy.Meter memory meter;\n" + "\n".join(f"    meter.{name} = {name};" for name in METER),
            "relayerBalance": (
                "vm.assume(tx.origin != address(subsidy));\n"
                "    token.set(address(subsidy), type(uint128).max, relayerBalance);"
            ),
        },
        observed={"token": "address(subsidy.TOKEN())"},
        imports=('import {DepositSubsidy} from "@periphery/DepositSubsidy.sol";',),
    ),
    project=PROJECT,
)


def paying(q):
    return q.result > 0


def price(q):
    """The token price that the quote uses: a parameter of _quoteSubsidy, and the cut point of quoteSubsidy."""
    return q._tokenPerEth if "_tokenPerEth" in q.inputs else q.tokenPerEth


def deposit_no_farming(q):
    """no farming: subsidy <= reimbursable gas x tx.gasprice, every config and every input that does not revert"""
    return q.result * WAD <= q._reimbursableGas * q.txGasPrice * price(q)


def envelope(q):
    """the gas to cover is at least the reimbursable gas and at most 2**24, the gas price at most the base fee plus
    MAX_PRIORITY_FEE_WEI with a base fee below 2**64, a token price and a fee below 2**128, a pot that holds the cap,
    and a fee plus cap that pays the gas to cover"""
    to_cover = q.free("gasToCover")
    return z3.And(
        q._reimbursableGas > 0,
        q._reimbursableGas <= to_cover,
        to_cover <= 2**24,
        q.txGasPrice >= 1,
        q.txGasPrice <= q.baseFee + MAX_PRIORITY_FEE_WEI,
        q.baseFee < 2**64,
        price(q) > 0,
        price(q) < 2**128,
        q._fee < 2**128,
        q.pot >= q.maxSubsidyPerOperation,
        (q._fee + q.maxSubsidyPerOperation) * WAD >= to_cover * q.txGasPrice * price(q),
    )


def _profit_covers(q, margin: int):
    capped_profit = z3.If(q.targetRelayerProfit < q._fee, q.targetRelayerProfit, q._fee)
    unreimbursed = (q.free("gasToCover") - q._reimbursableGas) * q.txGasPrice * price(q)
    return capped_profit * WAD >= unreimbursed + margin


def _required(q):
    """The payout that OperationExecutor requires: the gas to cover at tx.gasprice, rounded up to a token-wei."""
    return (q.free("gasToCover") * q.txGasPrice * price(q) + WAD - 1) / WAD


def envelope_with_margin(q):
    """inside the envelope, and the capped profit pays the gas that the subsidy does not reimburse, with a 1 token-wei
    margin"""
    return z3.And(envelope(q), _profit_covers(q, WAD))


def envelope_without_margin(q):
    """inside the envelope, and the capped profit pays the gas that the subsidy does not reimburse"""
    return z3.And(envelope(q), _profit_covers(q, 0))


def deposit_liveness(q):
    """liveness: inside the envelope, with a 1 token-wei margin on the profit, the quote does not revert and
    fee + subsidy >= the executor's required payout"""
    return z3.And(z3.Not(q.reverts), z3.Not(q.outside), q._fee + q.result >= _required(q))


def deposit_liveness_without_margin(q):
    """liveness without the 1 token-wei margin"""
    return q._fee + q.result >= _required(q)


def deposit_liveness_without_margin_short_by_one(q):
    """without the margin, fee + subsidy is at most 1 token-wei below the executor's required payout"""
    return q._fee + q.result + 1 >= _required(q)


for quote in (SPEC, PUBLIC_QUOTE):
    shown = ("_fee", "_reimbursableGas", "gasToCover", "txGasPrice", "_tokenPerEth" if quote is SPEC else "tokenPerEth")
    quote.theorem(assume=("no-revert", "inside"), witness=paying)(deposit_no_farming)
    quote.theorem(given=envelope_with_margin, witness=paying)(deposit_liveness)
    quote.theorem(
        given=envelope_without_margin,
        expect="counterexample",
        reason="rounding: without the 1 token-wei margin the payout can fall short",
        show=shown,
    )(deposit_liveness_without_margin)
    quote.theorem(given=envelope_without_margin, witness=paying)(deposit_liveness_without_margin_short_by_one)


def sound_bounds(q):
    """the transaction's gas before refunds is at least the meter's gross gas, its refund is at most the meter's
    estimate, and the EIP-7623 calldata floor is any gas"""
    return z3.And(
        q.free("grossActual") >= q.gross,
        q.free("refundActual") >= 0,
        q.free("refundActual") <= q.declared("refund"),
        q.free("calldataFloor") >= 0,
    )


def unpriced_refund(q):
    """the transaction's gas before refunds is at least the meter's gross gas, and its refund is the meter's estimate
    plus a refund that the meter does not price"""
    return z3.And(
        q.free("grossActual") >= q.gross,
        q.free("unpricedRefund") >= 0,
        q.free("refundActual") == q.declared("refund") + q.free("unpricedRefund"),
        q.free("calldataFloor") >= 0,
    )


def gas_used(q):
    """The gas that the transaction pays: its gross gas less the refund, which EIP-3529 caps at a fifth of the gross
    gas, and at least the EIP-7623 calldata floor."""
    gross, refund, floor = q.free("grossActual"), q.free("refundActual"), q.free("calldataFloor")
    used = gross - z3.If(refund < gross / 5, refund, gross / 5)
    return z3.If(used > floor, used, floor)


@PRICED_GAS.theorem(assume=("no-revert",), given=sound_bounds, witness=paying)
def priced_gas_at_most_gas_used(q):
    """pricedGas <= gasUsed when the gas constants are sound bounds (EIP-3529 cap, EIP-7623 floor)"""
    return q.result <= gas_used(q)


@PRICED_GAS.theorem(
    assume=("no-revert",),
    given=unpriced_refund,
    expect="counterexample",
    reason="a refund that the meter does not price lowers the gas used below the priced gas",
    show=("unpricedRefund",),
)
def priced_gas_with_unpriced_refund(q):
    """pricedGas <= gasUsed with a refund that the meter does not price"""
    return q.result <= gas_used(q)


@PRICED_GAS.theorem(assume=("no-revert",), given=unpriced_refund, witness=paying)
def priced_gas_overpricing_bound(q):
    """the overpricing is at most the unpriced refund"""
    return q.result <= gas_used(q) + q.free("unpricedRefund")
