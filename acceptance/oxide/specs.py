"""Every spec of the acceptance run against Oxide, in one run: the subsidy functions of Oxide's formulas/subsidies.py
(M1 and M2), and Caps.getCurrentAvailable, which Oxide does not prove (M3)."""

from acceptance.oxide.caps import AVAILABLE
from acceptance.oxide.deposit_subsidy import PRICED_GAS, PUBLIC_QUOTE, SPEC
from acceptance.oxide.withdrawal_subsidy import WITHDRAWAL_QUOTE

SPECS = [SPEC, PUBLIC_QUOTE, PRICED_GAS, WITHDRAWAL_QUOTE, AVAILABLE]
