// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

// Functions that assign their parameters, as a quote that raises its ceiling for a new account.
contract Parameters {
  uint256 internal $bonus;
  uint256 internal $last;
  mapping(uint256 => uint256) internal $limits;

  function capped(uint256 _ceiling, uint256 _gas, bool _fresh) public view returns (uint256) {
    if (_fresh) {
      _ceiling += $bonus;
    }
    return _gas < _ceiling ? _gas : _ceiling;
  }

  function spread(uint256 _a, uint256 _b) public pure returns (uint256) {
    if (_a < _b) {
      (_a, _b) = (_b, _a);
    }
    return _a - _b;
  }

  function earlyReturn(uint256 _a, bool _small) public pure returns (uint256) {
    if (_small) {
      _a = _a + 1;
      return _a;
    }
    _a = _a * 2;
    return _a;
  }

  function recorded(uint256 _amount) public {
    _amount += $bonus;
    $last = _amount;
  }

  function nested(uint256 _a) public pure returns (uint256) {
    _a += 1;
    return _a + _twice(_a);
  }

  function _twice(uint256 _a) internal pure returns (uint256) {
    _a = _a * 2;
    return _a;
  }

  function limitOf(uint256 _id) public view returns (uint256) {
    _id = _id + 1;
    return $limits[_id];
  }
}
