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

from solift.extract.common import _normal
from solift.extract.errors import Missing
from solift.extract.reads import _LOCALS
from solift.ir import BOOL, Const, Expr, Let, Stmt, Tuple, Type, Unpack, Var, tuple_of

_STRUCT = re.compile(r"t_struct\$_\w+_\$(\d+)")

_ARGUMENTS = (
    "a memory struct argument must be a struct that the function builds or aliases, a memory copy or a field of "
    "storage, a struct parameter, or a constructor"
)


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
        if init is not None and init["nodeType"] == "FunctionCall" and init["kind"] == "functionCall":
            # A struct that an internal call returns: the callee returns its fields, which unpack into the locals.
            call = self._expr(init, scope)
            self._parts_of(init, call, len(fields))
            locals_ = {f: self._generated(f"{name}_{f}", t, decl, scope) for f, t in fields}
            scope[decl["id"]] = ("struct", decl["id"], definition["id"], locals_)
            self.flattened += 1
            return (self._unpacked(tuple(locals_.values()), call, init),)
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

    def _struct_return(self, node, returns) -> Type:
        """The return type of a function that returns a memory struct: the tuple of its fields, or the one field's type.
        The struct must be the function's only value."""
        if len(returns) != 1:
            self.fail(node["returnParameters"], f"{node['name']} returns a memory struct among several values")
        fields = self._struct_fields(self._struct_definition(returns[0]), returns[0])
        return tuple_of(tuple(t for _, t in fields))

    def _named_struct_return(self, returned, scope: dict) -> tuple[Var, ...]:
        """The named results of a function whose named return is a memory struct: a local for each field, which starts
        at zero, as the new struct does, and which nothing writes."""
        definition = self._struct_definition(returned)
        fields = self._struct_fields(definition, returned)
        locals_ = {f: self._generated(f"{returned['name']}_{f}", t, returned, scope) for f, t in fields}
        scope[returned["id"]] = ("struct", returned["id"], definition["id"], locals_)
        self.flattened += 1
        return tuple(locals_.values())

    def _returned_struct(self, node, scope: dict, return_type: Type) -> Expr:
        """The value of `return <struct>`: the tuple of its fields. A callee must return new memory: a struct that it
        builds, a copy of storage or what an internal call returns. Its struct parameter, or an alias of it, is refused,
        as the caller would have two structs that are one memory."""
        if node["nodeType"] == "FunctionCall" and node["kind"] == "functionCall":
            return self._expr(node, scope)
        entry = scope.get(node.get("referencedDeclaration")) if node["nodeType"] == "Identifier" else None
        if self.stack and isinstance(entry, tuple) and entry[0] == "struct" and entry[1] in self.parameter_objects:
            self.fail(
                node,
                f"returns its struct parameter {self.text(node)}, or an alias of it: the caller's struct and the "
                "returned one would be one memory, which the model has as two",
            )
        values = self._struct_values(node, scope)
        return (
            values[0] if len(values) == 1 else Tuple(tuple(values), Type("tuple", parts=tuple(v.type for v in values)))
        )

    def _hoisted(self, node, fields, scope: dict) -> list[Expr]:
        """The fields of the struct that an internal call inside an expression returns, as `window().end`, or that a
        constructor there builds: the call unpacks, or the constructor's values go, into temporaries just before the
        statement (Statements._block), so that each field is computed, as each can revert. Under `&&`, `||` or `?:`, the
        temporaries would run where the struct is not built, so that is refused."""
        if node["id"] in self.guarded:
            self.fail(
                node,
                f"the struct of {self.text(node)}, under &&, || or ?:: its fields would be computed where it does not "
                "run",
            )
        if node["kind"] == "structConstructorCall":
            definition = self._struct_definition(node)
            values = self._constructed(node, definition, fields, scope)
            callee = definition["name"]
        else:
            call = self._expr(node, scope)
            self._parts_of(node, call, len(fields))
            callee = node["expression"].get("name") or node["expression"].get("memberName")
        temporaries = []
        for field, type_ in fields:
            name, n = f"{callee}_{field}", 1
            while name in self.temporaries:
                n += 1
                name = f"{callee}_{field}_{n}"
            self.temporaries.add(name)
            temporaries.append(self._generated(name, type_, node, scope))
        if node["kind"] == "structConstructorCall":
            self.hoisted += [Let(t, v, self.src(node)) for t, v in zip(temporaries, values, strict=True)]
        else:
            self.hoisted.append(self._unpacked(tuple(temporaries), call, node))
        return temporaries

    def _parts_of(self, node, call: Expr, count: int) -> None:
        if len(call.type.parts if call.type.kind == "tuple" else (call.type,)) != count:
            self.fail(node, f"{self.text(node)} does not return the {count} fields of its struct")

    def _unpacked(self, vars_: tuple[Var, ...], call: Expr, node) -> Stmt:
        """The statement that declares `vars_` as the fields that `call` returns: a `Let` for a one-field struct."""
        if len(vars_) == 1:
            return Let(vars_[0], call, self.src(node))
        return Unpack(vars_, call, True, self.src(node))

    def _struct_parameter(self, param, scope: dict) -> list[Var]:
        """The parameters of a callee for a memory struct parameter, one for each field (`_w_start`, `_w_end`). The
        struct is a flattened one in the callee, so a write to its fields is refused there."""
        definition = self._struct_definition(param)
        locals_ = {}
        for field, type_ in self._struct_fields(definition, param):
            locals_[field] = self._generated(f"{param['name']}_{field}", type_, param, scope)
        scope[param["id"]] = ("struct", param["id"], definition["id"], locals_)
        self.parameter_objects.add(param["id"])
        self.flattened += 1
        return list(locals_.values())

    def _struct_values(self, node, scope: dict) -> list[Expr]:
        """The value of each field of a memory struct argument, in the order of the struct's members: the locals of a
        flattened struct, the reads of a memory copy of storage, of a storage struct (which the call copies), of a
        storage pointer or of a struct parameter of the modeled function, or the values that a constructor gives."""
        definition = self._struct_definition(node)
        fields = self._struct_fields(definition, node)
        if node["nodeType"] == "FunctionCall" and node["kind"] == "structConstructorCall":
            return self._constructed(node, definition, fields, scope)
        if node["nodeType"] == "FunctionCall" and node["kind"] == "functionCall":
            return self._hoisted(node, fields, scope)
        entry = scope.get(node.get("referencedDeclaration")) if node["nodeType"] == "Identifier" else None
        if isinstance(entry, tuple) and entry[0] == "struct":
            return [entry[3][field] for field, _ in fields]
        if not (isinstance(entry, tuple) and entry[0] in ("copy", "param", "pointer")):
            path = self._storage_path(node, scope)
            if path is None:
                self.fail(node, f"the memory struct argument {self.text(node)}: {_ARGUMENTS}")
            self._storage_read(path[1], node)
            # The call copies the storage struct, as a declaration of a memory copy does.
            entry = ("copy", *path, self._identity(node, scope), None)
        members = {m["name"]: m for m in definition["members"]}
        return [self._field_read(entry, members[field], node, scope) for field, _ in fields]

    def _field_read(self, entry, member, node, scope: dict) -> Var:
        """The input of a field of a memory copy, a storage pointer or a struct parameter of the modeled function, as a
        read of `w.field` there gives it, for a struct passed whole to a callee: the same key, and the same identity
        (reads._identity of the field's member access)."""
        key = f"{entry[1]}.{member['name']}"
        if entry[0] == "param":
            what, root = "a field of the struct parameter", entry[2]
        else:
            what, root = "a storage field", entry[3]
        if entry[0] == "pointer" and entry[2] is None:
            what = "a field of the storage parameter"
        mapped = self.read_map.get(_normal(key))
        if mapped is None:
            self.fail(node, f"reads {what} {key}, which the read map does not name", Missing(key, what, self.src(node)))
        identity = (("member", member["name"], member["id"]), *root)
        if self.key_identities.setdefault(_normal(key), identity) != identity:
            self.fail(node, f"the read {key} names other things than at its first read: {_LOCALS}")
        if entry[0] != "copy" and (_normal(key), None) in self.outputs and not self.stack:
            return self.outputs[(_normal(key), None)].var
        if entry[0] == "copy" and entry[4] in self.written_copies:
            self.fail(node, f"reads {key} after a write to its memory copy: the model does not see the write")
        if entry[0] == "pointer":
            self._storage_read(entry[2], node)
        return self._input(key, member, node)

    def _struct_field(self, node, entry) -> Var:
        """The local of a field of a flattened struct, as `w.end`."""
        if node["nodeType"] != "MemberAccess" or node["expression"]["nodeType"] != "Identifier":
            self.fail(node, f"{self.text(node)} is not a field of the memory struct")
        return entry[3][node["memberName"]]
