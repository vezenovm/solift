"""The AST helpers and messages that several parts of the extractor share."""

from __future__ import annotations

import re

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


ASSIGNMENTS = {"=": None, "+=": "add", "-=": "sub", "*=": "mul", "/=": "div", "%=": "mod"}


def _normal(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _subtree(node):
    """Each AST node in `node`, `node` first."""
    if isinstance(node, dict):
        if "nodeType" in node:
            yield node
        for value in node.values():
            yield from _subtree(value)
    elif isinstance(node, list):
        for value in node:
            yield from _subtree(value)


EXTERNAL = "external call"


_STORAGE = (
    "a storage pointer must be to a storage struct, at mapping keys that name one entry for the whole call (inputs, or "
    "fields of tx, block or msg), or to a field of one"
)


def _storage_struct(decl) -> bool:
    """Whether a declaration is a storage pointer to a struct, as `Spender storage spender`."""
    return decl["typeDescriptions"]["typeString"].startswith("struct ") and decl.get("storageLocation") == "storage"


def _root(node):
    while node["nodeType"] in ("MemberAccess", "IndexAccess"):
        node = node["expression" if node["nodeType"] == "MemberAccess" else "baseExpression"]
    return node if node["nodeType"] == "Identifier" else None
