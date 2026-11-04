// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {IGate, Keys} from "tests/fixtures/contracts/Keys.sol";

contract GateStub is IGate {
    uint256 internal quoted;

    function set(uint256 _quoted) external {
        quoted = _quoted;
    }

    function quote(address, uint256) external view returns (uint256) {
        return quoted;
    }
}

contract KeysSetup is Test {
    Keys internal keys;
    GateStub internal gate;

    function setUp() public {
        keys = new Keys();
        gate = new GateStub();
        keys.setGate(gate);
    }
}
