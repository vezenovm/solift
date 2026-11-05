// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Gated} from "tests/fixtures/contracts/Gated.sol";

contract GatedSetup is Test {
    Gated internal gated;

    function setUp() public {
        gated = new Gated();
    }
}
