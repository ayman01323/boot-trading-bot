// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

interface IERC20Minimal {
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external returns (bool);
    function transferFrom(address from, address to, uint256 amount) external returns (bool);
    function approve(address spender, uint256 amount) external returns (bool);
}

interface IV2RouterMinimal {
    function swapExactTokensForTokens(
        uint256 amountIn,
        uint256 amountOutMin,
        address[] calldata path,
        address to,
        uint256 deadline
    ) external returns (uint256[] memory amounts);
}

/// @notice Reference atomic executor for future cross-DEX V2 activation.
/// @dev It is intentionally NOT deployed by the Python installer. Cross-DEX rows remain
///      shadow-only until an operator deploys/audits this contract and explicitly configures it.
contract AtomicV2ArbExecutor {
    address public immutable wrappedBase;
    address public owner;
    bool private locked;
    mapping(address => bool) public allowedRouter;

    event OwnerChanged(address indexed oldOwner, address indexed newOwner);
    event RouterAllowed(address indexed router, bool allowed);
    event ArbitrageExecuted(uint256 amountIn, uint256 amountReturned, uint256 profit);

    modifier onlyOwner() { require(msg.sender == owner, "OWNER"); _; }
    modifier nonReentrant() { require(!locked, "REENTRANCY"); locked = true; _; locked = false; }

    constructor(address _wrappedBase) {
        require(_wrappedBase != address(0), "ZERO_BASE");
        wrappedBase = _wrappedBase;
        owner = msg.sender;
    }

    function setOwner(address next) external onlyOwner {
        require(next != address(0), "ZERO_OWNER");
        emit OwnerChanged(owner, next);
        owner = next;
    }

    function setRouter(address router, bool allowed) external onlyOwner {
        require(router != address(0), "ZERO_ROUTER");
        allowedRouter[router] = allowed;
        emit RouterAllowed(router, allowed);
    }

    function _safeTransferFrom(address token, address from, address to, uint256 amount) private {
        (bool ok, bytes memory data) = token.call(abi.encodeWithSelector(IERC20Minimal.transferFrom.selector, from, to, amount));
        require(ok && (data.length == 0 || abi.decode(data, (bool))), "TRANSFER_FROM");
    }

    function _safeTransfer(address token, address to, uint256 amount) private {
        (bool ok, bytes memory data) = token.call(abi.encodeWithSelector(IERC20Minimal.transfer.selector, to, amount));
        require(ok && (data.length == 0 || abi.decode(data, (bool))), "TRANSFER");
    }

    function _forceApprove(address token, address spender, uint256 amount) private {
        (bool ok0, bytes memory d0) = token.call(abi.encodeWithSelector(IERC20Minimal.approve.selector, spender, 0));
        require(ok0 && (d0.length == 0 || abi.decode(d0, (bool))), "APPROVE0");
        (bool ok, bytes memory data) = token.call(abi.encodeWithSelector(IERC20Minimal.approve.selector, spender, amount));
        require(ok && (data.length == 0 || abi.decode(data, (bool))), "APPROVE");
    }

    /// @notice Execute all router legs atomically. Any failed leg or insufficient final profit reverts everything.
    function execute(
        address[] calldata routers,
        address[][] calldata paths,
        uint256 amountIn,
        uint256 minProfit,
        uint256 deadline
    ) external onlyOwner nonReentrant returns (uint256 amountReturned) {
        require(routers.length >= 2 && routers.length == paths.length, "LEGS");
        require(amountIn > 0, "AMOUNT");
        require(paths[0].length >= 2 && paths[0][0] == wrappedBase, "START");
        require(paths[paths.length - 1][paths[paths.length - 1].length - 1] == wrappedBase, "END");

        uint256 baseBefore = IERC20Minimal(wrappedBase).balanceOf(address(this));
        _safeTransferFrom(wrappedBase, msg.sender, address(this), amountIn);
        uint256 currentAmount = amountIn;
        address currentToken = wrappedBase;

        for (uint256 i = 0; i < routers.length; i++) {
            require(allowedRouter[routers[i]], "ROUTER");
            require(paths[i].length >= 2 && paths[i][0] == currentToken, "PATH_CONTINUITY");
            _forceApprove(currentToken, routers[i], currentAmount);
            uint256[] memory amounts = IV2RouterMinimal(routers[i]).swapExactTokensForTokens(
                currentAmount, 0, paths[i], address(this), deadline
            );
            currentAmount = amounts[amounts.length - 1];
            currentToken = paths[i][paths[i].length - 1];
        }

        require(currentToken == wrappedBase, "NOT_BASE");
        uint256 baseAfter = IERC20Minimal(wrappedBase).balanceOf(address(this));
        require(baseAfter >= baseBefore + amountIn + minProfit, "MIN_PROFIT");
        amountReturned = baseAfter - baseBefore;
        _safeTransfer(wrappedBase, msg.sender, amountReturned);
        emit ArbitrageExecuted(amountIn, amountReturned, amountReturned - amountIn);
    }
}
