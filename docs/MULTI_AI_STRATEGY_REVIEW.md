# Multi-AI Strategy Review

## Purpose

This system lets multiple independent AI models review the same sanitised trading-bot evidence, then uses an OpenAI synthesis step to compare their recommendations against objective P&L metrics. If the evidence supports a code/configuration change, a separate Codex lane may prepare a tested **draft pull request**.

It is designed to reduce confirmation bias. It does **not** automatically merge or deploy trading changes.

## End-to-end flow

1. The existing VPS audit loop produces rolling transaction/loss forensics.
2. The existing GitHub exporter publishes the sanitised report to:
   - branch: `audit-status`
   - file: `latest_loss_forensics.json`
3. `.github/workflows/multi-ai-strategy-review.yml` checks once per hour for a fresh report.
4. Independent reviewers analyse the same report:
   - OpenAI GPT
   - Google Gemini
   - Anthropic Claude (when configured)
5. Each review is saved separately.
6. OpenAI performs an evidence-weighted synthesis. It is instructed not to use simple majority voting.
7. Review results are committed to the dedicated `ai-reviews` branch, not `main`.
8. The synthesis may classify a recommendation as `CODE_CHANGE_CANDIDATE` or a directly evidenced operational defect as `PAUSE_AND_FIX`.
9. `.github/workflows/ai-candidate-implementation.yml` checks for a new eligible candidate.
10. Codex inspects the consensus, source report and repository, then may make a minimal change in a new `ai/candidate-*` branch.
11. Protected-file, diff-size, compile, import and Solana/trading-safety regression gates run before any push.
12. If the gates pass, GitHub opens a **draft PR only**. Nothing is merged or deployed automatically.

## What "profit more than losses" means

Do not optimise for the number of winning trades alone.

The primary evidence is money-weighted:

- realised net P&L after known costs;
- gross profit versus gross loss;
- profit factor (`gross profit / gross loss`) greater than 1;
- magnitude of the largest and average losses versus wins;
- execution failures and failed exits;
- fees, slippage, latency and price-impact leakage where the data exists;
- drawdown and sample size;
- persistence of the result in shadow/out-of-sample observation.

Seven winning trades and three losing trades can still be a bad strategy if the three losses are larger than the seven gains.

## Review safety gates

The review/synthesis lane requires:

- no named-asset buy/sell recommendation;
- no claim that profit is guaranteed;
- observed facts separated from inferred causes;
- every proposed change to have a falsifiable shadow test;
- at least two independent reviewers for a normal strategy/code candidate;
- objective evidence to outrank model agreement;
- `live_auto_deploy = false`;
- `draft_pr_only = true`.

A concrete operational defect may be classified `PAUSE_AND_FIX` from direct source evidence even when fewer reviewers are available. That exception is for fault containment, not autonomous strategy optimisation.

## Codex implementation boundaries

The candidate implementation lane is deliberately narrower than the AI review. Codex is instructed not to:

- submit blockchain transactions;
- change private keys, wallet addresses, seed/signing material, API/RPC credentials or Telegram authentication;
- silently increase capital, position size or risk limits;
- enable a live mode that is currently disabled;
- remove simulation, sellability, liquidity, slippage, profit, nonce, MEV, atomicity, canary, stop-loss, circuit-breaker or execution-safety controls;
- modify the live VPS deployment workflow or GitHub secret handling;
- commit, merge or deploy directly.

The workflow additionally blocks protected files, unusually broad patches (>12 files), and unusually large patches (>2,000 changed lines). It runs mandatory regression tests before pushing a candidate branch.

## GitHub Actions secrets

Add these under **Repository Settings → Secrets and variables → Actions → Repository secrets**:

- `OPENAI_API_KEY` — required for the OpenAI independent review, final synthesis and Codex implementation agent.
- `GEMINI_API_KEY` — required for Gemini.
- `ANTHROPIC_API_KEY` — optional third independent reviewer. If omitted, the run records Anthropic as unavailable; it does not invent a review.

Never commit API keys, private keys, seed phrases, signing material or RPC credentials.

## Optional model variables

Under **Repository Settings → Secrets and variables → Actions → Variables**, model identifiers may be overridden with:

- `OPENAI_REVIEW_MODEL`
- `OPENAI_SYNTH_MODEL`
- `GEMINI_MODEL`
- `CLAUDE_MODEL`

Current defaults in the workflow are GPT-5.6 Terra for the independent OpenAI review, GPT-5.6 for synthesis, Gemini 3.5 Flash and Claude Sonnet 5. Model identifiers remain configurable because provider offerings change.

## Review outputs

The `ai-reviews` branch contains:

- `reviews/latest_openai.json`
- `reviews/latest_gemini.json`
- `reviews/latest_anthropic.json`
- `reviews/latest_consensus.json`
- `reviews/latest_run_summary.json`
- timestamped copies under `reviews/runs/`

The workflow hashes each source report and skips an identical report, avoiding unnecessary repeated API cost.

## Candidate implementation deduplication

The Codex lane derives a short identifier from the source-report hash. It will not repeatedly implement the same candidate if a corresponding `ai/candidate-*`, `ai/reviewed-*` or `ai/failed-*` marker exists. A successful implementation becomes an `ai/candidate-*` draft PR branch. A safe no-op is recorded as `ai/reviewed-*`.

## Final control boundary

`main` remains outside the autonomous AI lane. This matters because the repository's existing deployment workflow is tied to `main`. An AI-generated candidate can therefore be reviewed, challenged, replayed/shadow-tested and rejected without changing the live bot.

Only a separate human decision to merge a tested PR can cross that boundary. Profitability is never guaranteed; the objective is to make changes falsifiable and evidence-led, and to reduce avoidable realised losses rather than merely increase the apparent win rate.
