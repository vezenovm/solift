"""The errors of the extraction, with what discover.py needs to add to the read map or the output map."""

from __future__ import annotations

from dataclasses import dataclass

from solift.ir import Src


@dataclass(frozen=True)
class Missing:
    """A read that the read map does not name: its key as the read map would name it, what it is, and its place."""

    key: str
    what: str
    src: Src


@dataclass(frozen=True)
class Wanted:
    """An output that the extraction needs: the place of a write, or an argument of an external call by its index, with
    the source text of each of the call's arguments."""

    key: str
    index: int | None = None
    arguments: tuple[str, ...] = ()


class ExtractionError(Exception):
    """An error with the place in the source. `missing` is the read, when the error is a read that the read map does not
    name: discover.py adds it to the read map and extracts again. `output` is the output, when the error is a write or
    a call argument that the model can have only as an output, and `effects` the writes and calls found, when it is a
    function without a value that names no output: discover.py adds them to the output map."""

    def __init__(self, message: str, missing: Missing | None = None, effects: tuple = (), output: Wanted | None = None):
        super().__init__(message)
        self.missing = missing
        self.effects = effects
        self.output = output
