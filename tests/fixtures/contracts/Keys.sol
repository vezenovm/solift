// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

interface IGate {
    function quote(address who, uint256 amount) external view returns (uint256);
    function allowed(address who, uint256 amount) external returns (uint256);
}

struct Pair {
    uint256 x;
    uint256 y;
}

interface IPairs {
    function get(uint256 n) external view returns (Pair memory);
}

// Read map keys that name a local that an assignment changes: taken where the key has one place, refused where it is
// read at two, or in a callee that runs twice.
library Counter {
    struct Box {
        uint256 v;
    }

    function bump(Box storage _box) public returns (uint256) {
        _box.v += 1;
        return _box.v;
    }
}

contract Keys {
    IGate internal gate;
    Counter.Box internal box;
    uint256 internal immutable START = 7;
    IPairs internal pairs;
    uint256 internal total;

    function setGate(IGate _gate) external {
        gate = _gate;
    }

    function quoted(address _who, uint256 _byShares, uint256 _byAmount) external view returns (uint256) {
        uint256 amount;
        if (_byShares > 0) {
            amount = _byShares * 2;
        } else {
            amount = _byAmount;
        }
        return gate.quote(_who, amount);
    }

    function quotedThenAssigned(address _who, uint256 _a) external view returns (uint256) {
        uint256 amount = _a;
        uint256 price = gate.quote(_who, amount);
        amount = amount + 1;
        return price + amount;
    }

    function quotedTwice(address _who, uint256 _a) external view returns (uint256) {
        uint256 amount = _a;
        uint256 first = gate.quote(_who, amount);
        amount = _a + 1;
        return first + gate.quote(_who, amount);
    }

    function _quoteOf(address _who, uint256 _a) internal view returns (uint256) {
        uint256 amount = _a;
        amount = amount + 1;
        return gate.quote(_who, amount);
    }

    function quotedInACalleeTwice(address _who) external view returns (uint256) {
        return _quoteOf(_who, 1) + _quoteOf(_who, 2);
    }

    // A field of an external call's struct, read through the struct and again in an expression after its argument
    // changed: one key at two places.
    function fieldThenDirect() external view returns (uint256) {
        uint256 n = 1;
        Pair memory s = pairs.get(n);
        uint256 a = s.x;
        n = 2;
        uint256 b = pairs.get(n).x;
        return a + b;
    }

    // Two fields of one call text at two places, each read once.
    function twoFields() external view returns (uint256) {
        uint256 n = 1;
        Pair memory s = pairs.get(n);
        uint256 a = s.x;
        n = 2;
        Pair memory t = pairs.get(n);
        return a + t.y;
    }

    // The result of a call that is not a view, as a policy hook's code.
    function take(address _who, uint256 _amount) external returns (uint256) {
        uint256 ok = gate.allowed(_who, _amount);
        require(ok == 0);
        return _amount;
    }

    function takeByShares(address _who, uint256 _byShares, uint256 _byAmount) external returns (uint256) {
        uint256 amount;
        if (_byShares > 0) {
            amount = _byShares * 2;
        } else {
            amount = _byAmount;
        }
        uint256 ok = gate.allowed(_who, amount);
        require(ok == 0);
        return amount;
    }

    function takeThenRead(address _who, uint256 _amount) external returns (uint256) {
        uint256 ok = gate.allowed(_who, _amount);
        return ok + total;
    }

    function takeInAnExpression(address _who, uint256 _amount) external returns (uint256) {
        return gate.allowed(_who, _amount) + 1;
    }

    function takeTwice(address _who, uint256 _amount) external returns (uint256) {
        uint256 a = gate.allowed(_who, _amount);
        uint256 b = gate.allowed(_who, _amount);
        return a + b;
    }

    function takeAfterAWrite(address _who, uint256 _amount) external returns (uint256) {
        total = _amount;
        uint256 ok = gate.allowed(_who, _amount);
        return ok;
    }

    function bumpItself() external returns (uint256) {
        total = 9;
        return 0;
    }

    // A call to the contract itself, and an external library function, which run in its storage.
    function selfCall(uint256 _x) external returns (uint256) {
        uint256 ok = this.bumpItself();
        return ok + _x;
    }

    function libraryCall(uint256 _x) external returns (uint256) {
        uint256 v = Counter.bump(box);
        return v + _x;
    }

    function selfCallStatement(uint256 _x) external returns (uint256) {
        this.bumpItself();
        return _x;
    }

    // A balance inside a larger key, after the call.
    function balanceAfter(address _who, uint256 _amount) external returns (uint256) {
        uint256 ok = gate.allowed(_who, _amount);
        return ok + (address(gate).balance + 0);
    }

    function assigned(address _who, uint256 _amount) external returns (uint256) {
        uint256 ok;
        ok = gate.allowed(_who, _amount);
        return ok;
    }

    function _start() internal view returns (uint256) {
        return START;
    }

    // An immutable, read through a callee before and after the call, which cannot change it.
    function immutableAround(address _who, uint256 _amount) external returns (uint256) {
        uint256 before = _start();
        uint256 ok = gate.allowed(_who, _amount);
        return before + ok + _start();
    }

    function _bump() internal returns (uint256) {
        total = total + 1;
        return total;
    }

    function bumped(uint256 _x) external returns (uint256) {
        uint256 b = _bump();
        return b + _x;
    }
}
