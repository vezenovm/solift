// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";

// Two libraries that read one key text, at constants of one name and different values.
library SlotsA {
  uint256 internal constant K = 0;

  function slotA(Pointers.Book storage _book) internal view returns (uint256) {
    return _book.slots[K];
  }
}

library SlotsB {
  uint256 internal constant K = 1;

  function slotB(Pointers.Book storage _book) internal view returns (uint256) {
    return _book.slots[K];
  }
}

// A library on a struct of the base.
library ChoiceKeys {
  function keyOf(PointersBase.Choice storage _choice) internal view returns (uint256) {
    return _choice.key;
  }
}

// A base whose private storage variables have the names of the contract's.
contract PointersBase {
  struct Choice {
    uint256 key;
  }

  uint256 private level;
  Choice private choice;
  mapping(uint256 => uint256) internal picks;

  function _baseValue() internal view returns (uint256) {
    return level;
  }

  function _basePick() internal view returns (uint256) {
    Choice memory c = choice;
    return picks[c.key];
  }

  function _baseChoice() internal view returns (uint256) {
    return ChoiceKeys.keyOf(choice);
  }
}

// Storage pointers to structs in a mapping, read directly and through callees, as in a pool that refills an allowance,
// and the pointers that the extractor refuses.
contract Pointers is PointersBase {
  struct Config {
    uint64 activeUntil;
    uint128 perPeriod;
  }

  struct Spender {
    Config config;
    uint192 left;
    uint64 last;
  }

  struct Book {
    mapping(address => uint256) owed;
    mapping(uint256 => uint256) slots;
  }

  struct Env {
    address sender;
  }

  struct Pair {
    Choice left;
    Choice right;
  }

  mapping(address => Spender) internal $spenders;
  Spender internal $main;
  Spender[] internal $list;
  Book internal $book;
  mapping(string => uint256) internal $scores;
  Pair internal $pair;
  uint256 private level;
  Choice private choice;

  function available(address _who) external view returns (uint256) {
    Spender storage spender = $spenders[_who];
    if (!_isLive(spender)) {
      return 0;
    }
    (, uint256 pullable) = _limits(spender);
    return pullable;
  }

  function mine() external view returns (uint256) {
    Spender storage spender = $spenders[msg.sender];
    return spender.left;
  }

  function perPeriod(address _who) external view returns (uint256) {
    Config storage config = $spenders[_who].config;
    return config.perPeriod;
  }

  function touch(address _who, uint64 _now) external returns (uint256) {
    Spender storage spender = $spenders[_who];
    uint256 left = spender.left;
    spender.last = _now;
    return left;
  }

  function readAfterWrite(address _who, uint64 _now) external returns (uint256) {
    Spender storage spender = $spenders[_who];
    spender.last = _now;
    return spender.left;
  }

  function twoSpenders(address _a, address _b) external view returns (uint256) {
    return _left($spenders[_a]) + _left($spenders[_b]);
  }

  function atALocalKey(address _who) external view returns (uint256) {
    address who = _who;
    Spender storage spender = $spenders[who];
    return spender.left;
  }

  function wholeConfig(address _who) external view returns (uint256) {
    Spender storage spender = $spenders[_who];
    Config memory config = spender.config;
    return uint256(config.perPeriod) + spender.left;
  }

  function inAnArray(uint256 _n) external view returns (uint256) {
    Spender storage spender = $list[_n];
    return spender.left;
  }

  function owedTwice(address _a, address _b) external view returns (uint256) {
    Book storage book = $book;
    address who = _a;
    uint256 first = book.owed[who];
    who = _b;
    return first + book.owed[who];
  }

  function owedInBranches(bool _c, address _a, address _b) external view returns (uint256 total) {
    if (_c) {
      address who = _a;
      total = $book.owed[who];
    }
    address who = _b;
    return total + $book.owed[who];
  }

  function owedOnce(address _a) external view returns (uint256) {
    return _owed(_a);
  }

  function owedNested(address _a, address _b) external view returns (uint256) {
    return _outer(_a) + _outer(_b);
  }

  function owedReset(address _a) external view returns (uint256) {
    return _owedAtOne(_a);
  }

  function slotsIncremented() external view returns (uint256) {
    uint256 n = 0;
    uint256 first = $book.slots[n++];
    return first + $book.slots[n++];
  }

  function slotsAssigned(uint256 _n, uint256 _m) external view returns (uint256) {
    return $book.slots[_n = _m];
  }

  function mineTwice() external view returns (uint256) {
    Spender storage spender = $spenders[msg.sender];
    return uint256(spender.left) + $spenders[msg.sender].left;
  }

  function sameValue() external view returns (bool) {
    return level == _baseValue();
  }

  function valueIs(uint256 level) external view returns (bool) {
    return level == _baseValue();
  }

  function samePick() external view returns (bool) {
    Choice memory c = choice;
    return picks[c.key] == _basePick();
  }

  function sameChoice() external view returns (bool) {
    return ChoiceKeys.keyOf(choice) == _baseChoice();
  }

  function sameSpenders(Spender storage _a, Spender storage _b) internal view returns (bool) {
    return _leftOf(_a) == _rightOf(_b);
  }

  function sameScores() external view returns (bool) {
    return $scores["a b"] == $scores["ab"];
  }

  function sameSender(Env memory msg) public view returns (bool) {
    return msg.sender == _sender();
  }

  function samePair() external view returns (bool) {
    return _leftPick() == _rightPick();
  }

  function flipTwice() external view returns (bool) {
    Choice memory c = choice;
    return _flip(c) == _flip(c);
  }

  function gasTwice() external view returns (bool) {
    uint256 a = gasleft();
    uint256 b = gasleft();
    return a == b;
  }

  function _leftPick() internal view returns (uint256) {
    Choice storage p = $pair.left;
    return picks[p.key];
  }

  function _rightPick() internal view returns (uint256) {
    Choice storage p = $pair.right;
    return picks[p.key];
  }

  function _flip(Choice memory _c) internal pure returns (bool) {
    _c.key += 1;
    return _c.key > 1;
  }

  function _leftOf(Spender storage _p) internal view returns (uint256) {
    return _valueOf(_p);
  }

  function _rightOf(Spender storage _p) internal view returns (uint256) {
    return _valueOf(_p);
  }

  function _valueOf(Spender storage _p) internal view returns (uint256) {
    return _p.left;
  }

  function _sender() internal view returns (address) {
    return msg.sender;
  }

  function slotsOfTwoLibraries() external view returns (uint256) {
    return SlotsA.slotA($book) + SlotsB.slotB($book);
  }

  function _owed(address _who) internal view returns (uint256) {
    return $book.owed[_who];
  }

  function _outer(address _who) internal view returns (uint256) {
    return _owedThrough($book, _who);
  }

  function _owedThrough(Book storage _book, address _who) internal view returns (uint256) {
    return _book.owed[_who];
  }

  function _owedAtOne(address _who) internal view returns (uint256) {
    _who = address(1);
    return $book.owed[_who];
  }

  function _left(Spender storage _spender) internal view returns (uint256) {
    return _spender.left;
  }

  function _isLive(Spender storage _spender) internal view returns (bool) {
    return block.timestamp <= _spender.config.activeUntil;
  }

  function _limits(Spender storage _spender) internal view returns (uint256 refilled, uint256 pullable) {
    uint256 elapsed = block.timestamp > _spender.last ? block.timestamp - _spender.last : 0;
    refilled = Math.min(_spender.config.perPeriod, _spender.left + elapsed);
    pullable = Math.min(refilled, $main.left);
  }

  function _writeThrough(Spender storage _spender) internal returns (uint256) {
    _spender.last = 1;
    return 1;
  }
}
