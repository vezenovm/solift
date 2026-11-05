// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Wide} from "tests/fixtures/contracts/Wide.sol";

contract WideSetup is Test {
    Wide internal wide;

    function setUp() public {
        wide = new Wide();
    }
}
