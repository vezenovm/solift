// SPDX-License-Identifier: Apache-2.0
pragma solidity >=0.8.27;

import {Test} from "forge-std/Test.sol";
import {Caps} from "@core/Caps.sol";

// Caps is abstract, so the link deploys this subclass with the immutables of the check and sets the two storage
// variables that getCurrentAvailable reads.
contract CapsLinkHarness is Caps {
  constructor(uint256 _rate, uint256 _globalLimit) Caps(0, _rate, _globalLimit) {}

  function set(uint256 _lastUpdatedTime, uint256 _cachedAvailable) external {
    $lastUpdatedTime = _lastUpdatedTime;
    $cachedAvailable = _cachedAvailable;
  }
}

contract CapsSetup is Test {
  CapsLinkHarness internal caps;

  function _deploy(uint256 _rate, uint256 _globalLimit, uint256 _lastUpdatedTime, uint256 _cachedAvailable) internal {
    caps = new CapsLinkHarness(_rate, _globalLimit);
    caps.set(_lastUpdatedTime, _cachedAvailable);
  }
}
