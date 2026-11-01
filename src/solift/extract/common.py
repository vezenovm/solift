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


def _super(callee) -> bool:
    """Whether a callee is `super.f`."""
    return _on(callee, "super")


def _this(callee) -> bool:
    """Whether a callee is `this.f`, an external call of the contract's own function."""
    return _on(callee, "this")


def _on(callee, name: str) -> bool:
    base = _unwrapped(callee.get("expression") or {}) if callee.get("expression") else {}
    return (
        callee["nodeType"] == "MemberAccess"
        and base.get("nodeType") == "Identifier"
        and base["name"] == name
        and base.get("referencedDeclaration", 0) < 0
    )


def _through_contract(callee) -> bool:
    """Whether a callee is `super.f` or a function named through its contract, as `Base.f`: an internal call."""
    base = callee.get("expression") or {}
    type_string = base.get("typeDescriptions", {}).get("typeString") or ""
    return callee["nodeType"] == "MemberAccess" and type_string.startswith("type(contract ")


def _unwrapped(callee):
    """An expression without its call options (`f{gas: g}`) and the parentheses around it (`(f)`, `(this)`)."""
    while True:
        if callee["nodeType"] == "FunctionCallOptions":
            callee = callee["expression"]
        elif callee["nodeType"] == "TupleExpression" and len(callee["components"]) == 1:
            callee = callee["components"][0]
        else:
            return callee
