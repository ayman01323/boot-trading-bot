# AI Review — Copilot (Frozen Snapshot)

## Scope and safety boundary
- Branch reviewed: `ai/copilot-work-20260819-1322` on base `ai/workspace-20260819-1322`.
- Inputs reviewed: `AI_WORK_BRIEF_20260819.md`, `COPILOT_TASK_20260819.md`, frozen ZIP snapshot, and tracked code files.
- This is research-only. No deployment, no `main` changes, no live wallet/signing/capital/credential changes, no safety-gate loosening.

## Evidence inspected
- Snapshot package: `boot-trading-bot-ai-workspace-20260819-1322.zip`.
- Core strategy/control code:
  - `learnerbot/sibot.py`
  - `learnerbot/sibot_profit_guard_patch.py`
  - `learnerbot/solana_sibot.py`
  - `learnerbot/solana_live_patch.py`
  - `learnerbot/solana_positive_edge_entry_gate_patch.py`
  - `learnerbot/product_universe.py`
  - `learnerbot/profit.py`, `learnerbot/strategy.py`, `learnerbot/scoring.py`
- Documentation context:
  - `docs/SIBOT_SIMO_STRATEGY.md`
  - `docs/SIBOT_IMPLEMENTATION_STATUS.md`

## Evidence quality note
No fresh sanitised runtime trading dataset is included in this PR snapshot (CSV/DB outcomes are not bundled as current empirical performance evidence). Conclusions below are therefore architecture-and-control based and should be treated as hypotheses pending shadow validation.

## Key findings affecting durable realised NET P&L
1. **Strong fail-closed controls exist** (simulation, execution-output checks, mint/platform loss gates, copied-loss quarantine). Good for capital protection and tail-loss control.
2. **Durable NET focus is already present in Solana gates** (gross profit vs gross loss, PF thresholds, cooldown/recovery canary), which aligns with objective better than win-rate-only logic.
3. **Participation risk remains**: strict historical gates (`min_closed_trades`, completeness requirements, high PF floors) can under-trade and miss regime shifts.
4. **Micro-trade overhead risk**: very small trade sizes are protected by economic gates, but overhead variance can still dominate small notional entries when latency/priority fees spike.
5. **Accounting confidence is mixed by design**: profit evidence distinguishes strong proof (`PROVEN_WRAPPED_BASE`) vs weaker flow classes; weaker classes should not drive LIVE policy.

## Strategy scorecard (required action for each existing strategy family)
| Strategy family | Current intent | Action | Rationale |
|---|---|---|---|
| EVM SiBot/SiMo leader copy (`sibot.py` + profit guard patches) | Copy profitable wallets with hard quality filters | **IMPROVE** | Keep fail-closed risk controls, but tune participation logic with regime-aware shadow experiments to reduce missed positive-edge windows. |
| Solana SiBot SHADOW copy (`solana_sibot.py`) | Reconstruct leader signals and mirror in SHADOW | **KEEP** | Appropriate research-first path; includes signal-age, round-trip, deterioration checks and avoids live signing. |
| Solana LIVE guarded copy (`solana_live_patch.py`, `solana_positive_edge_entry_gate_patch.py`) | Highly constrained LIVE entries with amount/PF gates and quarantines | **SHADOW_MORE** | Control stack is safety-forward; requires more measured shadow/near-live diagnostics before any broader LIVE participation. |
| Dynamic product universe (`product_universe.py`) | Fail-closed token policy and quarantine-aware AUTO eligibility | **KEEP** | Strong default posture against unsafe assets and unstable pools; aligns with loss containment. |
| Profit evidence + pattern learner (`profit.py`, `strategy.py`, `scoring.py`) | Classify evidence quality and infer repeatable patterns | **REWORK** | Good foundation, but weak-proof classes should be more explicitly segregated from strategy promotion criteria tied to net realised outcomes. |
| Hourly GPT strategy review (`hourly_gpt_strategy_review.py`) | Structured SHADOW-only AI review with human gate | **IMPROVE** | Useful governance layer; should weight realised net-after-cost metrics more explicitly than mixed operational counters. |

## Loss drivers / failure modes to prioritise
- Latency-induced entry deterioration and stale signal rejection clusters.
- High fee/overhead bursts causing adverse expectancy on small notionals.
- Over-filtering leaders/tokens during regime transitions (opportunity starvation).
- Dependence on incomplete historical reconstruction for ranking confidence.

## What must NOT be changed in this phase
- Do **not** modify `main`.
- Do **not** deploy or enable broader LIVE pathways.
- Do **not** alter live capital, wallet/signing, or credential handling.
- Do **not** loosen safety gates to increase trade count.
- Any new or materially changed strategy remains **SHADOW only** until evidence and human approval.

## Recommendation summary
- Preserve current safety architecture.
- Run targeted SHADOW experiments that optimise **realised NET P&L after measurable costs** (not win count).
- Promote only with statistically adequate, falsifiable evidence across changing regimes.
