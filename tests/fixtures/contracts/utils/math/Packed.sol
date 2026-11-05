// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

type Packed is uint256;

function plus(Packed _a, Packed _b) pure returns (Packed) {
    return Packed.wrap(Packed.unwrap(_a) + Packed.unwrap(_b));
}

using {plus as +} for Packed global;
