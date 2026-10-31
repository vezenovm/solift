"""Writes, external calls and events: the effects that the model leaves out, and the outputs that the link compares
after the call."""

from __future__ import annotations

import re

from solift.extract.common import ASSIGNMENTS, EXTERNAL, IDENTIFIER, _normal
from solift.extract.errors import ExtractionError, Missing, Wanted
from solift.ir import (
    Assign,
    Effect,
    Function,
    If,
    Output,
    Read,
    Return,
    Revert,
    Stmt,
    Tuple,
    Type,
    Var,
    parts,
    reserved,
    tuple_of,
)

_KEYS = "each key of an output must be an input or a field of tx, block or msg"


_BEFORE = "name its place in the read map too"


def _before(output: Output) -> Missing | None:
    """The read of an output's value before the call, which a write that some paths make needs."""
    if output.kind != "write":
        return None
    return Missing(output.source, "the value before the call of an output", output.var.src)


def _unwritten(output: Output) -> str:
    if output.kind == "call":
        return f"the output {output.var.name} ({output.source}): make the call on every path that returns"
    return (
        f"the output {output.var.name} ({output.source}): write it on every path that returns, or {_BEFORE}, so that "
        "the model has its value before the call"
    )


def _distinct(a: str, b: str) -> bool:
    """Whether two places of one storage variable differ for every key: they differ at two fields, as
    `$spenders[_a].left` and `$spenders[_b].last`, and not only at keys, which can be equal."""
    steps = zip(*(re.findall(r"\[[^\]]*\]|\.\w+|^[^.\[]+", place) for place in (a, b)), strict=False)
    return any(x != y and x.startswith(".") and y.startswith(".") for x, y in steps)


class Effects:
    def _with_outputs(self, function: Function, qualified: str) -> Function:
        """The modeled function with its outputs as named return variables after its own values: each return returns
        them too, and an output with a value before the call starts at it. An output without one must be written on
        every path that returns. Another write to the storage variable of an output is refused, as it could change the
        output's place."""
        for written, _ in self.output_map.values():
            if not any(o.source == written for o in self.outputs.values()):
                raise ExtractionError(f"the output map names {written}, which {qualified} does not write or call")
        for state, place, node in self.other_writes:
            if state in self.output_states:
                message = f"writes {place}, which could change the output {self.output_states[state][0]}"
                self.fail(node, message, output=Wanted(place))
        outputs = list(self.outputs.values())
        own = parts(function.returns)
        if not outputs:
            if not own:
                message = f"{qualified} returns no value: name a write or an external call in the output map"
                raise ExtractionError(message, effects=tuple(self.effects))
            return function
        for output in outputs:
            name = output.var.name
            if not IDENTIFIER.fullmatch(name) or name == "i" or reserved(name):
                raise ExtractionError(f"{name} is not a valid output name for {output.source}")
        needed = {o.var.name: o for o in outputs if o.before is None}
        ends = self._written_outputs(function.body, set(needed), set())
        if ends is not None and set(needed) - ends:
            output = needed[sorted(set(needed) - ends)[0]]
            message = f"{function.src}: {qualified} can end without a write of {_unwritten(output)}"
            raise ExtractionError(message, _before(output))
        returns = tuple_of((*own, *(o.var.type for o in outputs)))

        def returned(stmt):
            if isinstance(stmt, If):
                return If(stmt.cond, tuple(map(returned, stmt.then)), tuple(map(returned, stmt.orelse)), stmt.src)
            if not isinstance(stmt, Return):
                return stmt
            if len(own) > 1 and not isinstance(stmt.expr, Tuple):
                raise ExtractionError(f"{stmt.src}: a return of a call of several values in a function with outputs")
            values = () if not own else stmt.expr.args if len(own) > 1 else (stmt.expr,)
            values = (*values, *(o.var for o in outputs))
            return Return(values[0] if len(values) == 1 else Tuple(values, returns), stmt.src)

        start = tuple(Assign(o.var, o.before, o.var.src) for o in outputs if o.before is not None)
        body = start + tuple(returned(stmt) for stmt in function.body)
        results = (*(function.results or (None,) * len(own)), *(o.var for o in outputs))
        return Function(function.name, function.params, returns, body, function.src, results)

    def _written_outputs(self, stmts, needed: set, written: set) -> set | None:
        """The outputs in `needed` that are written where the statements end without a return, or None where they
        always return or revert. Refuses a return that one of them may not reach written."""
        written = set(written)
        for stmt in stmts:
            if isinstance(stmt, Assign) and stmt.var.name in needed:
                written.add(stmt.var.name)
            elif isinstance(stmt, Return):
                missing = sorted(needed - written)
                if missing:
                    output = next(o for o in self.outputs.values() if o.var.name == missing[0])
                    message = f"{stmt.src}: returns without a write of {_unwritten(output)}"
                    raise ExtractionError(message, _before(output))
                return None
            elif isinstance(stmt, Revert):
                return None
            elif isinstance(stmt, If):
                ends = [self._written_outputs(branch, needed, written) for branch in (stmt.then, stmt.orelse)]
                ends = [end for end in ends if end is not None]
                if not ends:
                    return None
                written = set.intersection(*ends)
        return written

    def _output(self, key: str, index: int | None, name: str, type_: Type, node, kind: str) -> Output:
        """The output of a place or of a call's argument, made when the extractor first meets it. A place that the read
        map names too has the read as its value before the call."""
        if (key, index) in self.outputs:
            return self.outputs[(key, index)]
        before = None
        if index is None and key in self.read_map:
            if key not in self.reads:
                var = Var(self.read_map[key][1], type_, input=True, src=self.src(node))
                self.reads[key] = Read(var, self.read_map[key][0])
            before = self.reads[key].var
            if before.type != type_:
                self.fail(node, f"the read {key} is a {before.type.solidity} and the output a {type_.solidity}")
        written = self.output_map[key][0]
        output = Output(Var(name, type_, src=self.src(node)), written, kind, index, before)
        self.outputs[(key, index)] = output
        return output

    def _external(self, call) -> bool:
        """Whether a call is to a function of another contract, or of this one through `this`, and not to a library
        function bound to a contract value, as `token.forceApprove(...)` under `using SafeERC20 for IERC20`."""
        callee = call["expression"]
        decl = self.nodes.get(callee.get("referencedDeclaration"))
        if callee["nodeType"] != "MemberAccess" or decl is None or decl["nodeType"] != "FunctionDefinition":
            return False
        if self.nodes.get(decl["scope"], {}).get("contractKind") == "library":
            return False
        return callee["expression"]["typeDescriptions"]["typeString"].startswith("contract ")

    def _external_call(self, call, scope: dict) -> tuple[Stmt, ...]:
        """An external call as a statement, as `psm.buyGem(msg.sender, amt);`. The model assumes that it does not revert
        and does not call back into the contract. Its arguments are outputs where the output map names them, a tuple of
        a name or None for each argument under the call's source text, and the others must not revert. A call that can
        change state can change any storage, so no read of storage may follow it."""
        callee = call["expression"]
        decl = self.nodes.get(callee.get("referencedDeclaration"))
        if decl is None or decl["nodeType"] != "FunctionDefinition":
            self.fail(call, f"a call that the extractor cannot resolve: {self.text(call)}")
        key = _normal(self.text(call))
        arguments = self._arguments(call, decl)
        self._cannot_revert(callee["expression"], scope)
        names = self.output_map[key][1] if key in self.output_map else (None,) * len(arguments)
        if not isinstance(names, tuple | list) or len(names) != len(arguments):
            self.fail(call, f"the output map names {len(names)} values of {key}, which has {len(arguments)} arguments")
        if key in self.output_map and self.stack:
            self.fail(call, f"{key} is an output in the callee {self.nodes[self.stack[-1]]['name']}")
        stmts = []
        params = decl["parameters"]["parameters"]
        texts = tuple(_normal(self.text(arg)) for arg in arguments)
        for index, (arg, name, param) in enumerate(zip(arguments, names, params, strict=True)):
            if name is None:
                try:
                    self._cannot_revert(arg, scope)
                except ExtractionError as err:
                    wanted = Wanted(key, index, texts)
                    raise ExtractionError(f"{err}; name it in the output map to model it", output=wanted) from None
                continue
            value = self._expr(arg, scope)
            output = self._output(key, index, name, self._type(param), arg, "call")
            self._assignable(arg, value.type, output.var.type)
            stmts.append(Assign(output.var, value, self.src(arg)))
        if key not in self.output_map:
            self._effect(call, "call", key, texts)
        if decl["stateMutability"] not in ("view", "pure"):
            self.written.add(EXTERNAL)
            for writes in self.writing:
                writes.add(EXTERNAL)
        return tuple(stmts)

    def _storage_write(self, expression, scope: dict, place: str, state) -> Assign | None:
        """A write to storage: an output where the output map names its place, and an effect otherwise. The value of an
        output can revert, and a compound assignment to one starts from its value before the call. The value runs
        before the write, so it can read what the write changes."""
        if place not in self.output_map:
            self._write(expression, scope, "write", place)
            self._wrote(state)
            self.other_writes.append((state["id"], place, expression))
            return None
        if self.stack:
            self.fail(expression, f"writes the output {place} in the callee {self.nodes[self.stack[-1]]['name']}")
        name = self.output_map[place][1]
        if not isinstance(name, str):
            self.fail(expression, f"the output map names {place} with {name!r}, not a name")
        lhs, rhs, operator = expression["leftHandSide"], expression["rightHandSide"], expression["operator"]
        if self._storage_path(lhs, scope) is None:
            self.fail(lhs, f"the output {place} is at a key that can change during the call: {_KEYS}")
        self._cannot_revert(lhs, scope)
        # The place is a read map key too where the output has its value before the call.
        self._stable(place, lhs, scope)
        output = self._output(place, None, name, self._type(lhs), lhs, "write")
        places = self.output_states.setdefault(state["id"], [])
        for other in places:
            if other != place and not _distinct(other, place):
                self.fail(lhs, f"the outputs {other} and {place} are one place where their keys are equal")
        if place not in places:
            places.append(place)
        if operator not in ASSIGNMENTS:
            self.fail(expression, f"the assignment operator {operator}")
        value = self._expr(rhs, scope)
        if ASSIGNMENTS[operator] is not None:
            if output.before is None:
                missing = Missing(output.source, "the value before the call of an output", self.src(lhs))
                message = f"{operator} to the output {place} needs its value before the call: {_BEFORE}"
                self.fail(expression, message, missing)
            value = self._op(expression, ASSIGNMENTS[operator], output.var, value)
        self._assignable(rhs, value.type, output.var.type)
        self._wrote(state)
        return Assign(output.var, value, self.src(expression))

    def _write(self, expression, scope: dict, kind: str, place: str | None = None) -> None:
        place = place or _normal(self.text(expression["leftHandSide"]))
        if expression["operator"] != "=":
            wanted = Wanted(place) if kind == "write" else None
            self.fail(expression, f"a write with {expression['operator']}: its value can revert", output=wanted)
        self._cannot_revert(expression["leftHandSide"], scope)
        try:
            self._cannot_revert(expression["rightHandSide"], scope)
        except ExtractionError as err:
            if kind != "write":
                raise
            raise ExtractionError(f"{err}; name {place} in the output map to model it", output=Wanted(place)) from None
        self._effect(expression, kind, place)

    def _wrote(self, state) -> None:
        """Records a write to the storage variable `state`, here and in each callee being extracted."""
        self.written.add(state["id"])
        for writes in self.writing:
            writes.add(state["id"])

    def _effect(self, node, kind: str, place: str, arguments: tuple[str, ...] = ()) -> None:
        self.effects.append(Effect(kind, place, " ".join(self.text(node).split()), self.src(node), arguments))

    def _event(self, call) -> str:
        """The signature of the event that an emit calls, such as `Updated(uint256)`."""
        decl = self.nodes.get(call["expression"].get("referencedDeclaration"))
        if decl is None or decl["nodeType"] != "EventDefinition":
            return _normal(self.text(call["expression"]))
        types = [p["typeDescriptions"]["typeString"] for p in decl["parameters"]["parameters"]]
        return f"{decl['name']}({','.join(types)})"

    def _cannot_revert(self, node, scope: dict) -> None:
        """Refuses a part of a write or an event that could revert: the model leaves it out."""
        kind = node["nodeType"]
        type_string = node.get("typeDescriptions", {}).get("typeString") or ""
        if kind == "MemberAccess" and self._type_bound(node) is not None:
            return
        if type_string.startswith("function"):
            pass
        elif kind in ("Identifier", "Literal"):
            return
        elif kind == "MemberAccess":
            return self._cannot_revert(node["expression"], scope)
        elif kind == "IndexAccess":
            base = node["baseExpression"]
            if base["typeDescriptions"]["typeString"].startswith("mapping(") and node.get("indexExpression"):
                self._cannot_revert(base, scope)
                return self._cannot_revert(node["indexExpression"], scope)
        elif kind == "TupleExpression" and len(node["components"]) == 1 and not node.get("isInlineArray"):
            return self._cannot_revert(node["components"][0], scope)
        elif kind == "FunctionCall" and node["kind"] == "typeConversion" and len(node["arguments"]) == 1:
            if re.fullmatch(r"uint\d+|address( payable)?|contract [\w.]+", type_string):
                return self._cannot_revert(node["arguments"][0], scope)
        self.fail(node, f"{self.text(node)} in a write or an event: the model leaves it out, so it must not revert")
