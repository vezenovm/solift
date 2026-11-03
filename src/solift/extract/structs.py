"""Memory structs that are not copies of storage: each is a set of scalar locals, one for each field.

A struct that the function builds (`Window memory w = Window({start: a, end: b});`) is a scope entry
`("struct", object, definition, fields)`, with a local of the model for each field (`w_start`, `w_end`). `object` names
the memory that the struct is: every name that aliases it (`Window memory v = w;`) has the same entry, so a read of
`v.end` is the local `w_end`. The IR has no struct type: smt.py, solidity.py, the vectors and the link see the scalars.

Memory structs alias, and the scalars do not follow a write through another name, so the fields are never written
after the struct is built: a write to one, a reassignment of the struct, and a struct that a branch chooses are
refused. A memory copy of storage (`("copy", ...)`) is tracked by its object too, so that a write to it through one
name is seen through its aliases.

Once a flattened struct exists, a read map call whose code has inline assembly that is not marked memory-safe is
refused (reads.py): such assembly can write the struct's memory, which the scalars do not see.
"""

from __future__ import annotations

import re

from solift.ir import BOOL, Const, Let, Stmt, Type, Var

_STRUCT = re.compile(r"t_struct\$_\w+_\$(\d+)")


def memory_safe(assembly) -> bool:
    """Whether an inline assembly block is marked memory-safe, by its flag or by the older comment."""
    flags = assembly.get("flags") or ()
    documentation = assembly.get("documentation") or ""
    return "memory-safe" in flags or re.search(r"@solidity\s+memory-safe-assembly\b", documentation) is not None


def _zero(type_: Type) -> Const:
    """The zero of a type, as a field of a new memory struct holds it, written as Solidity takes it."""
    if type_ == BOOL:
        return Const(False, BOOL, text="false")
    if type_.kind == "address":
        return Const(0, type_, text="address(0)")
    if type_.kind == "enum":
        return Const(0, type_, text=f"{type_.name}(0)")
    return Const(0, type_, text="0")


class Structs:
    def _struct_definition(self, node):
        """The StructDefinition of a struct-typed node."""
        match = _STRUCT.match(node["typeDescriptions"].get("typeIdentifier") or "")
        definition = self.nodes.get(int(match.group(1))) if match else None
        if definition is None or definition["nodeType"] != "StructDefinition":
            self.fail(node, f"the struct type of {self.text(node)} is not one the extractor can resolve")
        return definition

    def _struct_fields(self, definition, node) -> list[tuple[str, Type]]:
        """The name and type of each field of a memory struct, which must each be a type of the model."""
        fields = []
        for member in definition["members"]:
            type_string = member["typeDescriptions"]["typeString"]
            if (
                type_string.startswith(("struct ", "mapping("))
                or "[" in type_string
                or type_string in ("string", "bytes")
            ):
                self.fail(
                    node, f"the memory struct {definition['name']} has the field {member['name']} of type {type_string}"
                )
            fields.append((member["name"], self._type(member)))
        return fields

    def _struct_declaration(self, decl, init, scope: dict) -> tuple[Stmt, ...]:
        """A memory struct local that is not a copy of storage: an alias of a struct that the function has, or a new
        struct, built by a constructor or zero, with a local for each field."""
        name = decl["name"]
        if init is not None and init["nodeType"] == "Identifier":
            entry = scope.get(init.get("referencedDeclaration"))
            if isinstance(entry, tuple) and entry[0] in ("struct", "copy", "param"):
                scope[decl["id"]] = entry
                return ()
        definition = self._struct_definition(decl)
        fields = self._struct_fields(definition, decl)
        if init is None:
            values = [_zero(t) for _, t in fields]
        elif init["nodeType"] == "FunctionCall" and init["kind"] == "structConstructorCall":
            values = self._constructed(init, definition, fields, scope)
        else:
            self.fail(init, "a memory struct that is not built here, a copy of a storage struct, or an alias of one")
        stmts = []
        locals_ = {}
        for (field, type_), value in zip(fields, values, strict=True):
            var = self._generated(f"{name}_{field}", type_, decl, scope)
            locals_[field] = var
            stmts.append(Let(var, value, self.src(init or decl)))
        scope[decl["id"]] = ("struct", decl["id"], definition["id"], locals_)
        self.flattened += 1
        return tuple(stmts)

    def _constructed(self, call, definition, fields, scope: dict) -> list:
        """The value of each field that a struct constructor gives, in the order of the struct's members."""
        arguments = call["arguments"]
        if call.get("names"):
            by_name = dict(zip(call["names"], arguments, strict=True))
            if sorted(by_name) != sorted(f for f, _ in fields):
                self.fail(
                    call, f"the named arguments {', '.join(call['names'])} are not the fields of {definition['name']}"
                )
            arguments = [by_name[f] for f, _ in fields]
        if len(arguments) != len(fields):
            self.fail(call, f"{definition['name']} takes {len(fields)} fields, not {len(arguments)}")
        values = []
        for arg, (_, type_) in zip(arguments, fields, strict=True):
            value = self._expr(arg, scope)
            self._assignable(arg, value.type, type_)
            values.append(value)
        return values

    def _generated(self, name: str, type_: Type, node, scope: dict) -> Var:
        """A local of the model that the source does not declare, which must not have the name of one in scope."""
        if any(isinstance(v, Var) and v.name == name for v in scope.values()) or any(
            isinstance(e, tuple) and e[0] == "struct" and any(v.name == name for v in e[3].values())
            for e in scope.values()
        ):
            self.fail(node, f"the local {name} for a field of a memory struct has the name of another local")
        if name == "i":
            self.fail(node, "i names the inputs struct of the emitted Solidity")
        if self.function_names is None:
            self.function_names = {n["name"] for n in self.nodes.values() if n.get("nodeType") == "FunctionDefinition"}
        if name in self.function_names:
            self.fail(
                node,
                f"the local {name} for a field of a memory struct has the name of a function, which the model calls",
            )
        return Var(name, type_, src=self.src(node))

    def _struct_field(self, node, entry) -> Var:
        """The local of a field of a flattened struct, as `w.end`."""
        if node["nodeType"] != "MemberAccess" or node["expression"]["nodeType"] != "Identifier":
            self.fail(node, f"{self.text(node)} is not a field of the memory struct")
        return entry[3][node["memberName"]]
