# Strategy SHADOW → CANARY → FULL LIVE promotion policy

This policy defines when a Strategy Factory experiment is eligible to progress. It does not itself arm trading, change capital, sign a transaction, bypass safety checks, deploy code, or approve LIVE.

## 1. SHADOW

Every new or materially changed strategy starts in SHADOW.

The existing Strategy Lab evidence gate remains authoritative. A strategy must have enough separate evaluation evidence to become a `PROMOTION_CANDIDATE`; sparse evidence is not treated as success. The current Strategy Lab defaults require at least three evaluation windows, at least eight evaluated trades, adequate eligible-opportunity evidence, positive money-weighted net performance after recorded costs, and profit factor at or above the configured Strategy Lab floor (currently 1.10), subject to the Lab's other execution and participation checks.

A strategy that fails these checks remains SHADOW, becomes PROBATION/REWORK/REPLACE as appropriate, or is retired. The Factory may propose changes, but cannot promote itself.

## 2. PROMOTION CANDIDATE

`PROMOTION_CANDIDATE` means the SHADOW evidence is good enough to request a small LIVE canary. It is not permission to trade LIVE.

Before a canary is allowed:

- Engineering evidence must show no unresolved execution/safety defect affecting that path.
- The candidate must still pass the common LIVE quote, positive-edge, liquidity, sellability, slippage/price-impact, reserve, nonce, signing and preflight/simulation gates.
- MASTER approval is required because a canary uses real funds.
- Canary capital/exposure must be explicitly bounded and must not silently increase another LIVE strategy's capital.

## 3. CANARY LIVE

The canary trades only the next naturally eligible opportunities. Promotion never forces a trade merely to reach a sample count.

A canary becomes **READY FOR FULL LIVE** only when all of the following are true:

1. At least 24 hours have elapsed since the first canary execution.
2. At least 10 closed real canary trades have been observed. If the strategy produces fewer eligible trades, it stays in canary until the sample exists; time alone is insufficient.
3. Realised net P&L after recorded network fees, platform fees, slippage and other recorded execution costs is positive.
4. Canary profit factor is at least 1.10.
5. There is no unresolved landed-invalid execution, exit/sellability failure, reserve breach, signing/wallet fault, nonce/reconciliation fault, or other execution-safety regression attributable to the candidate.
6. Engineering Monitor has no unresolved P0/P1 defect on the candidate execution path.
7. Trade latency/RPC latency is either within the measured same-server baseline or Engineering has shown with evidence that the deviation did not invalidate the canary result.
8. The candidate has not triggered a strategy or execution circuit breaker that requires rework.

If any condition fails, the candidate returns to SHADOW/PROBATION/REWORK rather than automatically receiving more capital or being promoted.

## 4. READY FOR FULL LIVE

Passing the canary does not silently activate FULL LIVE. The MASTER dashboard must show the candidate under `LIVE CHANGES WAITING`.

FULL LIVE requires explicit MASTER approval. Approval authorises activation of that candidate only; it does not authorise an automatic capital increase, removal of existing safety gates, or unrelated strategy/configuration changes.

After approval, deployment/configuration checks and the normal LIVE preflight must pass. The strategy then becomes active for the **next eligible market signal**. It does not immediately place a trade solely because promotion occurred.

## 5. FULL LIVE ongoing review

FULL LIVE is not the end of monitoring. Engineering Monitor and Strategy Monitor continue to measure it by exact strategy version/Git SHA and chain.

- Strategy Monitor compares realised net P&L, profit factor, losses/drawdown, opportunities, execution leakage and failures against its SHADOW/canary evidence.
- Engineering Monitor checks latency, RPC behaviour, execution failures, infrastructure cost and technical regressions.
- Material deterioration sends the evidence package back to Strategy Factory for REWORK/REPLACE analysis.
- Existing deterministic stop-loss, circuit-breaker and execution-safety controls remain authoritative at all times.

The lifecycle is therefore:

`SHADOW → PROMOTION_CANDIDATE → MASTER CANARY APPROVAL → CANARY LIVE → READY FOR FULL LIVE → MASTER FULL-LIVE APPROVAL → FULL LIVE → CONTINUOUS MONITORING → REWORK/REPLACE when evidence deteriorates`.
