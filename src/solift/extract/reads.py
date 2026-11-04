"""Reads: storage, immutables, storage pointers and their keys, fields of tx, block and msg, balances, and the read
map's other expressions."""

from __future__ import annotations

import re

from solift.extract.common import EXTERNAL, _normal, _root, _subtree, _this, _through_contract, _unwrapped, memory_safe
from solift.extract.errors import Missing
from solift.ir import Read, Var

# A type that holds a function pointer, as `function () view returns (uint256)` or a mapping or array of one.
_POINTER = re.compile(r"\bfunction\b \(")

# The low-level calls, as members of an address and as Yul builtins, whose first Yul argument is the gas that the call
# forwards.
_CALLS = {"call", "callcode", "delegatecall", "staticcall"}

# The Yul builtins that compute a value from their arguments alone, through which gas() can reach a call's gas argument
# and nothing else.
_ARITHMETIC = {
    "add", "sub", "mul", "div", "sdiv", "mod", "smod", "exp", "addmod", "mulmod", "signextend", "not", "lt", "gt",
    "slt", "sgt", "eq", "iszero", "and", "or", "xor", "byte", "shl", "shr", "sar",
}  # fmt: skip


def _forwarded(argument) -> list:
    """The gas() calls whose value goes only into a call's gas argument: the argument itself, or one inside builtins
    of _ARITHMETIC. A Yul function of the code, as `keep(gas())`, could also store the value where the read finds it."""
    if argument.get("nodeType") != "YulFunctionCall":
        return []
    name = argument["functionName"]["name"]
    if name == "gas":
        return [argument]
    return [g for a in argument["arguments"] for g in _forwarded(a)] if name in _ARITHMETIC else []


class _Scan:
    """One read's scan (_varies): the functions and modifiers that it followed, each with the contract that runs it,
    the functions that the code names only for their selector, and whether the code can send a selector as calldata,
    through a low-level call or a pointer to an external function. Only then can a function named for its selector
    run, so the scan follows those functions once it finds such a call."""

    def __init__(self) -> None:
        self.seen: set = set()
        self.selected: list = []
        self.sends = False
        self.unsafe = False
        self.assembly = False


# The marks in a callee's reads that are not storage variables: a read map call that could read any storage, a storage
# parameter, and a balance.
MARKERS = {"call", "any", "balance"}


def _memory(node) -> bool:
    """Whether an expression is a reference to memory, as a struct or an array, and not a function that takes one."""
    type_string = node.get("typeDescriptions", {}).get("typeString") or ""
    return not type_string.startswith("function") and (type_string.endswith(" memory") or " memory " in type_string)


def _address(node) -> bool:
    return node.get("typeDescriptions", {}).get("typeString") in ("address", "address payable")


def _mapping(node) -> bool:
    """Whether an index access is to an entry of a mapping, which no key makes revert, unlike an array index."""
    return node["baseExpression"]["typeDescriptions"]["typeString"].startswith("mapping(")


_LOCALS = (
    "a read map key names one value for the whole call, so it must change nothing, and each local in it must be one "
    "that only its declaration assigns, or the key must be read at one place only, in a function that runs once"
)


def _assigned(node) -> set[int]:
    """The declarations that an assignment in `node` changes: an identifier on its left, also in a tuple."""
    targets = [n["leftHandSide"] for n in _subtree(node) if n.get("nodeType") == "Assignment"]
    assigned = set()
    while targets:
        target = targets.pop()
        if target is not None and target["nodeType"] == "TupleExpression":
            targets.extend(target["components"])
        elif target is not None and target["nodeType"] == "Identifier":
            assigned.add(target["referencedDeclaration"])
    return assigned


UNSAFE_ASSEMBLY = (
    "runs inline assembly that is not marked memory-safe, which could write a memory struct of the function that the "
    "model has as values"
)


class Reads:
    def _storage_path(self, node, scope: dict) -> tuple[str, dict | None] | None:
        """The read map prefix of the storage that `node` names, and the storage variable that holds it, or None for a
        storage parameter of the modeled function: a storage variable (`$config`), an entry of a mapping at a key that
        names one entry for the whole call (`$spenders[_who]`, `$spenders[msg.sender]`), a storage pointer, or a field
        of one of these (`spender.config`)."""
        kind = node["nodeType"]
        if kind == "Identifier":
            entry = scope.get(node.get("referencedDeclaration"))
            if isinstance(entry, tuple) and entry[0] == "pointer":
                return entry[1], entry[2]
            state = self._state_variable(node)
            return (state["name"], state) if state is not None and state.get("mutability") == "mutable" else None
        if kind == "IndexAccess" and _mapping(node) and node.get("indexExpression") is not None:
            base, key = self._storage_path(node["baseExpression"], scope), self._key(node["indexExpression"], scope)
            return (f"{base[0]}[{key}]", base[1]) if base is not None and key is not None else None
        if kind == "MemberAccess":
            base = self._storage_path(node["expression"], scope)
            return (f"{base[0]}.{node['memberName']}", base[1]) if base is not None else None
        return None

    def _key(self, node, scope: dict) -> str | None:
        """A mapping key that names one entry for the whole call: an input of the modeled function, also of a
        user-defined value type, or a field of `tx`, `block` or `msg`."""
        if node["nodeType"] == "Identifier":
            entry = scope.get(node.get("referencedDeclaration"))
            if isinstance(entry, Var) and entry.input:
                return entry.name
            return entry[1] if isinstance(entry, tuple) and entry[0] == "opaque" else None
        if node["nodeType"] == "MemberAccess" and node["expression"]["nodeType"] == "Identifier":
            base = node["expression"]
            if base["name"] in ("tx", "block", "msg") and base.get("referencedDeclaration", 0) < 0:
                return _normal(self.text(node))
        return None

    def _through(self, entry: tuple, root, node) -> str:
        """The read map key of `node`, a place that a copy, a struct parameter or a pointer `root` leads to:
        `$spenders[_who].config.cap` for `spender.config.cap`."""
        return entry[1] + _normal(self.text(node))[len(root["name"]) :]

    def _unmapped_storage(self, node) -> None:
        """Refuses a read of storage that the read map does not name and that has no other form, such as a mapping
        entry `$balances[_who]` or `position[id][borrower].collateral`, with the read as missing."""
        root = _root(node)
        decl = self.nodes.get(root.get("referencedDeclaration")) if root else None
        if decl is not None and decl.get("stateVariable") and decl.get("mutability") != "constant":
            key = _normal(self.text(node))
            missing = Missing(key, "a storage read", self.src(node))
            self.fail(node, f"reads the storage {key}, which the read map does not name", missing)

    def _read(self, node, scope: dict) -> Var | None:
        """The input for a read, or None if the expression is not a read. A read that the read map lacks is an error."""
        kind = node["nodeType"]
        key = None
        what = None
        copy = state = pointer = None
        balance = False
        root = _root(node) if kind in ("MemberAccess", "IndexAccess") else None
        through = scope.get(root.get("referencedDeclaration")) if root is not None else None
        if isinstance(through, tuple) and through[0] == "struct":
            # A field of a struct that the function builds is a local of the model, not a read.
            return self._struct_field(node, through)
        if isinstance(through, tuple) and through[0] == "returned":
            # A field of a struct from an external call is a read of the call's field (structs.py).
            if node["nodeType"] != "MemberAccess" or node["expression"] is not root:
                self.fail(node, f"{self.text(node)} is not a field of the struct that an external call returns")
            member = self.nodes[node["referencedDeclaration"]]
            return self._returned_field(through, member, node)
        below = isinstance(through, tuple) and through[0] in ("copy", "param", "pointer")
        if below:
            if node["typeDescriptions"]["typeString"].startswith(("struct ", "mapping(")):
                self.fail(node, f"{self.text(node)} is read other than by a field or an entry")
            key = self._through(through, root, node)
            what = "a field of the struct parameter" if through[0] == "param" else "a storage field"
            if through[0] == "pointer" and through[2] is None:
                what = "a field of the storage parameter"
            copy = through[4] if through[0] == "copy" else None
            pointer = through if through[0] == "pointer" else None
        elif kind == "MemberAccess" and node["memberName"] == "balance" and _address(node["expression"]):
            key, what, balance = _normal(self.text(node)), "the balance of an address", True
        elif kind == "MemberAccess" and node["expression"]["nodeType"] == "Identifier":
            base = node["expression"]
            declared = self.nodes.get(base.get("referencedDeclaration"))
            if declared is not None and declared.get("stateVariable") and declared.get("mutability") != "constant":
                key, what, state = f"{declared['name']}.{node['memberName']}", "a storage field", declared
            elif base["name"] in ("tx", "block", "msg") and base.get("referencedDeclaration", 0) < 0:
                key, what = self.text(node), "an environment field"
        elif kind == "Identifier" and node.get("referencedDeclaration") not in scope:
            declared = self.nodes.get(node.get("referencedDeclaration"))
            if declared is not None and declared.get("stateVariable") and declared.get("mutability") != "constant":
                if declared["typeDescriptions"]["typeString"].startswith("struct "):
                    self.fail(node, f"the storage struct {declared['name']} is read other than by field")
                key, what, state = declared["name"], f"the {declared['mutability']} variable", declared
        fallback = key is None
        if fallback:
            if kind == "Identifier" and node.get("referencedDeclaration") in scope:
                # A local or a parameter, though the read map may name a storage variable of the same name.
                return None
            key = self.text(node)
            if _normal(key) not in self.read_map:
                return None
        entry = self.read_map.get(_normal(key))
        if entry is None:
            self.fail(node, f"reads {what} {key}, which the read map does not name", Missing(key, what, self.src(node)))
        if node.get("typeDescriptions", {}).get("typeString", "").startswith("tuple("):
            self.fail(node, f"the read {key} gives several values, and a read map entry names one")
        self._stable(key, node, scope)
        if copy is None and (_normal(key), None) in self.outputs and not self.stack:
            # The place of an output that the function wrote: the output holds its value, which starts at the read. A
            # memory copy keeps the value from before the write.
            return self.outputs[(_normal(key), None)].var
        if copy in self.written_copies:
            self.fail(node, f"reads {key} after a write to its memory copy: the model does not see the write")
        if state is not None:
            self._state_read(state, node)
        if pointer is not None:
            self._storage_read(pointer[2], node)
        if balance:
            self._balance_read(node, scope)
        if fallback or below:
            # The indexes below a copy, a parameter or a pointer are any expressions, as in other read map keys.
            self._fallback_read(node, scope)
        if _normal(key) not in self.reads:
            var = Var(entry[1], self._type(node), input=True, src=self.src(node))
            self.reads[_normal(key)] = Read(var, entry[0])
        return self.reads[_normal(key)].var

    def _stable(self, key: str, node, scope: dict) -> None:
        """A read map key names one value for the whole call. So it changes nothing, it runs in a callee that runs once
        if it names the callee's locals or parameters (_run_once), each read of it names the same things (_identity),
        and a local that it names is one that only its declaration assigns, or else the key is read at one place only,
        where it names the local's value there."""
        for n in _subtree(node):
            kind = n.get("nodeType")
            if kind == "Assignment" or (kind == "UnaryOperation" and n["operator"] in ("++", "--", "delete")):
                self.fail(n, f"the read {key} changes {self.text(n)}: {_LOCALS}")
            var = scope.get(n.get("referencedDeclaration")) if kind == "Identifier" else None
            if isinstance(var, tuple) and var[0] in ("struct", "returned") and self.stack:
                # The fields of a struct that a callee builds or takes can differ at each run of the callee.
                self.keyed.add(self.stack[-1])
            if isinstance(var, Var) and not var.input:
                if n["referencedDeclaration"] in self.assigned:
                    # The key's one place runs at most once in the call, so the input is the value that it has there.
                    place = self.key_places.setdefault(_normal(key), node["id"])
                    if place != node["id"]:
                        self.fail(
                            n,
                            f"the read {key} names {n['name']}, which an assignment changes, at a second place: "
                            f"{_LOCALS}",
                        )
                if self.stack:
                    self.keyed.add(self.stack[-1])
        identity = self._identity(node, scope)
        if self.key_identities.setdefault(_normal(key), identity) != identity:
            self.fail(node, f"the read {key} names other things than at its first read: {_LOCALS}")

    def _identity(self, node, scope: dict) -> tuple:
        """What an expression names, beyond its text: the declaration of each identifier and of each member, also the
        one that runs for a function (_declaration_of), a copy or a pointer as the identity of its place, so that a read
        through it is the read of that place, and each literal, whose text loses its spaces in a read map key."""
        names = []
        for n in _subtree(node):
            if n.get("nodeType") == "Literal":
                names.append(("literal", n.get("hexValue")))
            elif n.get("nodeType") == "MemberAccess":
                names.append(("member", n["memberName"], self._declaration_of(n, self._within())))
            elif n.get("nodeType") == "Identifier":
                entry = scope.get(n.get("referencedDeclaration"))
                if isinstance(entry, tuple) and entry[0] in ("copy", "pointer"):
                    names.extend(entry[3])
                elif isinstance(entry, tuple) and entry[0] == "param":
                    # A memory struct parameter of the target, also through an alias of it.
                    names.extend(entry[2])
                else:
                    names.append(self._declaration_of(n, self._within()))
        return tuple(names)

    def _declaration_of(self, node, within: int):
        """The declaration that an identifier or a member access names, in a function of the contract `within`. For a
        function, also the one that runs (_runs): two calls of one function can bind named arguments by the parameter
        names of two declarations."""
        decl = self.nodes.get(node.get("referencedDeclaration"))
        if decl is not None and decl["nodeType"] == "FunctionDefinition":
            return decl["id"], self._runs(node, decl, within)["id"]
        return node.get("referencedDeclaration")

    def _run_once(self) -> None:
        """Refuses a callee that names its own locals or parameters in a read map key and runs more than once in the
        call, as one input would stand for the value at each run."""

        def runs(callee: int) -> int:
            return sum(1 if caller is None else runs(caller) for caller, c in self.calls if c == callee)

        for callee in sorted(self.keyed):
            if runs(callee) > 1:
                name = self.nodes[callee]["name"]
                self.fail(self.nodes[callee], f"{name} runs {runs(callee)} times in the call: {_LOCALS}")

    def _state_read(self, decl, node) -> None:
        """Records a read of the storage variable `decl`, which must not follow a write to it."""
        if decl["id"] in self.written:
            self.fail(node, f"reads {decl['name']} after a write to it: the model does not see the write")
        if EXTERNAL in self.written and decl.get("mutability") == "mutable":
            self.fail(node, f"reads {decl['name']} after an external call, which could change it")
        for reads in self.reading:
            reads.add(decl["id"])

    def _balance_read(self, node, scope: dict) -> None:
        """Records a read of an address's balance, which an external call can change, and the reads of the address."""
        if EXTERNAL in self.written:
            self.fail(node, f"reads {self.text(node)} after an external call, which could change it")
        self._cannot_revert(node["expression"], scope)
        self._fallback_read(node["expression"], scope)
        for reads in self.reading:
            reads.add("balance")

    def _storage_read(self, state, node) -> None:
        """Records a read through a memory copy or a storage pointer of the storage variable `state`, or of a storage
        parameter of the modeled function (None), which could be any storage, so it must not follow any write."""
        if state is not None:
            return self._state_read(state, node)
        if self.written:
            self.fail(node, "reads a storage parameter after a write, which could have changed it")
        for reads in self.reading:
            reads.add("any")

    def _fallback_read(self, node, scope: dict) -> None:
        """Records the reads of a read map key that is any other expression: each storage variable in it. Each call in
        it must be to a view or pure function, and after a write to an internal pure one, which reads no storage. An
        internal call may take memory only where nothing can reach it after the call (_fresh), and with no assembly in
        its code that is not memory-safe, as it could change it unseen. No call may reach `gasleft`, `gas()` in
        assembly, or a modifier that the target's contract overrides (_varies)."""
        scan = _Scan()
        why = self._varies(node, scan, self._within(), self.contract)
        while why is None and scan.sends and scan.selected:
            why = self._follow(*scan.selected.pop(), scan)
        if why is None and scan.unsafe:
            # Such assembly could write a memory struct that the model has as values: one that exists, one that a later
            # allocation puts where the assembly moved the free memory pointer, or one of a caller of a callee whose
            # model the extraction reuses. So it is refused once any flattened struct exists, here or at the end of the
            # extraction (extractor.py).
            self.unsafe_cut = self.unsafe_cut or node
            if self.flattened:
                why = UNSAFE_ASSEMBLY
        if why is not None:
            self.fail(node, f"the read {self.text(node)} {why}")
        changes = []
        for n in _subtree(node):
            ref = n.get("referencedDeclaration")
            if n.get("nodeType") == "Identifier":
                entry = scope.get(ref)
                if isinstance(entry, tuple) and entry[0] == "pointer":
                    self._storage_read(entry[2], n)
                declared = self.nodes.get(ref)
                if declared is not None and declared.get("stateVariable") and declared.get("mutability") != "constant":
                    self._state_read(declared, n)
                if isinstance(entry, tuple) and entry[0] == "copy" and entry[4] in self.written_copies:
                    self.fail(n, f"reads {n['name']} after a write to it: the model does not see the write")
            elif (
                n is not node
                and n.get("nodeType") == "MemberAccess"
                and n["memberName"] == "balance"
                and _address(n["expression"])
            ):
                # A balance inside a larger key: an external call that can change state may precede no read of it.
                self._balance_read(n, scope)
            elif n.get("nodeType") == "FunctionCall" and n["kind"] == "functionCall":
                callee = n["expression"]
                if callee["nodeType"] == "NewExpression":
                    # A new memory array: an allocation, which runs no code (_fresh).
                    continue
                decl = self.nodes.get(callee.get("referencedDeclaration"))
                if decl is None and callee.get("referencedDeclaration", 0) < 0:
                    continue
                if decl is None or decl["nodeType"] != "FunctionDefinition":
                    self.fail(n, f"the read {self.text(node)} makes a call that the extractor cannot resolve")
                decl = self._runs(callee, decl, self._within())
                # A getter, which a public state variable that overrides the function gives, is a view.
                mutability = decl.get("stateMutability", "view")
                internal = (
                    decl["visibility"] in ("internal", "private")
                    or callee["nodeType"] == "Identifier"
                    or _through_contract(callee)
                )
                if mutability not in ("view", "pure"):
                    changes.append(self._state_changing(node, n, decl, internal))
                given = [*n["arguments"], *([callee["expression"]] if callee["nodeType"] == "MemberAccess" else [])]
                memory = [a for a in given if _memory(a)]
                if internal and memory:
                    # The callee can change memory that it takes, which the model would not see. That memory is safe to
                    # pass when nothing can reach it after the call (_fresh), and the callee's assembly keeps to it.
                    if not all(self._fresh(a) for a in memory):
                        self.fail(n, f"the read {self.text(node)} passes memory to {decl['name']}, which can change it")
                    # Assembly that is marked memory-safe may still write next to the memory that it takes, where the
                    # function's own structs can be, so a cut that takes memory has no assembly at all.
                    if scan.assembly:
                        self.fail(
                            n, f"the read {self.text(node)} passes memory to {decl['name']}, and runs inline assembly"
                        )
                if mutability != "pure" or decl["visibility"] not in ("internal", "private"):
                    if self.written:
                        self.fail(n, f"the read {self.text(node)} follows a write, and its call could read the change")
                    for reads in self.reading:
                        reads.add("call")
        for call in changes:
            # The call can change state as an external call statement can: no storage read may follow it.
            self.written.add(EXTERNAL)
            for writes in self.writing:
                writes.add(EXTERNAL)
            arguments = tuple(_normal(self.text(a)) for a in call["arguments"])
            self._effect(call, "call", _normal(self.text(call)), arguments)

    def _state_changing(self, node, call, decl, internal: bool):
        """A call in a read map key that can change state: taken when it is an external call that is the whole key and
        the whole value of its statement, at one place, under the assumption for external calls that it does not revert
        and does not call back into the contract. Its result is the input. It is an effect of the model, and no storage
        read may follow it (_fallback_read)."""
        key = _normal(self.text(node))
        why = None
        library = self.nodes.get(self.owners.get(decl["id"]), {}).get("contractKind") == "library"
        if internal:
            why = "an internal call that can change state"
        elif library:
            # A library's external function runs by delegatecall, in the contract's own storage.
            why = "a library call, which runs in the contract's own storage"
        elif _this(call["expression"]):
            # A call to the contract itself enters it, which the assumption for external calls excludes.
            why = "a call to the contract itself, which can change its storage"
        elif call is not node:
            why = "a call that can change state inside a larger key"
        elif node["id"] != self.statement_value:
            why = "a call that can change state, which must be the whole value of its statement"
        elif self.key_places.setdefault(key, node["id"]) != node["id"]:
            why = "a call that can change state, at a second place"
        if why is not None:
            self.fail(call, f"the read {self.text(node)} calls {decl['name']}, which can change state: {why}")
        if self.stack:
            # Each run of a callee would make the call again, with its own result.
            self.keyed.add(self.stack[-1])
        return call

    def _fresh(self, node) -> bool:
        """Whether nothing but the call that takes `node` can reach the memory that it is: a struct constructor whose
        arguments are values or such constructors, a new array of a value type, or an internal call that takes no
        memory and has no assembly in its code. Such a call can only return memory that it allocates or decodes, as it
        reaches the caller's memory only through its arguments, while assembly can return any pointer."""
        node = _unwrapped(node)
        if node["nodeType"] != "FunctionCall":
            return False
        if node["kind"] == "structConstructorCall":
            return all(not _memory(a) or self._fresh(a) for a in node["arguments"])
        if node["kind"] != "functionCall":
            return False
        callee = node["expression"]
        if callee["nodeType"] == "NewExpression":
            return (
                re.fullmatch(r"(uint\d*|bool|address)\[\] memory", node["typeDescriptions"]["typeString"]) is not None
            )
        decl = self.nodes.get(callee.get("referencedDeclaration"))
        if decl is None or decl["nodeType"] != "FunctionDefinition":
            return False
        bound = [callee["expression"]] if callee["nodeType"] == "MemberAccess" else []
        if any(_memory(a) for a in (*node["arguments"], *bound)):
            return False
        scan = _Scan()
        why = self._varies(node, scan, self._within(), self.contract)
        return why is None and not scan.assembly and not scan.sends

    def _varies(self, node, scan: _Scan, within: int | None, contract: dict | None) -> str | None:
        """Why an expression, a function or a modifier can give two values in one call, or None, also through each
        function and modifier that it names: it calls `gasleft`, reads `gas()` in assembly other than as the gas that a
        call forwards, reads a function pointer from storage, whose target the scan does not see, or runs a modifier
        that its contract overrides. solc checks a function's mutability against the modifiers that it names, and an
        override of a modifier can change state in a view function.

        The scan follows each function and modifier that the code names, called or not, so a pointer that the code
        passes, as a comparator to a sort, is followed where it is named. A call runs what _runs gives it in the
        contract `contract`, in a function of the contract `within`: the target's contract, and for code that an
        external call reaches, the contract that the receiver's type names. A receiver of another type, such as an
        interface, runs the code that solc names (`contract` None). A function named as a value, as a pointer or in
        `abi.encodeCall(Base.g, ())`, can run as solc names it on any receiver, as _runs gives it, and if it is public
        or external, as a call through `this` runs it: the scan follows each. A function named only for its selector
        (`this.g.selector`) runs only through a call that sends the selector as calldata, so the scan follows it only
        if the code makes a low-level call or calls a pointer to an external function (_Scan). It follows
        user-defined operators too. Code that a low-level call runs is not scanned beyond the function that its
        calldata names in Solidity, like an external call's receiver of another type."""
        nodes = list(_subtree(node))
        called = {id(_unwrapped(n["expression"])) for n in nodes if n.get("nodeType") == "FunctionCall"}
        selected = {
            id(_unwrapped(n["expression"]))
            for n in nodes
            if n.get("nodeType") == "MemberAccess" and n["memberName"] == "selector"
        }
        forwarded = {
            id(g)
            for n in nodes
            if n.get("nodeType") == "YulFunctionCall" and n["functionName"]["name"] in _CALLS and n["arguments"]
            for g in _forwarded(n["arguments"][0])
        }
        for n in nodes:
            kind = n.get("nodeType")
            if kind == "InlineAssembly":
                scan.assembly = True
                scan.unsafe = scan.unsafe or not memory_safe(n)
            if kind == "Identifier" and n["name"] == "gasleft" and (n.get("referencedDeclaration") or 0) < 0:
                return "calls gasleft, whose value changes during the call"
            if kind == "YulFunctionCall":
                name = n["functionName"]["name"]
                scan.sends = scan.sends or name in _CALLS
                if name == "gas" and id(n) not in forwarded:
                    return "reads gas() in assembly, other than as the gas that a call forwards"
                continue
            if kind == "MemberAccess" and n["memberName"] in _CALLS and _address(n["expression"]):
                scan.sends = True
            if kind == "FunctionCall" and self._external_pointer(_unwrapped(n["expression"])):
                scan.sends = True
            if kind in ("Identifier", "IdentifierPath", "MemberAccess"):
                callee = n
            elif kind in ("BinaryOperation", "UnaryOperation") and n.get("function") in self.nodes:
                # A user-defined operator runs a free or library function.
                callee = {"nodeType": "Operator", "referencedDeclaration": n["function"]}
            else:
                continue
            decl = self.nodes.get(callee.get("referencedDeclaration"))
            if decl is not None and decl["nodeType"] == "VariableDeclaration" and self._stored_pointer(decl):
                return f"reads the function pointer {self.text(callee)} from storage, which the scan does not follow"
            if decl is None or decl["nodeType"] not in ("FunctionDefinition", "ModifierDefinition"):
                continue
            owner = self.nodes.get(self.owners.get(decl["id"]), {})
            external = (
                callee["nodeType"] == "MemberAccess"
                and not _through_contract(callee)
                and not _this(callee)
                and owner.get("contractKind") != "library"
            )
            if external:
                runs_in = self._receiver(callee)
                ran = (self._first(runs_in["linearizedBaseContracts"], decl) or decl) if runs_in else decl
                runs = [(ran, runs_in)]
                if id(callee) not in called:
                    # Named as a value, as in abi.encodeCall, it can be sent to a receiver of another type.
                    runs.append((decl, None))
            else:
                ran = self._runs(callee, decl, within, contract) if contract is not None else decl
                if decl["nodeType"] == "ModifierDefinition" and ran["id"] != decl["id"]:
                    overrider = self.nodes[self.owners[ran["id"]]]["name"]
                    return f"runs the modifier {decl['name']} of {overrider}, an override that could change state"
                runs = [(ran, contract)]
                if decl["nodeType"] == "FunctionDefinition" and id(callee) not in called:
                    runs.append((decl, None))
                    if contract is not None and decl.get("visibility") in ("public", "external"):
                        if owner.get("contractKind") == "contract":
                            runs.append((self._first(contract["linearizedBaseContracts"], decl) or decl, contract))
            for ran, runs_in in runs:
                if id(callee) in selected and not scan.sends:
                    scan.selected.append((ran, runs_in))
                    continue
                why = self._follow(ran, runs_in, scan)
                if why is not None:
                    return why
        return None

    def _follow(self, ran, runs_in: dict | None, scan: _Scan) -> str | None:
        """_varies of a function or modifier that runs in an instance of `runs_in`, once in a scan. A getter, which a
        public state variable gives, reads storage only."""
        key = (ran["id"], runs_in["id"] if runs_in else None)
        if ran["nodeType"] == "VariableDeclaration" or key in scan.seen:
            return None
        scan.seen.add(key)
        return self._varies(ran, scan, self.owners.get(ran["id"]), runs_in)

    def _external_pointer(self, callee) -> bool:
        """Whether a callee is a pointer to an external function, whose selector and address can come from any value,
        as assembly can set them, rather than a function that it names."""
        decl = self.nodes.get(callee.get("referencedDeclaration"))
        type_string = callee.get("typeDescriptions", {}).get("typeString") or ""
        named = decl is not None and decl["nodeType"] == "FunctionDefinition"
        return not named and type_string.startswith("function ") and " external" in type_string

    def _stored_pointer(self, decl) -> bool:
        """Whether a variable is storage, or a struct field, that holds a function pointer."""
        type_string = decl.get("typeDescriptions", {}).get("typeString") or ""
        field = self.nodes.get(decl.get("scope"), {}).get("nodeType") == "StructDefinition"
        return bool(_POINTER.search(type_string)) and (decl.get("stateVariable") or field)

    def _receiver(self, member):
        """The contract that the type of an external call's receiver names, or None for an interface or another type."""
        identifier = member["expression"].get("typeDescriptions", {}).get("typeIdentifier") or ""
        match = re.fullmatch(r"t_contract\$_\w+_\$(\d+)", identifier)
        found = self.nodes.get(int(match.group(1))) if match else None
        if found is None or found.get("nodeType") != "ContractDefinition" or found["contractKind"] != "contract":
            return None
        return found

    def _state_variable(self, node):
        if node["nodeType"] != "Identifier":
            return None
        decl = self.nodes.get(node.get("referencedDeclaration"))
        return decl if decl is not None and decl.get("stateVariable") else None
