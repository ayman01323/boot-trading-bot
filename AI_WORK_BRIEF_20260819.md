# AI Trading Bot Work Brief — Frozen Snapshot 2026-08-19 13:22 BST

Baseline ZIP: `boot-trading-bot-ai-workspace-20260819-1322.zip`
Baseline branch: `ai/workspace-20260819-1322`

## Objective
Improve durable realised NET profitability after all measurable costs while reducing loss magnitude, execution failures and avoidable drawdown. Do not optimise merely for win count.

## Evidence priority
1. Realised net P&L after costs.
2. Gross profit versus gross loss and profit factor.
3. Average and largest loss versus average and largest win.
4. Execution/exit failures, slippage, fees, latency and stale quotes where measured.
5. Opportunity participation and skip reasons.
6. Strategy performance by regime and sample size.
7. Data quality and accounting integrity.

## Required independent review
Each AI must independently inspect the frozen code snapshot and the latest sanitised trading evidence available in the repository workflow inputs. Do not copy another AI's conclusion before producing your own.

For every existing strategy return one of: KEEP, IMPROVE, REWORK, SHADOW_MORE, DORMANT, REPLACE.

Each AI may propose up to two genuinely new, falsifiable strategy hypotheses. New ideas must specify market regime, entry hypothesis, exit logic, required data, expected costs, failure modes, shadow test, minimum observations/trades and falsification criteria.

## Safety boundary
- Do not change `main`.
- Do not deploy to the VPS.
- Do not change live capital, wallet/signing configuration or credentials.
- Do not loosen safety gates to manufacture more trades.
- Do not force a trade when there is no positive expected edge.
- Any new or materially changed strategy starts SHADOW only.
- Any code implementation must remain on its dedicated AI branch and be reviewable as a draft PR.
- Never include secrets, private keys, wallet addresses or unsanitised private data in AI output.

## Architecture rule
Reuse the existing validation/execution path. Do not create a second signer or independent live execution path. Candidate strategies should emit a common candidate into the existing simulation/liquidity/sellability/slippage/positive-edge/capital/quarantine/exit controls.

## Deliverables
1. `AI_REVIEW_<PROVIDER>.md` — findings, metrics, weaknesses, strategy scorecard and recommendations.
2. `AI_PROPOSALS_<PROVIDER>.json` — machine-readable recommendations and shadow experiments.
3. Only when evidence supports it: isolated code changes on that provider's branch with tests.
4. A clear section listing what must NOT be changed.

Production promotion requires evidence, tests, shadow validation and human approval. No AI has authority to merge or deploy live changes.
