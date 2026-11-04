// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

interface IKeyedQuotes {
    function quote(uint256 n) external view returns (uint256);
}

// Modifiers on the target, inlined before its body; and the ones that the extractor refuses, as they run code after
// `_;`, run it twice, or read one key at two places.
contract Gated is Ownable {
    uint256 internal total;
    address internal keeper;
    IKeyedQuotes internal quotes;

    constructor() Ownable(msg.sender) {}

    modifier onlyKeeper() {
        require(msg.sender == keeper);
        _;
    }

    modifier atLeast(uint256 _min, uint256 _value) {
        require(_value >= _min);
        _;
    }

    modifier resetAfter() {
        _;
        total = 0;
    }

    modifier twice() {
        _;
        _;
    }

    modifier quoting() {
        uint256 n = 0;
        uint256 a = quotes.quote(n);
        n = 1;
        uint256 b = quotes.quote(n);
        require(a == b);
        _;
    }

    function _get(uint256 _n) internal view returns (uint256) {
        return quotes.quote(_n);
    }

    modifier gate() {
        require(_get(1) == 0);
        _;
    }

    function setKeeper(address _keeper) external {
        keeper = _keeper;
    }

    function setTotal(uint256 _total) external {
        total = _total;
    }

    function keeperAdd(uint256 _a) external view onlyKeeper returns (uint256) {
        return total + _a;
    }

    function ownerAdd(uint256 _a) external view onlyOwner returns (uint256) {
        return total + _a;
    }

    function bounded(uint256 _a) external view atLeast(10, _a) onlyKeeper returns (uint256) {
        return _a - 10;
    }

    function resetting(uint256 _a) external resetAfter returns (uint256) {
        return _a;
    }

    function runTwice(uint256 _a) external view twice returns (uint256) {
        return _a;
    }

    function quoted(uint256 _a) external view quoting returns (uint256) {
        return _a;
    }

    function gated(uint256 _a) external view gate returns (uint256) {
        return _get(2) + _a;
    }

    function keeperTwice(uint256 _a) external view onlyKeeper onlyKeeper returns (uint256) {
        return _a;
    }

    struct Bound {
        uint256 low;
    }

    function _bound(uint256 _x) internal pure returns (Bound memory) {
        return Bound(_x);
    }

    // A modifier argument that reads a field of a struct that an internal call returns, which unpacks before it; and
    // one under ?:, which the extractor refuses, as the call would run on every path.
    function boundedByCall(uint256 _a) external pure atLeast(_bound(5).low, _a) returns (uint256) {
        return _a;
    }

    function boundedIf(bool _c, uint256 _a) external pure atLeast(_c ? _bound(5).low : 0, _a) returns (uint256) {
        return _a;
    }
}
