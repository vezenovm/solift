"""The specs of MigrationSubsidy, which Oxide's registry exempts from its proofs: "It pays the gas plus a profit with no
fee to offset it, and only once the source portal is frozen. Its no-farming argument rests on the freeze, so it needs
its own model." Its fallback pays _quoteSubsidy(_pricedGas(flow, meter), tokenWeiPerEth). The fallback writes storage
and calls other contracts, so the two functions are proved here one at a time:

- _pricedGas is at most the gas that the transaction pays, when the gas constants are sound bounds, as for
  DepositSubsidy, and at most 3,127,100;
- _quoteSubsidy pays at most that gas at tx.gasprice plus targetRelayerProfit, and does not revert for such gas.

Together: a relayer earns at most targetRelayerProfit from one migration. That a farmer cannot repeat that profit is the
freeze argument, which stays outside the tool. The expected counterexample to "pays at most the gas" pins the profit,
so a change that removes it fails the run.
"""

import z3

from acceptance.oxide.deposit_subsidy import WAD, gas_used, paying
from acceptance.oxide.project import PROJECT
from z3sol.link import Link
from z3sol.run import Spec

MIGRATION_SUBSIDY = "src/periphery/migration/MigrationSubsidy.sol"
SETUP = "proofs/subsidies/test/MigrationSubsidySetup.sol:MigrationSubsidySetup"
# The largest migrationGasCeiling, FROZEN_NOTES_REFUND_GAS_CEILING, plus FRESH_BALANCE_GAS.
MAX_PRICED_GAS = 3_110_000 + 17_100
CLEARED_SLOT_REFUND = 4800

METER = (
    "frameGas",
    "calldataGas",
    "executorHeadGas",
    "operationExecutorStartedEmpty",
    "operationExecutorCredited",
    "migrationExecutorStartedEmpty",
    "destinationCapsCleared",
)

# _pricedGas reads the meter field by field, and the relayer's balance of TOKEN. The relayer is tx.origin, which must
# not be the subsidy, whose balance the token stub keeps apart.
PRICED_GAS = Spec(
    name="migration-priced-gas",
    target=f"{MIGRATION_SUBSIDY}:MigrationSubsidy._pricedGas",
    reads={
        **{f"_meter.{name}": name for name in METER},
        "TOKEN.balanceOf(tx.origin)": "relayerBalance",
    },
    link=Link(
        setup=SETUP,
        call="subsidy.exposedPricedGas(_flow, meter)",
        adapters={
            METER: "MigrationSubsidy.Meter memory meter;\n"
            + "\n".join(f"    meter.{name} = {name};" for name in METER),
            "relayerBalance": (
                "vm.assume(tx.origin != address(subsidy));\n"
                "    token.set(address(subsidy), type(uint128).max, relayerBalance);"
            ),
        },
        imports=('import {MigrationSubsidy} from "@periphery/migration/MigrationSubsidy.sol";',),
    ),
    project=PROJECT,
    # The gross gas is at least TX_INTRINSIC_GAS + EXIT_PATH_GAS, so the priced gas is never zero.
    impossible=("zero",),
)

QUOTE = Spec(
    name="migration-quote",
    target=f"{MIGRATION_SUBSIDY}:MigrationSubsidy._quoteSubsidy",
    reads={
        "$config.targetRelayerProfit": "targetRelayerProfit",
        "$config.maxSubsidyPerOperation": "maxSubsidyPerOperation",
        "TOKEN.balanceOf(address(this))": "pot",
        "tx.gasprice": "txGasPrice",
        "block.basefee": "baseFee",
    },
    link=Link(
        setup=SETUP,
        call="subsidy.exposedQuote(_gas, _tokenPerEth)",
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


def sound_bounds(q):
    """the transaction's gas before refunds is at least the meter's gross gas, its refund is at most the meter's refund
    before the EIP-3529 cap, and the EIP-7623 calldata floor is any gas"""
    estimate = q.declared("refund") + z3.If(q.destinationCapsCleared, CLEARED_SLOT_REFUND, 0)
    return z3.And(
        q.free("grossActual") >= q.gross,
        q.free("refundActual") >= 0,
        q.free("refundActual") <= estimate,
        q.free("calldataFloor") >= 0,
    )


@PRICED_GAS.theorem(assume=("no-revert",), given=sound_bounds, witness=paying)
def migration_priced_gas_at_most_gas_used(q):
    """pricedGas <= gasUsed when the gas constants are sound bounds (EIP-3529 cap, EIP-7623 floor)"""
    return q.result <= gas_used(q)


@PRICED_GAS.theorem(assume=("no-revert",), witness=paying)
def migration_priced_gas_at_most_the_largest_ceiling(q):
    """pricedGas <= 3,127,100, the largest ceiling plus FRESH_BALANCE_GAS, for every flow and meter"""
    return q.result <= MAX_PRICED_GAS


def meter_below_2_128(q):
    """the meter's gas fields are below 2**128"""
    return z3.And(q.frameGas < 2**128, q.calldataGas < 2**128, q.executorHeadGas < 2**128)


@PRICED_GAS.theorem(given=meter_below_2_128, witness=paying)
def migration_priced_gas_does_not_revert(q):
    """liveness: for a meter below 2**128, _pricedGas does not revert: its refund never exceeds its gross gas"""
    return z3.Not(q.reverts)


def migration_gas_cost(q):
    """the priced gas at tx.gasprice, in token-wei times WAD"""
    return q._gas * q.txGasPrice * q._tokenPerEth


@QUOTE.theorem(assume=("no-revert", "inside"), witness=paying)
def migration_pays_at_most_gas_and_profit(q):
    """subsidy <= gas x tx.gasprice + targetRelayerProfit, every config and every input that does not revert"""
    return q.result * WAD <= migration_gas_cost(q) + q.targetRelayerProfit * WAD


@QUOTE.theorem(
    assume=("no-revert", "inside"),
    expect="counterexample",
    reason=(
        "the subsidy adds targetRelayerProfit to the gas of each migration, so its no-farming argument rests on the "
        "frozen source portal, as Oxide's registry says, and not on the price"
    ),
    show=("_gas", "txGasPrice", "_tokenPerEth", "targetRelayerProfit"),
)
def migration_pays_at_most_gas(q):
    """subsidy <= gas x tx.gasprice: no profit"""
    return q.result * WAD <= migration_gas_cost(q)


def priced_envelope(q):
    """the gas is at most 3,127,100 (the theorem on _pricedGas), the base fee below 2**64 and the token price below
    2**128"""
    return z3.And(q._gas <= MAX_PRICED_GAS, q.baseFee < 2**64, q._tokenPerEth < 2**128)


@QUOTE.theorem(given=priced_envelope, witness=paying)
def migration_quote_does_not_revert(q):
    """liveness: for gas that _pricedGas can return, _quoteSubsidy does not revert and stays inside the mulDiv lemma's
    domain, for every config, pot and gas price"""
    return z3.And(z3.Not(q.reverts), z3.Not(q.outside))
