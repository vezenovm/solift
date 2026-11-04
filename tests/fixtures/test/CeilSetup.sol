// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Ceil} from "tests/fixtures/contracts/Ceil.sol";

contract CeilSetup is Test {
    Ceil internal ceil;

    function setUp() public {
        ceil = new Ceil();
    }
}
