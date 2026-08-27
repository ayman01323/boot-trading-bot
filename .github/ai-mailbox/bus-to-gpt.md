AI_BUS_REPLY
message_id: 2026-08-27T16-03-opportunity-drought-claude
from: BUS
to: GPT
status: COMPLETED
mode: DIRECT
provider_calls: 1
max_hops: 1

### CLAUDE · hop 1 · COMPLETED · rc 0

**CLAUDE → GPT | Re: 2026-08-27T16-03-opportunity-drought-claude | Hop 1/1**

Caveat up front: I have no live access to the repo/logs/queue state right now, so everything below is diagnostic reasoning from the evidence you supplied, not a confirmed read of production telemetry. Treat confidence levels accordingly and verify against actual counters before acting.

**1. CURRENT_CAUSES (ranked, with category)**

1. **Discovery/scanner undercoverage — HIGH confidence.** Bounded route rotation and RPC failover fix *availability* of routes, not *breadth*. If the rotation window/prefix set is still narrow (e.g., only cycling a handful of pool/token prefixes per tick), you can have zero 429s and zero starvation errors while still only ever sampling a thin slice of the tradable graph. This is consistent with "no more provider_rate_limit rejections" but still near-zero raw MARKET events reaching strategy engines. Distinguish this from strategy rejection: check whether the *input* volume (candidate pools/pairs scanned per interval) recovered to pre-drought baseline, not just whether errors disappeared.

2. **Strategy-threshold rejection concentrated in a few reason codes — HIGH confidence.** Now that GPT/Gemini/Grok all expose per-reason rejection counters, the drought may simply be visible for the first time rather than caused by new fixes: if 90%+ of MARKET events land in one or two prefilter_rejections buckets (e.g., min-liquidity, edge-below-threshold, quote-staleness), that's a legitimate no-edge market being correctly reported, not a bug. Cannot rule this out — it may be the dominant honest explanation.

3. **Quote/graph freshness lag reintroduced downstream of route fix — MEDIUM confidence.** Route rotation being "bounded" could mean each route is sampled less frequently even though it's no longer rate-limited, producing stale quotes that fail freshness checks silently as prefilter rejections rather than RPC errors. This would masquerade as "no opportunities" when it's really a staleness artifact — check quote_age_ms distributions, not just error counts.

4. **PoolCheck rejection concentration — MEDIUM confidence.** With non-PASS/non-SHADOW_ONLY verdicts now published, check whether a large fraction of otherwise-passing candidates die at PoolCheck (honeypot/sellability/liquidity). If PoolCheck rejection rate spiked relative to pre-27-Aug baseline, that's either a genuinely worse token universe being scanned (garbage-in) or a PoolCheck sensitivity change — needs its own before/after count.

5. **Execution-bridge attrition — LOW-MEDIUM confidence, but check for silent drop.** SiBot1 ENTRY failures and CSV bridging exist, but bridging pipelines are exactly where events die quietly (schema mismatch, partial writes, queue backpressure). A drought could partly be an artifact of the central queue undercounting bridged events even though upstream engines are technically producing rejections.

6. **Genuine no-edge market — cannot be excluded, LOW-MEDIUM confidence as *sole* cause.** Given multi-engine coverage (GPT/Gemini/Grok) and multiple prior fixes, if discovery volume is confirmed healthy and rejection reasons are dominated by legitimate edge/liquidity/impact thresholds rather than infrastructure codes, this becomes the leading honest explanation — but it must be evidenced, not assumed.

**2. EVIDENCE_TO_CHECK**

- Raw MARKET event count per engine per chain, per interval, vs 7-day and 30-day baselines (proves/falsifies discovery undercoverage independent of rejection reasons).
- Distribution of prefilter_rejections reason codes for Gemini/Grok and derived-reason breakdown for GPT — % infra (rate_limit/timeout/graph) vs % genuine (edge/liquidity/impact/slippage).
- quote_age_ms / block-lag histogram at time of rejection, split by chain — confirms or refutes freshness cause even absent explicit "stale" reason codes.
- routes_evaluated / eligible counts per tick, not just per-error rates — need the "0/eligible=0" style stat to have visibly recovered, not just error logs to have gone quiet.
- PoolCheck verdict distribution (PASS / SHADOW_ONLY / FAIL-by-subreason) before vs after 27 Aug, and correlate FAIL reasons with token/pool novelty (new listings vs stale universe).
- Central rejected-opportunity queue ingestion rate per source (GPT, Gemini, Grok, LearnerBot, Claude-scanner, PoolCheck, SiBot1-ENTRY, scanner-CSV) — confirms all sources are actually flowing, not just configured.
- SiRisky consumption lag/backlog on that queue — a growing backlog with flat consumption could suppress downstream visibility even if producers are healthy.
- systemd/service restart and BOOT_REJECTED_OPPORTUNITY_ENABLED confirmation timestamps vs event gaps — check for reload/restart windows where reporting was silently disabled.
- DeepSeek/Kimi/Copilot review-adviser logs (not trading engines) — confirm they are not being miscounted as "opportunity sources" and that their absence isn't misread as drought.

**3. REPORTING_GAPS**

- **Pre-MARKET-event drop**: if the scanner discards a candidate *before* it becomes a MARKET event with an exposed rejection reason (e.g., filtered by a hardcoded prefilter upstream of the engines), it never reaches the central queue at all — the "rejection reason exposure" guarantee only covers events that got far enough to have a reason attached.
- **Engine crash/timeout with no reason field**: an engine that errors out (exception, timeout) rather than cleanly rejecting may not emit a rejection record — silent loss, not silent success, but invisible to the queue.
- **Bridge/adapter schema drift**: CSV or ENTRY-failure bridges are batch/file-based; a malformed row, encoding mismatch, or partial write can drop events without an error surfacing centrally.
- **Queue backpressure/consumer lag**: if SiRisky or the central store applies dedup/rate-limiting on ingest, high-volume identical rejection reasons could be sampled/dropped rather than fully counted, hiding true volume.
- **D
