// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Test} from "forge-std/Test.sol";
import {Structs} from "tests/fixtures/contracts/Structs.sol";

contract StructsSetup is Test {
    Structs internal structs;

    function setUp() public {
        structs = new Structs();
    }
}
