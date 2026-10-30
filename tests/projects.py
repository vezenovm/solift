"""The fixture project and the Quoter link, shared by the link and Halmos tests. The example spec holds them."""

from examples.quoter.spec import LINK as QUOTER_LINK
from examples.quoter.spec import PROJECT, QUOTER
from examples.quoter.spec import READS as QUOTER_READS

__all__ = ["QUOTER", "QUOTER_LINK", "QUOTER_READS", "fixtures"]


def fixtures():
    return PROJECT
