import sys

from examples.pricer.spec import SPECS
from examples.quoter.spec import SPEC
from z3sol.cli import main

sys.exit(main([SPEC, *SPECS]))
