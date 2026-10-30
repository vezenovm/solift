"""The spec of PlainWithdrawalSubsidy.quoteSubsidy, cut at _tokenWeiPerEth(): its read map, its link to Oxide's
harness, and the withdrawalQuote theorems of Oxide's formulas/prove.py, on the extracted model."""

from acceptance.oxide.deposit_subsidy import PROJECT, paying
from z3sol.link import Link
from z3sol.run import Spec

WAD = 10**18
PLAIN_WITHDRAWAL_SUBSIDY = "src/periphery/PlainWithdrawalSubsidy.sol"

# The pricing of the flow is a mapping entry, read through a memory copy. The token price is a cut point: the link sets
# the feed to a symbolic answer, and reads the price that results.
WITHDRAWAL_QUOTE = Spec(
    name="withdrawal-quote",
    target=f"{PLAIN_WITHDRAWAL_SUBSIDY}:PlainWithdrawalSubsidy.quoteSubsidy",
    reads={
        "$flowPricing[_flow].startPriceWei": "startPriceWei",
        "$flowPricing[_flow].maxSubsidy": "maxSubsidy",
        "_tokenWeiPerEth()": "tokenPerEth",
        "TOKEN.balanceOf(address(this))": "pot",
        "tx.gasprice": "txGasPrice",
        "block.basefee": "baseFee",
    },
    link=Link(
        setup="proofs/subsidies/test/SubsidySetup.sol:PlainWithdrawalSubsidySetup",
        call="subsidy.quoteSubsidy(_flow)",
        adapters={
            ("startPriceWei", "maxSubsidy"): (
                "PlainWithdrawalSubsidy.FlowPricing memory pricing =\n"
                "      PlainWithdrawalSubsidy.FlowPricing({startPriceWei: startPriceWei, maxSubsidy: maxSubsidy});\n"
                "    vm.prank(OWNER);\n"
                "    subsidy.setFlowPricing(_flow, pricing);"
            ),
            "usdPerEth": "feed.set(int256(uint256(usdPerEth)), block.timestamp);",
            "pot": "token.set(address(subsidy), pot, 0);",
            "baseFee": "vm.fee(baseFee);",
        },
        observed={"txGasPrice": "tx.gasprice", "tokenPerEth": "subsidy.exposedTokenWeiPerEth()"},
        parameters={"usdPerEth": "uint40"},
        imports=('import {PlainWithdrawalSubsidy} from "@periphery/PlainWithdrawalSubsidy.sol";',),
    ),
    project=PROJECT,
)


@WITHDRAWAL_QUOTE.theorem(assume=("no-revert", "inside"), witness=paying)
def withdrawal_no_farming_against_the_model(q):
    """no farming against the model: subsidy <= modeledTxGas x tx.gasprice, every input that does not revert"""
    modeled = q.call("modeledTxGas", q._flow).value
    return q.result * WAD <= modeled * q.txGasPrice * q.tokenPerEth
