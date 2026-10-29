"""Models built by hand, as the extractor should build them, for the tests of the IR and its back ends."""

from z3sol import ops
from z3sol.ir import UINT256, Const, Function, If, Let, Model, Read, Return, Var, uint

MAX_PRIORITY_FEE_WEI = 100_000_000
WAD = 10**18


def quote() -> Model:
    """DepositSubsidy._quoteSubsidy, with the config fields, the pot, tx.gasprice and block.basefee as reads."""
    fee, gas, token_per_eth = (Var(name, input=True) for name in ("_fee", "_reimbursableGas", "_tokenPerEth"))
    profit = Var("targetRelayerProfit", uint(128), input=True)
    cap = Var("maxSubsidyPerOperation", uint(128), input=True)
    pot, tx_gas_price, base_fee = (Var(name, input=True) for name in ("pot", "txGasPrice", "baseFee"))
    tip = Const(MAX_PRIORITY_FEE_WEI, uint(128), text="100000000", name="MAX_PRIORITY_FEE_WEI")
    gas_price, gas_cost, target, subsidy = (Var(name) for name in ("gasPrice", "gasCost", "targetPayout", "subsidy"))
    body = (
        If(ops.make("eq", gas, Const(0)), (Return(Const(0)),)),
        Let(gas_price, ops.make("min", tx_gas_price, ops.make("add", base_fee, tip))),
        Let(gas_cost, ops.make("mulDiv", ops.make("mul", gas, gas_price), token_per_eth, Const(WAD, text="1e18"))),
        Let(target, ops.make("add", gas_cost, ops.make("min", profit, fee))),
        If(ops.make("le", target, fee), (Return(Const(0)),)),
        Let(subsidy, ops.make("min", ops.make("sub", target, fee), cap)),
        Return(ops.make("min", subsidy, pot)),
    )
    reads = (
        Read(profit, "$depositConfig.targetRelayerProfit"),
        Read(cap, "$depositConfig.maxSubsidyPerOperation"),
        Read(pot, "TOKEN.balanceOf(address(this))"),
        Read(tx_gas_price, "tx.gasprice"),
        Read(base_fee, "block.basefee"),
    )
    return Model(
        "DepositSubsidy",
        Function("_quoteSubsidy", (fee, gas, token_per_eth), UINT256, body),
        reads=reads,
        imports=('import {Math} from "@oz/utils/math/Math.sol";',),
        pragma=">=0.8.27",
        spdx="Apache-2.0",
    )


class Reverts(Exception):
    pass


def quote_reference(_fee, _reimbursableGas, _tokenPerEth, targetRelayerProfit, maxSubsidyPerOperation, pot,
                    txGasPrice, baseFee):  # fmt: skip
    """The same function in plain Python integers, written from the Solidity, for checking the z3 terms."""

    def checked(value):
        if value >= 2**256:
            raise Reverts
        return value

    if _reimbursableGas == 0:
        return 0
    gas_price = min(txGasPrice, checked(baseFee + MAX_PRIORITY_FEE_WEI))
    product = checked(_reimbursableGas * gas_price)
    gas_cost = checked(product * _tokenPerEth // WAD)
    target = checked(gas_cost + min(targetRelayerProfit, _fee))
    if target <= _fee:
        return 0
    return min(min(target - _fee, maxSubsidyPerOperation), pot)
