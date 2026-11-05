import pathlib

import pytest

from solift import forge

ROOT = pathlib.Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def build(tmp_path_factory):
    """The build infos of the repository's contracts and test fixtures, which the extraction tests share."""
    work = tmp_path_factory.mktemp("forge")
    return forge.build(ROOT, work / "out", work / "cache")
