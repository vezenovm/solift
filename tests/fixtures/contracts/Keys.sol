// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

interface IGate {
    function quote(address who, uint256 amount) external view returns (uint256);
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
contract Keys {
    IGate internal gate;
    IPairs internal pairs;

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
}
