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

/// @notice Atomic two-or-more-leg V2 arbitrage executor.
/// @dev Operator must explicitly allow both callers and routers. Every execution pulls
///      wrapped base from the caller and returns capital + profit to the same caller.
///      A failed leg, stale deadline or insufficient final profit reverts atomically.
contract AtomicV2ArbExecutor {
    address public immutable wrappedBase;
    address public owner;
    bool private locked;

    mapping(address => bool) public allowedRouter;
    mapping(address => bool) public allowedCaller;

    event OwnerChanged(address indexed oldOwner, address indexed newOwner);
    event RouterAllowed(address indexed router, bool allowed);
    event CallerAllowed(address indexed caller, bool allowed);
    event ArbitrageExecuted(
        address indexed caller,
        uint256 amountIn,
        uint256 amountReturned,
        uint256 profit
    );

    modifier onlyOwner() {
        require(msg.sender == owner, "OWNER");
        _;
    }

    modifier onlyCaller() {
        require(allowedCaller[msg.sender], "CALLER");
        _;
    }

    modifier nonReentrant() {
        require(!locked, "REENTRANCY");
        locked = true;
        _;
        locked = false;
    }

    constructor(address _wrappedBase) {
        require(_wrappedBase != address(0), "ZERO_BASE");
        wrappedBase = _wrappedBase;
        owner = msg.sender;
        allowedCaller[msg.sender] = true;
        emit CallerAllowed(msg.sender, true);
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

    function setCaller(address caller, bool allowed) external onlyOwner {
        require(caller != address(0), "ZERO_CALLER");
        allowedCaller[caller] = allowed;
        emit CallerAllowed(caller, allowed);
    }

    function _safeTransferFrom(address token, address from, address to, uint256 amount) private {
        (bool ok, bytes memory data) =
            token.call(abi.encodeWithSelector(IERC20Minimal.transferFrom.selector, from, to, amount));
        require(ok && (data.length == 0 || abi.decode(data, (bool))), "TRANSFER_FROM");
    }

    function _safeTransfer(address token, address to, uint256 amount) private {
        (bool ok, bytes memory data) =
            token.call(abi.encodeWithSelector(IERC20Minimal.transfer.selector, to, amount));
        require(ok && (data.length == 0 || abi.decode(data, (bool))), "TRANSFER");
    }

    function _forceApprove(address token, address spender, uint256 amount) private {
        (bool ok0, bytes memory d0) =
            token.call(abi.encodeWithSelector(IERC20Minimal.approve.selector, spender, 0));
        require(ok0 && (d0.length == 0 || abi.decode(d0, (bool))), "APPROVE0");

        (bool ok, bytes memory data) =
            token.call(abi.encodeWithSelector(IERC20Minimal.approve.selector, spender, amount));
        require(ok && (data.length == 0 || abi.decode(data, (bool))), "APPROVE");
    }

    /// @notice Execute router legs atomically and return wrapped-base capital + profit to caller.
    /// @param routers Whitelisted V2 routers, one per leg.
    /// @param paths Token path for each router leg. Adjacent legs must be continuous.
    /// @param amountIn Wrapped-base capital pulled from msg.sender.
    /// @param minProfit Minimum wrapped-base profit before caller gas cost.
    /// @param deadline Absolute timestamp; stale transactions revert.
    function execute(
        address[] calldata routers,
        address[][] calldata paths,
        uint256 amountIn,
        uint256 minProfit,
        uint256 deadline
    ) external onlyCaller nonReentrant returns (uint256 amountReturned) {
        require(block.timestamp <= deadline, "DEADLINE");
        require(routers.length >= 2 && routers.length == paths.length, "LEGS");
        require(amountIn > 0, "AMOUNT");
        require(paths[0].length >= 2 && paths[0][0] == wrappedBase, "START");
        require(
            paths[paths.length - 1].length >= 2 &&
            paths[paths.length - 1][paths[paths.length - 1].length - 1] == wrappedBase,
            "END"
        );

        uint256 baseBefore = IERC20Minimal(wrappedBase).balanceOf(address(this));
        _safeTransferFrom(wrappedBase, msg.sender, address(this), amountIn);

        uint256 currentAmount = amountIn;
        address currentToken = wrappedBase;

        for (uint256 i = 0; i < routers.length; i++) {
            require(allowedRouter[routers[i]], "ROUTER");
            require(paths[i].length >= 2 && paths[i][0] == currentToken, "PATH_CONTINUITY");

            _forceApprove(currentToken, routers[i], currentAmount);
            uint256[] memory amounts = IV2RouterMinimal(routers[i]).swapExactTokensForTokens(
                currentAmount,
                0,
                paths[i],
                address(this),
                deadline
            );

            currentAmount = amounts[amounts.length - 1];
            currentToken = paths[i][paths[i].length - 1];
        }

        require(currentToken == wrappedBase, "NOT_BASE");

        uint256 baseAfter = IERC20Minimal(wrappedBase).balanceOf(address(this));
        require(baseAfter >= baseBefore + amountIn + minProfit, "MIN_PROFIT");

        amountReturned = baseAfter - baseBefore;
        _safeTransfer(wrappedBase, msg.sender, amountReturned);

        emit ArbitrageExecuted(msg.sender, amountIn, amountReturned, amountReturned - amountIn);
    }

    /// @notice Rescue tokens accidentally sent directly to the contract.
    /// @dev Cannot withdraw capital currently being executed because nonReentrant execution is atomic.
    function rescue(address token, address to, uint256 amount) external onlyOwner nonReentrant {
        require(token != address(0) && to != address(0), "ZERO");
        _safeTransfer(token, to, amount);
    }
}
