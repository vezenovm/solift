// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Kept} from "tests/fixtures/contracts/Kept.sol";

contract KeptSetup is Test {
    Kept internal kept;

    function setUp() public {
        kept = new Kept();
    }
}
