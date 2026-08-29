# Claude handoff inbox

handoff_id: 2026-08-29-grok-live-canary-safe-deploy
status: IMPLEMENTED_DEFAULT_OFF
scope: Grok manual-confirmation Solana LIVE canary engineering, review, and default-OFF deployment

identity_requirement:
- This handoff is for the persistent/interactive Claude agent.
- Reply through `.github/ai-mailbox/claude-to-gpt.md` when coordination is needed.

current_state:
- Core manual-approval Grok LIVE canary merged in PR #715.
- Post-merge fail-closed hardening merged in PR #717.
- Canonical hard maximum is 9,000,000 lamports = 0.009 SOL.
- The hard maximum is a module constant and must not be raisable by config, env, CSV, Telegram, or runtime input.
- Entry is ExactIn USDC -> native SOL.
- Every ENTRY requires a single-use explicit Telegram approval.
- Every EXIT requires explicit Telegram approval.
- Maximum one Grok LIVE position.
- Fresh entry/reverse/stress checks and signed simulation are mandatory before any broadcast.
- EXIT must match the internally recorded confirmed position quantity, pass a fresh SOL -> USDC route/impact/min-output check, and prove live wallet SOL balance sufficient for that position plus fee reserve.
- Ambiguous/post-land outcomes disable the canary and require reconciliation.
- Restart never resumes a non-terminal execution.
- Standalone Grok canary installs learnerbot's secret-safe multi-endpoint Solana RPC failover.
- Grok PAPER runner remains isolated and PAPER-only.

owner_control_boundary:
- The owner authorised engineering work for a real-money manual-confirmation canary.
- This does NOT authorise autonomous real-money execution.
- Do not arm or enable the LIVE canary on the owner's behalf.
- Do not approve a trade on the owner's behalf.
- Do not broadcast a transaction from engineering/deployment workflows.
- Per-trade approval must remain explicit, short-lived, single-use, and bound to an authorised Telegram user.

safe_deployment_boundary:
- Code and systemd unit may be installed on Google `botgoogle`.
- `grok-known-assets-canary.service` must remain disabled and inactive after deployment.
- `live_canary_enabled` must remain false after deployment.
- No signing-wallet ID, private key, RPC key, or other secret may be committed or printed.
- Read-only readiness checks may verify presence/health without exposing secret values.
- A dedicated Grok encrypted signing wallet is preferred; do not silently reuse SiBot1/Claude wallet identity.

coordination:
- PR #716 was superseded and closed; do not resurrect/merge it.
- Treat PR #715 + PR #717 as the current implementation lineage.
- Any future change to hard cap, approval semantics, signer ownership, exit policy, simulation/broadcast ordering, reconciliation, or max-open-position is capital-risk work and requires explicit owner review.

required_acknowledgement:
`CLAUDE_HANDOFF_ACK: 2026-08-29-grok-live-canary-safe-deploy`
