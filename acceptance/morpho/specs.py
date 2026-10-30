"""Every spec of the acceptance run against Morpho Blue, in one run."""

from acceptance.morpho.health import HEALTHY
from acceptance.morpho.shares import SUPPLY_WITHDRAW

SPECS = [HEALTHY, SUPPLY_WITHDRAW]
