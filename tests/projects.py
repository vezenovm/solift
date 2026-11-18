"""The fixture project and the Quoter link, shared by the link and Halmos tests. The example spec holds them."""

from examples.quoter.spec import HARNESS, PROJECT, QUOTER, SPEC
from examples.quoter.spec import READS as QUOTER_READS
from solift.link import Link

# The quoter's link as the run makes it (link.combine): the quoter's model has every input of its harness.
QUOTER_LINK = Link(setup=HARNESS.setup, call=SPEC.call, adapters=HARNESS.adapters, observed=HARNESS.observed)

__all__ = ["QUOTER", "QUOTER_LINK", "QUOTER_READS", "fixtures"]


def fixtures():
    return PROJECT
