// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.27;

interface ISwapper {
  function fee() external view returns (uint256);

  function buy(address _recipient, uint256 _amount) external;
}

// Storage writes and external call arguments that a model has as outputs, narrowing conversions, and the outputs that
// the extractor refuses.
contract Effects {
  using Approvals for ISwapper;

  struct Allowance {
    uint192 left;
    uint64 last;
  }

  event Pulled(address who, uint256 amount);

  ISwapper internal immutable SWAPPER;
  mapping(address => Allowance) internal $allowances;
  uint256 internal $total;
  uint256 internal $count;

  constructor(ISwapper _swapper) {
    SWAPPER = _swapper;
  }

  function buy(uint256 _amountIn) external {
    uint256 amount = _amountIn / (1e18 + SWAPPER.fee());
    SWAPPER.buy(msg.sender, amount);
  }

  function pull(uint256 _amount, uint256 _refill) external returns (uint256) {
    Allowance storage allowance = $allowances[msg.sender];
    uint256 left = uint256(allowance.left) + _refill;
    require(_amount <= left, "above the allowance");
    allowance.left = uint192(left - _amount);
    allowance.last = uint64(block.timestamp);
    emit Pulled(msg.sender, _amount);
    return _amount;
  }

  function add(uint256 _x, uint256 _y) external returns (bool) {
    if (_x == 0) {
      return false;
    }
    $total += _x;
    $total = $total - _y;
    return true;
  }

  function truncate(uint256 _a) external pure returns (uint192) {
    return uint192(_a);
  }

  function writeOnSomePaths(uint256 _x) external {
    if (_x > 0) {
      $count = _x;
    }
  }

  function skip(bool _skip, uint256 _x) external {
    if (_skip) {
      return;
    }
    $count = _x;
    SWAPPER.buy(msg.sender, _x);
  }

  function replace(uint256 _x) external returns (uint256) {
    Allowance memory saved = $allowances[msg.sender];
    $allowances[msg.sender].left = uint192(_x);
    return saved.left;
  }

  function otherWriteToAnOutput(address _a, uint256 _x) external {
    $allowances[msg.sender].left = uint192(_x);
    $allowances[_a].left = 1;
  }

  function readAfterACall() external returns (uint256) {
    SWAPPER.buy(msg.sender, 1);
    return $count;
  }

  function nothing(uint256 _x) external {
    emit Pulled(msg.sender, _x);
  }

  function outputAtALocalKey(address _a, uint256 _x) external {
    address who = _a;
    $allowances[who].left = uint192(_x);
  }

  function swapperAfterACall() external returns (address) {
    SWAPPER.buy(msg.sender, 1);
    return address(SWAPPER);
  }

  function boundCallStatement(uint256 _x) external returns (uint256) {
    SWAPPER.buyAll(_x);
    return _x;
  }
}

library Approvals {
  function buyAll(ISwapper _swapper, uint256 _x) internal {
    _swapper.buy(address(0), _x);
  }
}
