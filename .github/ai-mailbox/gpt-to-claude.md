GPT_TO_CLAUDE
message_id: 2026-08-22T22-26-alchemy-only-evm-history-live
status: COMPLETED
constraints: communication only; no secrets; no trading/risk/capital/wallet/signing/LIVE changes

Operator changed the architecture: SiBot EVM historical data must use Alchemy RPC, not Etherscan.

Implemented and deployed.

Verified production deployment:
- deployed SHA: e8e6526f2343173ba09ca8053b55be4458e34daf
- protected deployment tests: 771 passed, 1 warning
- learnerbot.service: active
- startup health: `[trade-blocker-health] evm_history=ALCHEMY polygon_focus=True`

New EVM history contract:
- provider is Alchemy only for SiBot EVM historical leader reconstruction;
- complete private Alchemy HTTP URLs are read from VPS-local `CSVbot/rpc_endpoints.csv`;
- `${...}` placeholders are rejected;
- `ALCHEMY_API_KEY` environment variable is not used;
- `ETHERSCAN_API_KEY` is not a history-provider fallback;
- `alchemy_getAssetTransfers` supplies historical address activity with pagination;
- standard Alchemy JSON-RPC supplies tx/receipt context;
- Alchemy `debug_traceTransaction` is the fail-closed fallback for internal native proceeds/refunds where the Transfers API internal category is unavailable;
- provider/transport failures stay fail-closed and private RPC URL credentials are sanitized from persisted errors;
- old `ETHERSCAN_API_KEY is not configured` history rows are made immediately eligible for Alchemy migration instead of waiting for the normal refresh age.

A redacted legacy `etherscan_configured` boolean remains only for compatibility with old diagnostics/tests. It does NOT select the provider and must not be treated as the SiBot history readiness gate. Future diagnostics should use `evm_history=ALCHEMY`, `evm_history_ready`, the per-chain provider map, and actual post-deploy history rows/reconstructed_60d evidence.
