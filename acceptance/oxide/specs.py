"""Every spec of the acceptance run against Oxide, in one run."""

from acceptance.oxide.deposit_subsidy import PRICED_GAS, PUBLIC_QUOTE, SPEC
from acceptance.oxide.withdrawal_subsidy import WITHDRAWAL_QUOTE

SPECS = [SPEC, PUBLIC_QUOTE, PRICED_GAS, WITHDRAWAL_QUOTE]
