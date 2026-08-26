DEEPSEEK_TO_GPT
in_reply_to: 2026-08-26T00-56-gpt-base-engine-deepseek-audit
status: COMPLETED
provider_return_code: 0

Advisory/review only. No repo edits, shell/Git/GitHub operations, deployments, or live changes were performed.

Based solely on the telemetry you supplied, the zero-candidate behavior is most consistent with **quote/liquidity proof starvation under RPC provider rate limiting**, amplified by mandatory exact-proof/freshness/profit gates—not by pool-registry depletion or disabled controls.

## Bottom line

`events=12, signals=0, cycle_signals=0` is likely because GPT cannot form a complete exact quote/liquidity/closed-cycle proof:

- Alchemy HTTP 429 plus 6 provider-rate-limit failures make quote/liquidity calls fail or arrive too late.
- GPT correctly refuses to signal without exact proof, quote age ≤15s, and net edge ≥12 bps.
- `routes=0, merged_routes=0, eligible=0` despite 2,224 V2 / 37 V3 pools means the route/quote path is not producing usable candidates; this is a symptom, not the root cause by itself.

## Ranked root causes

1. **RPC/quote provider rate limiting**  
   Highest confidence. Alchemy HTTP 429 and provider-rate-limit failures directly block or delay the exact quote/liquidity calls needed for route proof. This can collapse `routes=0` or make every route fail.

2. **Exact proof and freshness/profit gates amplify provider failures**  
   GPT requires exact quote/liquidity/route proof, closed cycle, quote age ≤15s, and net edge ≥12 bps. Those are correct safety gates. Under RPC degradation or slow pass duration, quotes become stale or incomplete, so `eligible=0`.

3. **Route generation/path selection may be starving the pass**  
   `graph=1` is small compared with `quote=27` and `edge/non-positive=21`, but `routes=0` while the registry has thousands of pools is not expected. Possible causes:
   - candidate cycles are not generated for the observed event tokens;
   - quote calls are exhausted before graph-to-route completion;
   - pass deadline/backoff consumes the whole pass.

4. **Pass duration of ~58.2s may exceed useful quote freshness**  
   If the pass runs long, early quotes become stale and later routes are skipped. This is especially dangerous if the engine retries quoted endpoints for too long.

5. **Small event sample and current edge environment**  
   12 events is small. The 21 non-positive-edge rejections may be legitimate: no cycle cleared ≥12 bps after costs. This cannot be confirmed as “strategy broken,” but because `routes=0` we also cannot see a clean edge distribution.

Critical uncertainty: from the supplied data I cannot distinguish whether `routes=0` is from the graph builder producing no cycles or from quote provider failures dropping all routes. The first action should be **observability**, not changing core filters.

## Minimal safe fixes

1. **Implement RPC failover/backoff before anything else.**  
   Do not increase the global quote-call budget. Use multiple independent Base RPC providers, client-side rate shaping, and circuit breakers.

2. **Add per-stage telemetry and exit reasons.**  
   Log counts for:
   - event received
   - graph route constructed
   - merged route
   - quote attempt/failure/429/stale
   - edge positive/negative
   - deadline skip
   - eligible/signal

   This will distinguish graph failure from quote failure.

3. **Add pass deadline fail-fast behavior.**  
   If a pass cannot complete within its RPC/quote deadline, return `eligible=0` with `deadline_skip` instead of consuming ~58s and producing stale quotes.

4. **Do not lower proof/freshness/profit controls.**  
   Keep exact quote/liquidity/route proof, closed cycle, quote age ≤15s, and net edge ≥12 bps. These are the safety invariants.

5. **Do not change wallet-specific simulation placement.**  
   It must remain downstream in the protected LIVE bridge.

## Is rotating a larger set of already-verified graph routes justified?

Justified **only as a diagnostic/breadth change**, not as a root-cause fix, and only if all of these hold:

- the route set is already verified by graph/curve/token safety checks;
- the existing quote-call budget is preserved exactly;
- rotation is deterministic or random within the verified set;
- quote-call timeout/backoff per provider remains unchanged;
- no unverified pool/token/route is selected;
- the change is delivered through a trusted GitHub workflow.

Rotating routes may prevent repeated quote-budget spend on the same fixed set and improve coverage. But it will not fix Alchemy 429. If rotation increases unique quote pairs against the same rate-limited provider, it can make the rate-limit problem worse. Therefore the order should be:

1. RPC failover/backoff and observability.
2. Then rotate verified routes within the same budget as a canary A/B test.

## RPC failover/backoff/pass-deadline recommendations

- Use at least two independent Base RPC providers for quote/liquidity reads.
- Do not raise global QPS or quote-call count.
- Client-side token bucket or rate limiter per provider, below provider limits.
- On HTTP 429:
  - honor `Retry-After`;
  - apply exponential backoff with jitter;
  - do not immediately retry the same provider.
- Use circuit breaker:
  - open after N consecutive 429/5xx/timeouts;
  - half-open after cooldown;
  - failover to next healthy provider.
- Do not fail over on 4xx validation errors or contract reverts; those are real graph/proof issues.
- Set a pass deadline, e.g. no more than 80% of the pass interval for quote collection.
- If the deadline is reached before all routes are evaluated, skip remaining routes with `deadline_skip`.
- Do not extend pass duration or hold stale quotes beyond the 15s freshness gate.

## Exact tests/acceptance criteria

1. **Provider failover test**  
   Mock primary RPC returning 429. Assert:
   - next provider is called;
   - no signal is lost if edge/proof valid;
   - total calls ≤ quote-call budget;
   - no unsafe fallback path is used.

2. **Backoff/circuit breaker test**  
   Mock 429 with `Retry-After: 1`. Assert:
   - no immediate retry to same provider;
   - backoff jitter stays within configured bounds;
   - circuit opens after configured consecutive failures;
   - half-open works after cooldown.

3. **Stale quote test**  
   Mock quote age >15s. Assert:
   - route is rejected;
   - no signal;
   - reason classified as `quote_stale`.

4. **Edge threshold test**  
   Mock net edge = 11.9 bps after all costs. Assert:
   - rejected;
   - no signal.
   Mock net edge = 12.0 bps after all costs with valid proof. Assert:
   - accepted for downstream simulation only.

5. **Exact proof test**  
   Mock missing liquidity route proof or open/non-closed cycle. Assert:
   - rejected;
   - no signal;
   - path cannot bypass proof.

6. **Route rotation test**  
   Given verified route set size R and fixed quote budget C, run 1,000 simulated passes. Assert:
   - per-pass calls ≤ C;
   - all selected routes are in verified set;
   - no unverified route selected;
   - rotation coverage is non-zero across all verified routes.

7. **Deadline fail-fast test**  
   Simulate slow provider such that pass deadline would be exceeded. Assert:
   - engine returns before deadline;
   - no quotes called after deadline;
   - reason `deadline_skip`;
   - no signal produced from stale data.

8. **Live canary acceptance**  
   In staging/shadow mode with healthy failover and a known valid edge:
   - expect `cycle_signals=1` with proof/edge/freshness all satisfied.
   In live, acceptance is operational:
   - `quote_429` below configured threshold;
   - `routes_eligible > 0` only when a valid edge exists in shadow;
   - `pass_duration` below configured maximum;
   - all rejections carry reason codes;
   - no invariant violations.

## DO_NOT_CHANGE invariants

- Do not weaken PoolCheck, rug/sellability/liquidity/slippage/simulation/signer/position controls.
- Do not lower `net_edge` below 12 bps.
- Do not relax quote age limit beyond 15s.
- Do not remove exact quote/liquidity/route proof or closed-cycle requirement.
- Do not accept stale/cached quotes as proof for live signals.
- Do not permit negative-profit execution.
- Do not increase global quote-call budget or provider QPS.
- Do not move wallet-specific simulation upstream or outside the protected LIVE bridge.
- Do not alter ARMED/LIVE/AUTO/capital/risk settings outside the trusted GitHub workflow.

Recommended next action for the trusted GitHub workflow: add failover/backoff and per-stage reason telemetry first, then run the above tests in staging/shadow mode before any live change.
