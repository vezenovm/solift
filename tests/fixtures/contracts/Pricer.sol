// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {IToken} from "./Quoter.sol";
import {Kinds} from "./Kinds.sol";

IToken constant OTHER = IToken(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48);
address constant NOBODY = address(0);
bool constant STRICT = true;

// A gas price with the shapes of a subsidy's priced gas: an enum parameter, an address parameter compared with an
// immutable and a constant, a struct parameter, locals assigned in branches, compound assignments, a named return, and
// a quote that reads a mapping entry through a memory copy and cuts an internal call.
contract Pricer {
  struct Meter {
    uint256 startGas;
    uint256 used;
    bool fresh;
    bool paid;
  }

  struct Rate {
    uint256 floor;
    uint256 cap;
  }

  uint256 internal constant BASE = 21_000;
  uint256 internal constant EXTRA = 5000;

  IToken public immutable TOKEN;
  mapping(Kinds.Kind kind => Rate rate) internal $rates;

  constructor(IToken _token) {
    TOKEN = _token;
  }

  function setRate(Kinds.Kind _kind, uint256 _floor, uint256 _cap) external {
    $rates[_kind] = Rate({floor: _floor, cap: _cap});
  }

  function _priced(Kinds.Kind _kind, address _token, Meter memory _meter) internal view returns (uint256) {
    uint256 ceiling = _ceiling(_kind, _token);
    if (!_meter.paid || ceiling == 0) {
      return 0;
    }
    uint256 gross = _meter.used + BASE;
    if (TOKEN.balanceOf(tx.origin) == 0) {
      gross += EXTRA;
      ceiling += EXTRA;
    }
    uint256 refund = _refund(_token) + (_meter.fresh ? 100 : 200);
    refund = Math.min(refund, gross / 5);
    return Math.min(gross - refund, ceiling);
  }

  function _ceiling(Kinds.Kind _kind, address _token) internal view returns (uint256) {
    uint256 ceiling;
    if (_kind == Kinds.Kind.Small) {
      ceiling = 1000;
    } else if (_kind == Kinds.Kind.Large) {
      ceiling = 9000;
    } else {
      return 0;
    }
    if (_token == address(TOKEN)) {
      return ceiling;
    }
    if (_token == address(OTHER)) {
      return ceiling + 700;
    }
    return 0;
  }

  function _refund(address _token) internal view returns (uint256 refund) {
    refund = 300;
    if (_token != address(TOKEN)) {
      refund += 400;
    }
  }

  function quoteFor(Kinds.Kind _kind) public view returns (uint256) {
    Rate memory rate = $rates[_kind];
    uint256 quoted = units(_kind) * _price();
    if (quoted <= rate.floor) {
      return 0;
    }
    quoted = Math.min(quoted - rate.floor, rate.cap);
    return quoted;
  }

  function units(Kinds.Kind _kind) public pure returns (uint256) {
    if (_kind == Kinds.Kind.Small) {
      return 3;
    }
    if (_kind == Kinds.Kind.Large) {
      return 7;
    }
    return 1;
  }

  function _price() internal view returns (uint256) {
    return TOKEN.balanceOf(address(0xFEE));
  }

  function _named(uint256 _x, uint256 _y) internal pure returns (uint256) {
    return _difference({_b: _x, _a: _y}) + Math.mulDiv({x: _x, denominator: 1, y: 2});
  }

  function _difference(uint256 _a, uint256 _b) internal pure returns (uint256) {
    return _a - _b;
  }

  function _isNobody(address _a) internal pure returns (bool) {
    return _a == NOBODY;
  }

  function _wide(bool _flag, uint8 _x) internal pure returns (uint256) {
    return (_flag ? _x : 57896044618658097711785492504343953926634992332820282019728792003956564819968) + 1;
  }

  function _strict(uint256 _a) internal pure returns (bool) {
    return STRICT && _a > 0;
  }

  function _isOne(address _a) internal pure returns (bool) {
    return _a == 0x0000000000000000000000000000000000000001;
  }
}
