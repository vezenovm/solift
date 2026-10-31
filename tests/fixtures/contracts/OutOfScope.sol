// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

// One function for each construct that the extractor refuses.
contract OutOfScope {
  uint256 internal $total;

  function loop(uint256 _n) internal pure returns (uint256) {
    uint256 sum = 0;
    for (uint256 k = 0; k < _n; k++) {
      sum += k;
    }
    return sum;
  }

  function storageWrite(uint256 _x) internal returns (uint256) {
    $total += _x;
    return _x;
  }

  function uncheckedBlock(uint256 _a) internal pure returns (uint256) {
    unchecked {
      return _a + 1;
    }
  }

  function signedParameter(int256 _a) internal pure returns (uint256) {
    return _a > 0 ? 1 : 0;
  }

  function cast(uint256 _a) internal pure returns (uint256) {
    return uint128(_a);
  }

  function tupleOfAnExternalCall(uint256 _a) internal view returns (uint256 x) {
    (x, ) = this.pair(_a);
  }

  function tupleAssignmentToStorage(uint256 _a) internal returns (uint256) {
    ($total, ) = (_a, _a);
    return _a;
  }

  function exponentiation(uint256 _a) internal pure returns (uint256) {
    return _a ** 2;
  }

  function assembly_(uint256 _a) internal pure returns (uint256) {
    uint256 r = _a;
    assembly {
      r := add(r, 1)
    }
    return r;
  }

  function unmappedRead() internal view returns (uint256) {
    return block.timestamp;
  }

  function modulo(uint256 _a) internal pure returns (uint256) {
    return _a % 3;
  }

  function narrowArithmetic(uint128 _a) internal pure returns (uint256) {
    return _a + _a;
  }

  function mathMax(uint256 _a, uint256 _b) internal pure returns (uint256) {
    return Math.max(_a, _b);
  }

  function recursion(uint256 _a) internal pure returns (uint256) {
    if (_a == 0) {
      return 0;
    }
    return recursion(_a - 1);
  }

  function assignment(uint256 _a) internal pure returns (uint256) {
    uint256 x = _a;
    _a = x + 1;
    return x;
  }

  function requireCall(uint256 _a) internal pure returns (uint256) {
    require(_a > 0, string(abi.encodePacked(_a)));
    return _a;
  }

  function structParameterOfACallee(uint256 _a) internal pure returns (uint256) {
    return _field(Pair({a: _a, b: _a}));
  }

  function readsTotal() internal view returns (uint256) {
    return $total;
  }

  struct Pair {
    uint256 a;
    uint256 b;
  }

  enum Side {
    Left,
    Right
  }

  function _field(Pair memory _pair) internal pure returns (uint256) {
    return _pair.a;
  }

  function moduloAssignment(uint256 _a) internal pure returns (uint256) {
    uint256 x = _a;
    x %= 3;
    return x;
  }

  function enumCast(Side _side) internal pure returns (uint256) {
    return uint8(_side);
  }

  function enumOrder(Side _side) internal pure returns (bool) {
    return _side < Side.Right;
  }

  function addressLocalWithoutValue(address _a) internal pure returns (bool) {
    address x;
    return x == _a;
  }

  function shadowing(uint256 _a, uint256 _b) internal pure returns (uint256) {
    return _shadowed(_a, _b);
  }

  function _shadowed(uint256 _x, uint256 _y) internal pure returns (uint256) {
    if (_y == 1) {
      uint256 _x = 1;
      _y = _x;
    }
    return _x;
  }

  function inputsName(uint256 _a) internal pure returns (uint256) {
    return _named(_a);
  }

  function _named(uint256 i) internal pure returns (uint256) {
    return i;
  }

  function noInputs() internal pure returns (uint256) {
    return 1;
  }

  function constantNamedI(uint256 _a) internal pure returns (uint256) {
    return _a + Named.i;
  }

  function constantNamedAsALocal(uint256 _x) internal pure returns (uint256) {
    uint256 A = _x;
    return Named.A + A;
  }

  function pair(uint256 _a) external pure returns (uint256, uint256) {
    return (_a, _a);
  }
}

// Constants whose names collide with the names of the emitted Solidity.
library Named {
  uint256 internal constant i = 7;
  uint256 internal constant A = 7;
}
