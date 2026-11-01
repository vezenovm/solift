// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Arrays} from "@openzeppelin/contracts/utils/Arrays.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {IToken} from "./Quoter.sol";

// Internal calls of virtual functions. Each runs the override of the contract that the target names, while solc
// resolves the call in the contract that makes it, which can be a base of the target's contract.

// A base that declares a function without a body, which a derived contract implements.
abstract contract Pool {
  uint256 internal supply;

  function cash() internal view virtual returns (uint256);

  function rate() public view returns (uint256) {
    return supply == 0 ? 1e18 : (cash() * 1e18) / supply;
  }
}

contract TokenPool is Pool {
  IToken internal immutable TOKEN;

  constructor(IToken _token) {
    TOKEN = _token;
  }

  function cash() internal view override returns (uint256) {
    return TOKEN.balanceOf(address(this));
  }
}

// A base whose function a derived contract overrides.
contract Fees {
  function fee() internal pure virtual returns (uint256) {
    return 1;
  }

  function total(uint256 _a) public pure returns (uint256) {
    return _a + fee();
  }

  function weighed(uint256 _a, uint256 _b) public pure returns (uint256) {
    return weigh({_value: _a, _weight: _b});
  }

  function weigh(uint256 _value, uint256 _weight) internal pure virtual returns (uint256) {
    return _value + _weight;
  }

  function weighedOnce() internal pure returns (uint256) {
    return weigh({_value: 1, _weight: 2});
  }
}

contract DerivedFees is Fees {
  function fee() internal pure override returns (uint256) {
    return 2;
  }

  // The override names its parameters the other way round: a call names the base's parameters, and the override takes
  // the arguments in their order.
  function weigh(uint256 _weight, uint256 _value) internal pure override returns (uint256) {
    return 10 * _weight + _value;
  }

  // weighedOnce passes (1, 2), and the second call (2, 1), as each binds the names of the function that solc names.
  function weighedTwice(uint256 _a) public pure returns (uint256) {
    return _a + weighedOnce() + weigh({_value: 1, _weight: 2});
  }
}

// A diamond. In the linearization of Tiers (Tiers, HighTier, LowTier, Tier), super in HighTier runs LowTier's
// override, while HighTier's own bases hold only Tier.
contract Tier {
  function rate() internal pure virtual returns (uint256) {
    return 1;
  }
}

contract LowTier is Tier {
  function rate() internal pure virtual override returns (uint256) {
    return 2;
  }

  function lower() internal pure returns (uint256) {
    return super.rate();
  }
}

contract HighTier is Tier {
  function rate() internal pure virtual override returns (uint256) {
    return 3;
  }

  function next(uint256 _a) public pure returns (uint256) {
    return _a + super.rate();
  }

  function named(uint256 _a) public pure returns (uint256) {
    return _a + Tier.rate();
  }

  function own(uint256 _a) public pure returns (uint256) {
    return _a + rate();
  }

  function higher() internal pure returns (uint256) {
    return super.rate();
  }
}

contract Tiers is LowTier, HighTier {
  function rate() internal pure override(LowTier, HighTier) returns (uint256) {
    return 4;
  }

  // super.rate() is Tier's rate in lower and LowTier's in higher.
  function both(uint256 _a) public pure returns (uint256) {
    return _a + lower() + higher();
  }
}

// Two unrelated bases of one function. In the linearization of Shares (Shares, Third, Second, First), super in Third
// runs Second's share, which does not override First's, the one that solc names.
contract First {
  function share() internal pure virtual returns (uint256) {
    return 2;
  }
}

contract Second {
  function share() internal pure virtual returns (uint256) {
    return 3;
  }
}

contract Third is First {
  function shared(uint256 _a) public pure returns (uint256) {
    return _a + super.share();
  }
}

contract Shares is First, Second, Third {
  function share() internal pure override(First, Second) returns (uint256) {
    return 4;
  }
}

// Overrides that call gasleft, where the base does not: in the body, in a modifier, and through this.
contract Meter {
  function left() internal view virtual returns (uint256) {
    return 0;
  }

  function spent(uint256 _a) public view returns (uint256) {
    return _a + left();
  }

  function exposed() external view virtual returns (uint256) {
    return 0;
  }

  function viaThis(uint256 _a) public view returns (uint256) {
    return _a + this.exposed();
  }
}

contract GasMeter is Meter {
  function left() internal view override returns (uint256) {
    return gasleft();
  }

  function exposed() external view override returns (uint256) {
    return gasleft();
  }
}

contract GatedMeter is Meter {
  modifier gate() {
    if (gasleft() > 100000) _;
  }

  function left() internal view override gate returns (uint256) {
    return 1;
  }
}

// A call on another instance runs that instance's code, here Gauge's reading, which calls gasleft.
contract Gauge {
  function reading() internal view virtual returns (uint256) {
    return gasleft();
  }

  function get() public view returns (uint256) {
    return reading();
  }
}

contract FixedGauge is Gauge {
  function reading() internal pure override returns (uint256) {
    return 0;
  }

  function twice(Gauge _other) public view returns (uint256) {
    return _other.get() + _other.get();
  }
}

// An override of a modifier can change state in a view function, whose mutability solc checks against the modifier
// that the function names.
contract Counted {
  uint256 internal count;

  modifier counts() virtual {
    _;
  }

  function current() internal view counts returns (uint256) {
    return count;
  }

  function delta(uint256 _a) public returns (uint256) {
    return _a + current() - current();
  }
}

contract CountedTwice is Counted {
  modifier counts() override {
    count = count + 1;
    _;
  }
}

// A modifier named through its contract is not virtual: Unthrottled's level runs Throttled's throttle.
contract Throttled {
  modifier throttle() virtual {
    if (gasleft() > 100000) _;
  }

  function level() internal view Throttled.throttle returns (uint256) {
    return 1;
  }

  function levels(uint256 _a) public view returns (uint256) {
    return _a + level() + level();
  }
}

contract Unthrottled is Throttled {
  modifier throttle() override {
    _;
  }
}

// super in Probe skips Silent's sense, which has no body, and runs Sensor's, which calls gasleft.
contract Sensor {
  function sense() internal view virtual returns (uint256) {
    return gasleft();
  }
}

abstract contract Silent {
  function sense() internal view virtual returns (uint256);
}

contract Probe is Sensor {
  function delta(uint256 _a) public view returns (uint256) {
    return _a + super.sense() - super.sense();
  }
}

contract Probes is Sensor, Silent, Probe {
  function sense() internal pure override(Sensor, Silent) returns (uint256) {
    return 0;
  }
}

// A call through a function pointer, which the scan of a read map call does not follow.
contract Pointed {
  uint256 internal count;
  function() internal view returns (uint256) internal pointer;

  constructor() {
    pointer = counted;
  }

  function counted() internal view returns (uint256) {
    return count;
  }

  function indirect() internal view returns (uint256) {
    return pointer();
  }

  function twice(uint256 _a) public view returns (uint256) {
    return _a + indirect() - indirect();
  }
}

// A receiver typed GasReader runs GasReader's reading, which calls gasleft, where Reader's does not.
contract Reader {
  function reading() internal view virtual returns (uint256) {
    return 0;
  }

  function read() public view returns (uint256) {
    return reading();
  }
}

contract GasReader is Reader {
  function reading() internal view override returns (uint256) {
    return gasleft();
  }
}

contract Sampler {
  function delta(GasReader _other, uint256 _a) public view returns (uint256) {
    return _a + _other.read() - _other.read();
  }
}

// An override that reads the gas in assembly, which the scan, as on main, does not read.
contract Assembled {
  function measure() internal view virtual returns (uint256) {
    return 0;
  }

  function delta(uint256 _a) public view returns (uint256) {
    return _a + measure() - measure();
  }
}

contract GasAssembled is Assembled {
  function measure() internal view override returns (uint256 r) {
    assembly {
      r := gas()
    }
  }
}

// A public state variable that overrides an external function: its getter runs, and the function's modifier does not.
contract Rated {
  modifier gate() virtual {
    _;
  }

  function fixedRate() external view virtual gate returns (uint256) {
    return 0;
  }

  function total(uint256 _a) public view returns (uint256) {
    return _a + this.fixedRate();
  }
}

contract FixedRated is Rated {
  uint256 public override fixedRate = 7;

  modifier gate() override {
    if (gasleft() > 100000) _;
  }
}

// A pointer called with call options, a function called in parentheses, a getter that gives a pointer, a selector,
// which the scan follows as the function that it names, and a low-level call of a function named as a value.
contract Optioned {
  function() external view returns (uint256) public pointer;

  constructor() {
    pointer = this.remaining;
  }

  function remaining() external view virtual returns (uint256) {
    return 0;
  }

  function optioned() internal view returns (uint256) {
    return pointer{gas: 100000}();
  }

  function plain() internal pure returns (uint256) {
    return 7;
  }

  function wrapped() internal pure returns (uint256) {
    return (plain)();
  }

  function selector() internal pure returns (uint256) {
    return uint32(this.remaining.selector);
  }

  function gotten() internal view returns (uint256) {
    return uint32(this.pointer().selector);
  }

  function encoded() internal view returns (uint256) {
    (bool ok, bytes memory data) = address(this).staticcall(abi.encodeCall(this.remaining, ()));
    require(ok);
    return abi.decode(data, (uint256));
  }

  function encodedByName() internal view returns (uint256) {
    (bool ok, bytes memory data) = address(this).staticcall(abi.encodeCall(Optioned.remaining, ()));
    require(ok);
    return abi.decode(data, (uint256));
  }

  function parenthesized() internal view returns (uint256) {
    return (this).remaining();
  }

  function viaEncodedByName(uint256 _a) public view returns (uint256) {
    return _a + encodedByName();
  }

  function viaParenthesized(uint256 _a) public view returns (uint256) {
    return _a + parenthesized();
  }

  function viaGetter(uint256 _a) public view returns (uint256) {
    return _a + gotten();
  }

  function viaEncoded(uint256 _a) public view returns (uint256) {
    return _a + encoded();
  }

  function viaPointer(uint256 _a) public view returns (uint256) {
    return _a + optioned();
  }

  function viaParentheses(uint256 _a) public pure returns (uint256) {
    return _a + wrapped();
  }

  function viaSelector(uint256 _a) public pure returns (uint256) {
    return _a + selector();
  }
}

contract GasOptioned is Optioned {
  function remaining() external view override returns (uint256) {
    return gasleft();
  }
}

// Cuts of code that forwards gas to a precompile, and of OpenZeppelin's sort with the comparator that it passes.
contract Medians {
  uint256[] internal prices;

  function median() internal view returns (uint256) {
    uint256[] memory sorted = prices;
    Arrays.sort(sorted);
    return sorted[sorted.length / 2];
  }

  function quote(uint256 _amount) public view returns (uint256) {
    return _amount * median();
  }

  function power(uint256 _b, uint256 _e, uint256 _m) public view returns (uint256) {
    return Math.modExp(_b, _e, _m);
  }

  function precompiled(uint256 _x) internal view returns (uint256 result) {
    assembly {
      mstore(0, _x)
      if iszero(staticcall(sub(gas(), 2000), 0x02, 0, 0x20, 0, 0x20)) {
        revert(0, 0)
      }
      result := mload(0)
    }
  }

  function hashed(uint256 _x) public view returns (uint256) {
    return precompiled(_x);
  }
}

// A function named through its contract as a value runs as named: through an internal pointer, and on a receiver
// that abi.encodeCall does not tie to the target. FlatPinned's g and h are not what runs.
contract Pinned {
  function g() public view virtual returns (uint256) {
    return gasleft();
  }

  function h() external view virtual returns (uint256) {
    return gasleft();
  }

  function pinned() internal view returns (uint256) {
    function() internal view returns (uint256) p = Pinned.g;
    return p();
  }

  function sent(Pinned _other) internal view returns (uint256) {
    (bool ok, bytes memory data) = address(_other).staticcall(abi.encodeCall(Pinned.h, ()));
    require(ok);
    return abi.decode(data, (uint256));
  }

  function viaPinned(uint256 _a) public view returns (uint256) {
    return _a + pinned();
  }

  function viaSent(Pinned _other, uint256 _a) public view returns (uint256) {
    return _a + sent(_other);
  }
}

contract FlatPinned is Pinned {
  function g() public pure override returns (uint256) {
    return 7;
  }

  function h() external pure override returns (uint256) {
    return 7;
  }
}

// abi.encodeCall takes the selector of _schema.g, and the call goes to a receiver of another type: Schema's h runs.
contract Schema {
  function h() internal view virtual returns (uint256) {
    return gasleft();
  }

  function g() public view returns (uint256) {
    return h();
  }
}

contract FlatSchema is Schema {
  function h() internal pure override returns (uint256) {
    return 7;
  }
}

contract Prober {
  function read(FlatSchema _schema, Schema _receiver) internal view returns (uint256) {
    (bool ok, bytes memory data) = address(_receiver).staticcall(abi.encodeCall(_schema.g, ()));
    require(ok);
    return abi.decode(data, (uint256));
  }

  function delta(FlatSchema _schema, Schema _receiver, uint256 _a) public view returns (uint256) {
    return _a + read(_schema, _receiver);
  }
}
