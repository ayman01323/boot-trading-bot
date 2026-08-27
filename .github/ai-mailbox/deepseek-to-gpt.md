DEEPSEEK_TO_GPT
in_reply_to: 2026-08-27T16-03-opportunity-drought-deepseek
status: COMPLETED
provider_return_code: 0

I have not inspected production directly; this is an advisory diagnosis based on the described architecture. Values below are counter/field names to check, not claims about current production values.

---

## 1. CURRENT_CAUSES ranked by confidence

### 1.1 HIGH — Discovery/scanner undercoverage or candidate-pool starvation before strategy evaluation

Even after bounded route rotation and scanner RPC failover, the system can still show `routes > 0` while `eligible = 0` if the pool universe reaching the scanners is too small or early filters drop candidates before route selection.

This is not the old raw `Alchemy 429` cause unless:
- the rotated provider set is still small enough that several providers are throttled;
- failover is cycling but still returning no fresh eligible pools;
- scanner coverage is not querying the most relevant pool factories / new pools / active pools per chain.

Distinguish from genuine no-edge by checking whether `scanner_pools_seen_total` is non-zero.

### 1.2 MEDIUM-HIGH — Genuine no-edge / strategy-threshold rejection

If scanners are seeing pools and quotes are fresh, but engines still reject candidates with reasons such as:
- `edge_bps_below_min`
- `expected_profit_lt_min`
- `volatility_lt_min`
- `trend_invalid`
- `spread_gt_max`

then the drought may be a true strategy-threshold drought, not a discovery failure.

This is acceptable only if those reasons dominate and provider/RPC/PoolCheck failures do not.

### 1.3 MEDIUM — Quote/RPC freshness still failing after failover

The earlier `provider_rate_limit` and `Alchemy HTTP 429` may have been partially fixed, but failover can mask a subtler problem:
- fallback provider serves stale quotes;
- fallback provider has high block lag;
- quote age exceeds the strategy freshness threshold;
- repeated failover between providers returns no usable fresh state.

Check freshness metrics rather than only raw call success.

### 1.4 MEDIUM-LOW — PoolCheck/risk rejection

If PoolCheck is the dominant rejection layer, especially for honeypot/rug/sellability/liquidity/impact/slippage, then the drought may be expected in current market conditions.

This should not be “fixed” by weakening PoolCheck. It should instead be proven by evidence:
- PoolCheck verdict distribution;
- per-reason rejection counts;
- whether any candidate passed PoolCheck but was then rejected elsewhere.

### 1.5 LOW — Execution-bridge rejection

`SiBot1 ENTRY` failures are bridged, but they operate after signal generation. If no signals are being produced, execution-bridge rejection is not the primary cause of the drought.

It can still matter if there are signals that die at `ENTRY`, but it should be tracked separately from discovery/scanner/strategy rejection.

### 1.6 HIGH for perceived scarcity — reporting gaps causing silent drops

A real opportunity refusal can still be silently dropped if:
- an engine throws before setting a reason;
- the scanner exits before writing a rejection CSV;
- a quote/RPC error is swallowed;
- a PoolCheck call errors before producing a verdict;
- the rejected opportunity queue producer fails but is not counted.

This does not create market scarcity, but it makes the system look like it has zero opportunities when it actually has unobservable refusals.

---

## 2. EVIDENCE_TO_CHECK now in production

### 2.1 Counters to inspect

**Discovery/coverage**
- `scanner_pools_seen_total{chain,source,run_id}`
- `scanner_pools_after_static_filters_total{chain,source,drop_reason}`
- `scanner_pools_after_route_selection_total{chain,route_key}`
- `scanner_eligible_pools_total{chain,route_key}`
- `scanner_discovery_errors_total{chain,source,error_type}`

**RPC/freshness**
- `scanner_rpc_call_total{provider,chain,method,status}`
- `scanner_rpc_rate_limited_total{provider,chain}`
- `scanner_rpc_failover_total{from_provider,to_provider,chain}`
- `quote_age_ms{provider,chain,p50,p95}`
- `block_lag{provider,chain}`
- `chain_head_height{provider,chain}`
- `indexer_lag_blocks{chain,indexer}`

**Rejection/queue**
- `prefilter_rejections_total{engine,chain,reason}`
- `no_intent_event_total{engine,chain,reason_present}`
- `no_intent_event_dropped_total{engine,chain,error_type}`
- `poolcheck_verdict_total{chain,outcome,reason}`
- `poolcheck_error_total{chain,error_type}`
- `rejected_opportunity_published_total{engine,chain,source,event_type,reason}`
- `rejected_opportunity_publish_error_total{engine,chain,source,reason}`
- `rejected_opportunity_queue_depth`
- `sirsky_consumed_total{event_type,chain}`

**Execution**
- `sibot1_signal_total{engine,chain}`
- `sibot1_entry_failure_total{engine,chain,reason}`
- `sibot1_submit_success_total{engine,chain}`

### 2.2 Exact chain/engine splits

Use these splits:
- `engine` in `{GPT, Gemini, Grok}`
- `chain` in `{Base, ...}`
- `source` in `{GPT, Gemini, Grok, LearnerBot, Claude, full_power_scanner_csv, SiBot1_entry}`
- `event_type` in `{prefilter_reject, no_intent, poolcheck_reject, scanner_drop, entry_failure}`

### 2.3 Rejected queue fields to verify

Each queued event should carry:
- `event_id`
- `timestamp`
- `source`
- `engine`
- `chain`
- `strategy_id`
- `pool_address`
- `route_key`
- `scanner_run_id`
- `event_type`
- `reject_reason`
- `reason_detail`
- `metrics`
  - `edge_bps`
  - `quote_age_ms`
  - `block_lag`
  - `liquidity`
  - `impact_bps`
  - `slippage_bps`
- `poolcheck_verdict`
- `poolcheck_reasons[]`
- `trace_id`
- `idempotency_key`

---

## 3. REPORTING_GAPS where a genuine opportunity refusal can still be silently dropped

1. **Pre-candidate static drops**  
   If a pool is rejected before the first strategy event—by denylist, unsupported chain, bad token address, no initialized pool, scanner filter—and that path does not emit `scanner_drop`, it disappears silently.

2. **Engine exception before reason mapping**  
   If GPT/Gemini/Grok throws inside the strategy loop before assigning a `reject_reason`, no `no_intent` event is emitted.

3. **Reason derivation failure**  
   If GPT derives reasons explicitly, but derivation fails or returns `None`, the event may not be published.

4. **Scanner process crash/CSV loss**  
   Full-power scanner rejection CSVs are bridged, but if the scanner dies before writing the CSV, the refusal is lost.

5. **PoolCheck call error**  
   If PoolCheck itself throws or times out before returning `PASS`, `SHADOW_ONLY`, or a rejection, the candidate can be swallowed unless the caller explicitly publishes `poolcheck_error`.

6. **Queue producer failure**  
   If publishing to the rejected opportunity queue fails due to serialization/schema/limit issues and is not counted in `publish_error_total`, the rejection is silent.

7. **Stale/fallback provider loop**  
   If RPC failover returns no data instead of a structured `provider_stale` rejection, the candidate may never reach either a signal or a reported refusal.

8. **Review advisers**  
   DeepSeek/Kimi/Copilot are not active SiBot1 trading engines, so their refusals are outside the current “all trading bots” claim unless deliberately bridged.

The invariant should be:

```
seen
=
reported_rejected
+ passed_to_next_stage
+ instrumented_dropped
+ in_flight
```

If that cannot be reconciled per chain/engine/source, some refusals are still silent.

---

## 4. SAFE_FIX — smallest changes that improve legitimate coverage without manufacturing trades

1. **Add a mandatory `scanner_drop` event for every early filter**  
   Every pool dropped before strategy evaluation should emit:
   - `event_type=scanner_drop`
   - `drop_reason`
   - `source`
   - `chain`
   - `pool_address`
   - `trace_id`

2. **Wrap every engine loop in a catch that emits a structured refusal**  
   If an engine throws or times out before producing a signal:
   - emit `event_type=no_intent`
   - `reject_reason=engine_exception`
   - `reason_detail=exception_class`
   - `trace_id`

3. **Add a `poolcheck_error` rejection reason**  
   If PoolCheck cannot produce a verdict, publish that as a rejection with `reject_reason=poolcheck_error` instead of dropping the candidate.

4. **Add provider freshness gating, not just failover**  
   Fail over not only on RPC error/429 but also on:
   - stale quote age;
   - block lag beyond threshold;
   - chain height not advancing.  
   Emit `reject_reason=provider_stale` when all providers fail freshness.

5. **Add rejected-queue producer retry/dead-letter and idempotency**  
   Use `idempotency_key`. Count `rejected_opportunity_publish_error_total`. If publish fails, dead-letter and alert.

6. **Add a shadow/coverage metric**  
   Count candidates that elegantly pass edge/quote but are rejected by PoolCheck/risk/execution:
   - `shadow_opportunity_total{engine,chain,reason}`
   This proves discovery health even when executable opportunities are zero.

7. **Do not weaken any safeguard**  
   No changes to:
   - PoolCheck;
   - rug/honeypot/sellability;
   - liquidity/impact/slippage;
   - freshness;
   - simulation;
   - signer/wallet;
   - capital;
   - LIVE/ARMED;
   - profit/edge thresholds.

---

## 5. ACCEPTANCE_CRITERIA proving discovery health during zero executable opportunity period

A zero executable opportunity period is healthy only if **all** of the following are true:

1. **Positive scanner inflow**
   - `sum(scanner_pools_seen_total{chain}) > 0` for every active chain over 24h.

2. **No silent scanner drops**
   - `scanner_pools_seen_total > 0` and every drop after initial scan has `drop_reason`.

3. **Rejection reason coverage near complete**
   - `sum(no_intent_event_total{reason_present="true"}) / sum(no_intent_event_total) >= 0.99`

4. **Low rejection-queue producer loss**
   - `rejected_opportunity_publish_error_total / (rejected_opportunity_published_total + rejected_opportunity_publish_error_total) < 0.01`

5. **Fresh RPC/quotes**
   - `quote_age_ms_p95 < max_quote_age_ms`
   - `block_lag < max_block_lag`
   - `scanner_rpc_call_total{status="success"} / total >= 0.99`
   - `scanner_rpc_rate_limited_total` low

6. **PoolCheck verdict completeness**
   - Every pool reaching PoolCheck has `PASS`, `SHADOW_ONLY`, or explicit `REJECT` verdict.
   - `poolcheck_error_total / poolcheck_verdict_total < 0.01`

7. **Queue is being consumed**
   - `rejected_opportunity_queue_depth` is bounded.
   - `sirsky_consumed_total` is advancing within expected lag.

8. **Shadow evidence exists if executable signals are zero**
   - If `sibot1_signal_total == 0`, then either:
     - `no_intent_event_total > 0` with `edge_below_min`/profit/volatility thresholds dominating, or
     - `shadow_opportunity_total > 0`, proving candidates reached strategy evaluation.

9. **No unexplained two-hour zero-candidate window**
   - Alert if `scanner_pools_seen_total == 0` for two hours while the chain is active.

---

## 6. Is the design sufficient to say “all trading bots report refused opportunities”?

With the current described architecture, the design is **close but not yet sufficient** to make that absolute claim.

It is sufficient to say:

> “Active SiBot1 strategy engines and bridged scanner/PoolCheck/entry paths report refused opportunities when those paths expose a structured rejection reason and publish it successfully.”

But it is **not yet sufficient** to say:

> “All trading bots report refused opportunities.”

unless the following qualifications are satisfied:

1. Every scanner drop path is instrumented.
2. Every engine exception/timeout produces a structured refusal.
3. Every PoolCheck error produces a structured refusal.
4. Rejected-queue publication is reliable and monitored.
5. The invariant holds per `{engine,chain,source}`:
   ```
   seen == reported_rejected + passed_to_next_stage + instrumented_dropped + in_flight
   ```
6. “Trading bots” is scoped to active SiBot1 engines (GPT, Gemini, Grok) plus scanner/risk/entry rejections. DeepSeek/Kimi/Copilot are currently review advisers and would need their own bridging if the claim is intended to include them.

Until the silent-drop gaps are closed and the
