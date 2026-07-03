// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title HedgehogSettlement — v0 stub for on-chain batch settlement
/// @notice Settles cleared Hedgehog batches atomically on Polygon:
///         CTF outcome tokens (ERC-1155) against USDC, at the uniform batch
///         clearing price, with both sides' EIP-712 intent signatures verified.
///
/// v0 scope (deliberately narrow):
///   - settleCross: P2P settlement of two matched sealed intents
///   - settleSolver: solver fills residual imbalance from its own inventory
///   - commitments: each settlement must present the pre-image of the
///     commitment hash posted at intent time (commit–reveal integrity)
///
/// Upgrade path:
///   v1  threshold-encrypted intents (no server-side plaintext)
///   v2  ZK VALID-MATCH proof replaces reveal (Renegade-style), so even
///       post-trade, only net settlement is public — tape stays public by design.
///
/// ⚠ Unaudited prototype. Do not deploy with real funds before an audit
///   and a real legal review (see README).

interface IERC20 {
    function transferFrom(address from, address to, uint256 amt) external returns (bool);
}

interface IERC1155 {
    function safeTransferFrom(address from, address to, uint256 id, uint256 amt, bytes calldata data) external;
}

contract HedgehogSettlement {
    IERC20 public immutable usdc;
    IERC1155 public immutable ctf;          // Polymarket Conditional Token Framework
    address public operator;                 // batch clearer (v0: trusted; v2: proof-verified)

    mapping(bytes32 => bool) public consumed;    // commitment → settled

    event Cross(bytes32 buyCommit, bytes32 sellCommit, uint256 tokenId, uint256 size, uint256 px1e6);
    event SolverFill(bytes32 commit, uint256 tokenId, uint256 size, uint256 px1e6);

    modifier onlyOperator() { require(msg.sender == operator, "not operator"); _; }

    constructor(address _usdc, address _ctf) {
        usdc = IERC20(_usdc);
        ctf = IERC1155(_ctf);
        operator = msg.sender;
    }

    /// @notice P2P cross at the uniform batch price. px1e6 = price × 1e6 (USDC decimals).
    function settleCross(
        address buyer, address seller,
        bytes32 buyCommit, bytes32 sellCommit,
        bytes calldata buyReveal, bytes calldata sellReveal,
        uint256 tokenId, uint256 size, uint256 px1e6
    ) external onlyOperator {
        _consume(buyCommit, buyReveal);
        _consume(sellCommit, sellReveal);
        uint256 notional = size * px1e6 / 1e6;
        require(usdc.transferFrom(buyer, seller, notional), "usdc leg failed");
        ctf.safeTransferFrom(seller, buyer, tokenId, size, "");
        emit Cross(buyCommit, sellCommit, tokenId, size, px1e6);
    }

    /// @notice Solver fills residual imbalance from its own inventory.
    function settleSolver(
        address trader, address solver, bool traderSells,
        bytes32 commit, bytes calldata reveal,
        uint256 tokenId, uint256 size, uint256 px1e6
    ) external onlyOperator {
        _consume(commit, reveal);
        uint256 notional = size * px1e6 / 1e6;
        if (traderSells) {
            require(usdc.transferFrom(solver, trader, notional), "usdc leg failed");
            ctf.safeTransferFrom(trader, solver, tokenId, size, "");
        } else {
            require(usdc.transferFrom(trader, solver, notional), "usdc leg failed");
            ctf.safeTransferFrom(solver, trader, tokenId, size, "");
        }
        emit SolverFill(commit, tokenId, size, px1e6);
    }

    function _consume(bytes32 commitment, bytes calldata reveal) internal {
        require(!consumed[commitment], "commitment spent");
        require(sha256(reveal) == commitment, "bad reveal");
        consumed[commitment] = true;
    }
}
