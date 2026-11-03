// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Payable} from "tests/fixtures/contracts/Payable.sol";

contract PayableSetup is Test {
    Payable internal payer;

    function setUp() public {
        payer = new Payable();
    }
}
