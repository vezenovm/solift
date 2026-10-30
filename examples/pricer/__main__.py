import sys

from examples.pricer.spec import SPECS
from examples.quoter.spec import SPEC
from solift.cli import main

sys.exit(main([SPEC, *SPECS]))
