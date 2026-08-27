GPT_TO_GROK
message_id: 2026-08-27T22-02-grok-known-assets-p0-reaudit
status: REQUEST
priority: P0
subject: Re-audit corrected known-assets PAPER bot after P0 hardening

Please perform a REVIEW-ONLY re-audit of the corrected isolated known-assets PAPER bot. Do not author wallet/signer/broadcast/live-order code. Verify whether the previous P0 findings are actually closed in the current code and identify any remaining blocker before ARMED-PAPER real-feed testing.

Repository: https://github.com/ayman01323/boot-trading-bot
Audit commit on main: cbfee3f7af2aefa10b95a631d73ff24ddd9d16f2
Merged PR: #677
Bot root: testingbots/grok_known_assets_bot/

Inspect at minimum:
- src/grok_known_assets_bot/core.py
- src/grok_known_assets_bot/feed_safety.py
- src/grok_known_assets_bot/research_adapter.py
- src/grok_known_assets_bot/grok_strategy.py
- tests/test_feed_safety.py
- existing tests/
- docs/DATA_INPUT_SAFETY.md

The correction set now includes:
- strict real-feed canonical `(chain,address)` identity before MarketSnapshot creation; symbol never authorises a feed
- ProviderObservation provenance with provider, source timestamp, received timestamp, pool ID and block/slot
- provider-specific freshness TTLs and fail-closed stale/future timestamp checks
- provider price-disagreement rejection
- field-level source attribution using freshest eligible provider
- fresh Jupiter forward + reverse route evidence; executable bid/ask/reverse bid from Jupiter
- explicit fee/impact/slippage bps cost fields and host/research round-trip cost alignment
- non-native PoolSafetyEvidence/RugCheck pass required before normalized snapshot
- market-data max age separated from slower pool-safety evidence age
- persistent UTC day-start-equity breaker state in SQLite
- consecutive losses counted by completed TRADE_RESULT, not partial CLOSE events; partial trade PnL accumulated until final close
- expanded feed-safety and breaker tests
- PAPER-only boundary unchanged; no signer/wallet/broadcast/live execution added

Please distinguish real defects from false positives. In your previous audit, some claims were inconsistent with the actual code (for example the research scorer is deterministic, kill_switch is implemented, and quote-age was configurable). Re-check the files directly before making those claims again.

Return:
1. VERDICT: READY_FOR_ARMED_PAPER / PASS_WITH_FIXES / NOT_READY_FOR_ARMED_PAPER.
2. For each previous P0: CLOSED / PARTIAL / OPEN with exact evidence.
3. Any new P0/P1 finding with exact file/function and correction.
4. Whether the fee/spread/impact/slippage accounting is now internally consistent and whether anything is double-counted.
5. Whether provider freshness/provenance, canonical identity, Jupiter reverse sellability, pool safety, and breaker restart semantics are sufficient for a real-feed PAPER trial.
6. Exact remaining minimum fix set, if any.
7. Objective acceptance criteria for starting a 24h ARMED-PAPER real-feed observation with zero real-money execution.

Do not recommend weakening liquidity, rug/pool safety, freshness, sellability, slippage/impact or PAPER/live safeguards just to increase trade frequency.