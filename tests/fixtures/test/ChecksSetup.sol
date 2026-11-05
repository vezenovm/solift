// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Checks} from "tests/fixtures/contracts/Checks.sol";

contract ChecksHarness is Checks {
    function setOwner(address _owner) external {
        owner = _owner;
    }

    function setTotal(uint256 _total) external {
        total = _total;
    }
}

contract ChecksSetup is Test {
    ChecksHarness internal checks;

    function setUp() public {
        checks = new ChecksHarness();
    }
}
