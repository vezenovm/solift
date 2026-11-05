// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Math as M} from "@openzeppelin/contracts/utils/math/Math.sol";
import {Mode as Flag} from "./Modes.sol";

library Named {
  function i(uint256 _x) internal pure returns (uint256) {
    return _x + 1;
  }
}

library Doubling {
  function twice(uint256 _x) internal pure returns (uint256) {
    return _x * 2;
  }
}

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

  // A read map key on an assigned parameter at two places, where the parameter has two values.
  function limitsAround(uint256 _id) public view returns (uint256) {
    uint256 first = $limits[_id];
    _id = _id + 1;
    return first + $limits[_id];
  }

  modifier atMost(uint256 _x) {
    require(_x <= 100);
    _;
  }

  // A modifier whose argument is a parameter that the body assigns: the argument is its value at the call.
  function raisedWithin(uint256 _a) public pure atMost(_a) returns (uint256) {
    _a += 1;
    return _a;
  }

  // A parameter local, and a local, of the name of a function of the model, which they would shadow there.
  function shadowsCallee(uint256 twice) public pure returns (uint256) {
    twice += 1;
    return Doubling.twice(twice);
  }

  function localShadowsCallee(uint256 _a) public pure returns (uint256) {
    uint256 twice = _a + 1;
    return Doubling.twice(twice);
  }

  // A local of the name of a called function where it is not in scope at the call: in the declaration's own value, and
  // in another branch.
  function initializedFromCallee(uint256 _a) public pure returns (uint256) {
    uint256 twice = Doubling.twice(_a);
    return twice;
  }

  function calleeBesideBranchLocal(uint256 _a, bool _small) public pure returns (uint256) {
    if (_small) {
      uint256 twice = _a;
      return twice;
    }
    return Doubling.twice(_a);
  }

  // A called function that the inputs struct i of the emitted Solidity would shadow.
  function callsI(uint256 _a) public pure returns (uint256) {
    return Named.i(_a);
  }

  // The library of an operator and the enum of a member, which the emitted Solidity names as Math and Mode, and a
  // parameter of that name: an input, which the model reads as i.Math, or a local where the body assigns it.
  function mathInput(uint256 Math) public pure returns (uint256) {
    return M.min(Math, 10);
  }

  function mathShadowed(uint256 Math) public pure returns (uint256) {
    Math += 1;
    return M.min(Math, 10);
  }

  // A function of the model of the name of an operator's library, which it would shadow in the emitted library.
  function Math(uint256 _a) public pure returns (uint256) {
    return M.min(_a, 10);
  }

  function flagged(Flag _f, uint256 Mode) public pure returns (uint256) {
    Mode += 1;
    return _f == Flag.On ? Mode : 0;
  }

  // The enum's name as the type of a local, which a parameter's local of that name shadows.
  function flagLocal(uint256 Mode, Flag _f) public pure returns (uint256) {
    Mode += 1;
    Flag local = _f;
    return local == Flag.Off ? 0 : Mode;
  }

  // The emitted Solidity reverts as revert(), which a parameter's local named revert shadows, and an input does not.
  function revertShadowed(uint256 revert) public pure returns (uint256) {
    revert += 1;
    require(revert < 10);
    return revert;
  }

  function revertInput(uint256 revert) public pure returns (uint256) {
    require(revert < 10);
    return revert;
  }
}
