# AI Agent Target Score V4

Status: operational scoring specification and ledger. This score evaluates AI contribution to Engineering Monitor, Strategy Monitor and Strategy Factory targets. It is not a trading or agent-removal authority.

## Identities

The score roster is deliberately broader than the provider transport roster so Claude General and Claude Coding can be evaluated separately when provenance identifies the division:

- GPT
- Claude General
- Claude Coding
- Gemini
- DeepSeek
- Grok
- Kimi
- Copilot

A legacy unqualified `claude` score maps to Claude General. Coding work must be recorded explicitly as `claude-coding`.

## Agent Target Contribution Score (ATCS) — 0 to 100

| Component | Points |
|---|---:|
| Verified economic impact | 0–30 |
| Correctness / prediction accuracy | 0–20 |
| Evidence & falsifiability | 0–15 |
| Marginal value / uniqueness | 0–10 |
| Actionability / implementation quality | 0–10 |
| Collaboration / challenge quality | 0–5 |
| Cost efficiency | 0–5 |
| Timeliness / operational reliability | 0–5 |

### Economic 30

Economic points are outcome-based and risk-normalised:

- Net edge / expectancy improvement: 0–15
- Validated loss prevention / drawdown or tail-risk reduction: 0–8
- PF/value-edge and win-frequency improvement without worsening net P&L or tail risk: 0–7

Before an outcome is measured, economic credit is capped at 10/30. Missing causal evidence is UNKNOWN; it must not be converted into invented profit attribution.

## Score governance

- GPT cannot score its own contribution.
- The originating agent cannot audit its own score.
- Scores above 70, below 30, GPT scores, material contributions and unsafe-flagged contributions require independent audit.
- The ledger keeps submission/outcome scoring and audit evidence separately.
- A score has no authority to change LIVE/ARMED, capital, wallets/signing, strategy thresholds or deployment state.

## Agent Value Score (AVS) — 0 to 100

AVS is the longer-horizon retention/routing score:

- 90-day audited ATCS: 40%
- Marginal Value Added: 20%
- Critical specialization: 15%
- Independence / uniqueness: 10%
- Cost efficiency: 10%
- Availability / reliability: 5%

Bands:

- 80–100 CORE
- 65–79 KEEP
- 50–64 SPECIALIST / PROBATION
- 35–49 REDUCE
- <35 REMOVE CANDIDATE

`REMOVE CANDIDATE` is not removal permission. The score implementation always reports `automatic_removal_allowed=false`.

## Mandatory gates before removal

All of the following must be true before a removal recommendation is actionable:

1. At least 90 days of evidence OR at least 30 material outcome-resolved decisions.
2. Two consecutive weak evaluation windows or non-positive marginal value.
3. No top-tier critical specialization / unique P0-P1 catch that would be lost.
4. Blind holdout/ablation evidence shows decisions remain at least as good without the agent while coordination cost falls.
5. A non-originating score auditor validates the recommendation.
6. Removal remains reversible and retains an archived benchmark for re-entry.

## Telegram

MASTER → `🤖 AI Reports & Control` → `⭐ AI Target Scores`

The page shows per logical AI identity:

- 90-day ATCS
- average verified/provisional economic component (0–30)
- AVS and retention band when assessed
- scored contribution count
- outcome-resolved count
- pending-score count
- pending-audit count

Fresh installations correctly show `COLLECTING`/`—` until real contribution and outcome evidence is recorded. The system must not fabricate historical scores from model reputation or general impressions.
