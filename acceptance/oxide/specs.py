"""Every spec of the acceptance run against Oxide, in one run: the subsidy functions of Oxide's formulas/subsidies.py
(M1 and M2), and Caps.getCurrentAvailable and MigrationSubsidy, which Oxide does not prove (M3)."""

from acceptance.oxide.caps import AVAILABLE
from acceptance.oxide.deposit_subsidy import PRICED_GAS, PUBLIC_QUOTE, SPEC
from acceptance.oxide.migration_subsidy import PRICED_GAS as MIGRATION_PRICED_GAS
from acceptance.oxide.migration_subsidy import QUOTE as MIGRATION_QUOTE
from acceptance.oxide.withdrawal_subsidy import WITHDRAWAL_QUOTE

SPECS = [SPEC, PUBLIC_QUOTE, PRICED_GAS, WITHDRAWAL_QUOTE, AVAILABLE, MIGRATION_PRICED_GAS, MIGRATION_QUOTE]
