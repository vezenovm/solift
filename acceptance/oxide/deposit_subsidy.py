"""The spec of DepositSubsidy._quoteSubsidy: its read map, its link to Oxide's harness, and the depositQuote theorems
of Oxide's formulas/prove.py, on the extracted model."""

import z3

from z3sol.link import Link
from z3sol.run import Spec
from z3sol.workspace import Project

MAX_PRIORITY_FEE_WEI = 100_000_000
WAD = 10**18

PROJECT = Project(
    profile="subsidy-proofs",
    link=("foundry.toml", "node_modules", ".deps"),
    copy=("src", "proofs/subsidies/src/SubsidyHarnesses.sol", "proofs/subsidies/test/SubsidySetup.sol"),
    generated="proofs/subsidies/test/z3sol",
)

SPEC = Spec(
    name="deposit-quote",
    target="src/periphery/DepositSubsidy.sol:DepositSubsidy._quoteSubsidy",
    reads={
        "$depositConfig.targetRelayerProfit": "targetRelayerProfit",
        "$depositConfig.maxSubsidyPerOperation": "maxSubsidyPerOperation",
        "TOKEN.balanceOf(address(this))": "pot",
        "tx.gasprice": "txGasPrice",
        "block.basefee": "baseFee",
    },
    link=Link(
        setup="proofs/subsidies/test/SubsidySetup.sol:DepositSubsidySetup",
        call="subsidy.exposedQuote(_fee, _reimbursableGas, _tokenPerEth)",
        adapters={
            ("targetRelayerProfit", "maxSubsidyPerOperation"): (
                "_configure(targetRelayerProfit, maxSubsidyPerOperation);"
            ),
            "pot": "token.set(address(subsidy), pot, 1);",
            "baseFee": "vm.fee(baseFee);",
        },
        observed={"txGasPrice": "tx.gasprice"},
    ),
    project=PROJECT,
)


def paying(q):
    return q.result > 0


@SPEC.theorem(assume=("no-revert", "inside"), witness=paying)
def deposit_no_farming(q):
    """no farming: subsidy <= reimbursable gas x tx.gasprice, every config and every input that does not revert"""
    return q.result * WAD <= q._reimbursableGas * q.txGasPrice * q._tokenPerEth


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
        q._tokenPerEth > 0,
        q._tokenPerEth < 2**128,
        q._fee < 2**128,
        q.pot >= q.maxSubsidyPerOperation,
        (q._fee + q.maxSubsidyPerOperation) * WAD >= to_cover * q.txGasPrice * q._tokenPerEth,
    )


def _profit_covers(q, margin: int):
    capped_profit = z3.If(q.targetRelayerProfit < q._fee, q.targetRelayerProfit, q._fee)
    unreimbursed = (q.free("gasToCover") - q._reimbursableGas) * q.txGasPrice * q._tokenPerEth
    return capped_profit * WAD >= unreimbursed + margin


def _required(q):
    """The payout that OperationExecutor requires: the gas to cover at tx.gasprice, rounded up to a token-wei."""
    return (q.free("gasToCover") * q.txGasPrice * q._tokenPerEth + WAD - 1) / WAD


def envelope_with_margin(q):
    """inside the envelope, and the capped profit pays the gas that the subsidy does not reimburse, with a 1 token-wei
    margin"""
    return z3.And(envelope(q), _profit_covers(q, WAD))


def envelope_without_margin(q):
    """inside the envelope, and the capped profit pays the gas that the subsidy does not reimburse"""
    return z3.And(envelope(q), _profit_covers(q, 0))


@SPEC.theorem(given=envelope_with_margin, witness=paying)
def deposit_liveness(q):
    """liveness: inside the envelope, with a 1 token-wei margin on the profit, the quote does not revert and
    fee + subsidy >= the executor's required payout"""
    return z3.And(z3.Not(q.reverts), z3.Not(q.outside), q._fee + q.result >= _required(q))


@SPEC.theorem(
    given=envelope_without_margin,
    expect="counterexample",
    reason="rounding: without the 1 token-wei margin the payout can fall short",
    show=("_fee", "_reimbursableGas", "gasToCover", "txGasPrice", "_tokenPerEth"),
)
def deposit_liveness_without_margin(q):
    """liveness without the 1 token-wei margin"""
    return q._fee + q.result >= _required(q)


@SPEC.theorem(given=envelope_without_margin, witness=paying)
def deposit_liveness_without_margin_short_by_one(q):
    """without the margin, fee + subsidy is at most 1 token-wei below the executor's required payout"""
    return q._fee + q.result + 1 >= _required(q)
