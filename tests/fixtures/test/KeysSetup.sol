// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {IGate, Keys} from "tests/fixtures/contracts/Keys.sol";

contract GateStub is IGate {
    uint256 internal quoted;
    uint256 internal answer;
    uint256 public calls;

    function set(uint256 _quoted) external {
        quoted = _quoted;
    }

    function setAnswer(uint256 _answer) external {
        answer = _answer;
    }

    function quote(address, uint256) external view returns (uint256) {
        return quoted;
    }

    function allowed(address, uint256) external returns (uint256) {
        calls += 1;
        return answer;
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
