// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

struct Range {
    uint256 low;
    uint256 high;
}

interface IQuotes {
    function quote(uint256 n) external view returns (uint256);
    function range() external view returns (Range memory);
}

// Internal calls as statements to functions that only read and revert: checks, inlined; and the ones that the
// extractor refuses, as they return early, write, emit, or run twice with a key on their parameter.
contract Checks {
    error NotOwner(address caller);

    event Seen(uint256 value);

    IQuotes internal quotes;
    address internal owner;
    uint256 internal total;

    function _requireTo(address _to) internal pure {
        require(_to != address(0));
    }

    function checked(address _to, uint256 _a) external pure returns (uint256) {
        _requireTo(_to);
        return _a;
    }

    function checkedTwice(address _to, address _from, uint256 _a) external pure returns (uint256) {
        _requireTo(_to);
        _requireTo(_from);
        return _a;
    }

    function _checkOwner() internal view {
        if (owner != msg.sender) {
            revert NotOwner(msg.sender);
        }
    }

    function owned(uint256 _a) external view returns (uint256) {
        _checkOwner();
        return total + _a;
    }

    function _checkRange(Range memory _range) internal pure {
        require(_range.low <= _range.high);
    }

    // A check of a struct that the function holds, and of one that a constructor builds for it.
    function ranged(uint256 _a, uint256 _b) external pure returns (uint256) {
        Range memory r = Range(_a, _b);
        _checkRange(r);
        return r.high - r.low;
    }

    function rangedConstructed(uint256 _a, uint256 _b) external pure returns (uint256) {
        _checkRange(Range(_a, _b));
        return _b - _a;
    }

    // A struct from an external view call, passed whole to the check.
    function rangedFromOutside() external view returns (uint256) {
        _checkRange(quotes.range());
        return 0;
    }

    function _range(uint256 _x) internal pure returns (Range memory) {
        return Range(_x, 1 / _x);
    }

    function _checkGuarded(bool _c, uint256 _x) internal pure {
        require(_c ? _range(_x).low == 0 : true);
    }

    // A struct-valued call under ?: in a check: its temporaries would run where the call does not.
    function guardedInACheck(bool _c, uint256 _x) external pure returns (uint256) {
        _checkGuarded(_c, _x);
        return _x;
    }

    function _early(uint256 _x) internal pure {
        if (_x == 0) {
            return;
        }
        require(_x == 1);
    }

    function early(uint256 _x) external pure returns (uint256) {
        _early(_x);
        return _x + 10;
    }

    function _write() internal {
        total = 1;
    }

    function writes(uint256 _x) external returns (uint256) {
        _write();
        return _x;
    }

    function _emitted() internal {
        emit Seen(1);
    }

    function emits(uint256 _x) external returns (uint256) {
        _emitted();
        return _x;
    }

    function _keyed(uint256 _n) internal view {
        require(quotes.quote(_n) == 0);
    }

    function keyedTwice(uint256 _x) external view returns (uint256) {
        _keyed(1);
        _keyed(2);
        return _x;
    }
}
