// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Bits} from "tests/fixtures/contracts/Bits.sol";

contract BitsSetup is Test {
    Bits internal bits;

    function setUp() public {
        bits = new Bits();
    }
}
