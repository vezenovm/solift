"""Constants, numbers, and the types of AST nodes."""

from __future__ import annotations

import re
from fractions import Fraction

from solift.extract.common import _normal
from solift.extract.errors import ExtractionError
from solift.ir import ADDRESS, BOOL, LITERAL, Const, Type, enum, uint

SUBDENOMINATIONS = {
    "wei": 1,
    "gwei": 10**9,
    "ether": 10**18,
    "seconds": 1,
    "minutes": 60,
    "hours": 3600,
    "days": 86400,
    "weeks": 604800,
}


class Values:
    def _named(self, node) -> Const:
        """A constant, named by an identifier or a member access such as `Fees.TIP`, or an enum member such as
        `Kinds.Kind.Small`, which the emitted Solidity names by its full name, or `type(uint<n>).max` or `.min`."""
        bound = self._type_bound(node)
        if bound is not None:
            return bound
        decl = self.nodes.get(node.get("referencedDeclaration"))
        if decl is not None and decl["nodeType"] == "EnumValue":
            definition, index = self.enum_members[decl["id"]]
            type_ = self._enum(definition)
            return Const(index, type_, text=f"{type_.name}.{decl['name']}", src=self.src(node))
        if decl is None or decl["nodeType"] != "VariableDeclaration" or decl.get("mutability") != "constant":
            self.fail(node, f"{self.text(node)} is not a constant, a local, a parameter or a read")
        return self._constant(decl)

    def _type_bound(self, node) -> Const | None:
        """`type(uint<n>).max` or `.min` as a constant, as written, or None for any other expression."""
        base = node.get("expression") or {}
        callee = base.get("expression") or {}
        if not (
            node["nodeType"] == "MemberAccess"
            and node["memberName"] in ("max", "min")
            and base.get("nodeType") == "FunctionCall"
            and callee.get("name") == "type"
            and callee.get("referencedDeclaration", 0) < 0
        ):
            return None
        type_ = self._type(node)
        if type_.kind != "uint":
            self.fail(node, f"{self.text(node)} is not the bound of an unsigned type")
        value = type_.bound - 1 if node["memberName"] == "max" else 0
        return Const(value, type_, text=_normal(self.text(node)), src=self.src(node))

    def _constant(self, decl) -> Const:
        self.used.add(self._place(decl)[0])
        value = decl.get("value")
        if value is None:
            self.fail(decl, f"the constant {decl['name']} has no value")
        if value["nodeType"] == "Literal" and value["kind"] == "bool":
            number, text = value["value"] == "true", value["value"]
        elif value["nodeType"] == "Literal" and value["kind"] == "number":
            number, text = self._number(value), self.text(value)
        elif value["nodeType"] in ("Identifier", "MemberAccess"):
            other = self._named(value)
            number, text = other.value, other.text
        elif (
            value["nodeType"] == "FunctionCall"
            and value["kind"] == "typeConversion"
            and self._type(decl) == ADDRESS
            and len(value["arguments"]) == 1
            and value["arguments"][0]["nodeType"] == "Literal"
            and value["arguments"][0]["kind"] == "number"
        ):
            # A checksummed address literal stands alone; any other number needs its conversion, as `address(0)`.
            literal = value["arguments"][0]
            number, text = self._number(literal), self.text(literal)
            if not re.fullmatch(r"0x[0-9a-fA-F]{40}", text):
                text = f"address({text})"
        else:
            self.fail(value, f"the constant {decl['name']} is not a literal, an address or another constant")
        return Const(number, self._type(decl), text=text, name=decl["name"], src=self.src(decl))

    def _number(self, node) -> int:
        written = node["value"].replace("_", "").lower()
        if written.startswith("0x"):
            number = Fraction(int(written, 16))
        elif "e" in written:
            mantissa, exponent = written.split("e")
            number = Fraction(mantissa) * Fraction(10) ** int(exponent)
        else:
            number = Fraction(written)
        number *= SUBDENOMINATIONS[node["subdenomination"]] if node.get("subdenomination") else 1
        if number.denominator != 1 or number < 0:
            self.fail(node, f"{self.text(node)} is not an unsigned integer")
        return int(number)

    def _type(self, node) -> Type:
        type_string = node["typeDescriptions"]["typeString"]
        if type_string == "bool":
            return BOOL
        if type_string.startswith("int_const "):
            return LITERAL
        match = re.fullmatch(r"uint(\d+)", type_string)
        if match:
            return uint(int(match.group(1)))
        if re.fullmatch(r"int\d*", type_string):
            self.fail(node, "a signed integer")
        if type_string in ("address", "address payable") or re.fullmatch(r"contract \w+", type_string):
            return ADDRESS
        # The type string names an enum by its canonical name, which two files can share, and the identifier by the id
        # of its definition.
        match = re.fullmatch(r"t_enum\$_\w+_\$(\d+)", node["typeDescriptions"].get("typeIdentifier") or "")
        if match and self.nodes.get(int(match.group(1)), {}).get("nodeType") == "EnumDefinition":
            return self._enum(self.nodes[int(match.group(1))])
        self.fail(node, f"the type {type_string} is not supported")

    def _enum_named(self, name: str, key: str):
        """The definition of the enum of the canonical name `name`, the type of the read map key `key`, which one file
        must declare."""
        found = [n for n in self.nodes.values() if n.get("nodeType") == "EnumDefinition" and n["canonicalName"] == name]
        if len(found) != 1:
            raise ExtractionError(f"the read map key {key} has the enum type {name}, which {len(found)} files declare")
        return found[0]

    def _enum(self, definition) -> Type:
        """The enum type, and the import of the contract or file-level enum that declares it, for the emitted
        Solidity."""
        name = definition["canonicalName"]
        symbol = name.partition(".")[0]
        path = self._place(definition)[0]
        if self.enum_imports.setdefault(symbol, path) != path:
            raise ExtractionError(f"two enums are declared under the name {symbol}")
        self.used.add(path)
        return enum(name, len(definition["members"]))
