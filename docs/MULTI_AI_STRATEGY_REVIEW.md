# Multi-AI Strategy Review

## Purpose

This lane lets multiple independent AI models review the same sanitised trading-bot evidence and lets an OpenAI synthesis step compare their recommendations against objective P&L metrics.

It is designed to reduce confirmation bias. It is **not** a mechanism for automatically deploying trading changes.

## Flow

1. The existing VPS audit loop produces the rolling transaction/loss forensics.
2. The existing GitHub exporter publishes the sanitised report to:
   - branch: `audit-status`
   - file: `latest_loss_forensics.json`
3. `.github/workflows/multi-ai-strategy-review.yml` checks once per hour for a fresh report.
4. Independent reviewers analyse the same report:
   - OpenAI
   - Google Gemini
   - Anthropic Claude (when configured)
5. Each review is saved separately.
6. OpenAI then performs an evidence-weighted synthesis. It is instructed not to use simple majority voting.
7. Results are committed to the dedicated `ai-reviews` branch. They do not modify `main`.
8. A recommendation may become a `CODE_CHANGE_CANDIDATE`, but that status means only that a tested draft PR may be prepared. It does not authorise merge or live deployment.

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

## Safety gates

The system deliberately requires:

- no named-asset buy/sell recommendation;
- no claim that profit is guaranteed;
- observed facts to be separated from inferred causes;
- every proposed change to have a falsifiable shadow test;
- at least two independent reviewers for a normal strategy/code candidate;
- objective evidence to outrank model agreement;
- `live_auto_deploy = false`;
- `draft_pr_only = true`;
- human review before anything reaches `main`.

A concrete operational defect may be classified `PAUSE_AND_FIX` from direct source evidence even when fewer reviewers are available. That exception is for fault containment, not autonomous strategy optimisation.

## GitHub Actions secrets

Add these under **Repository Settings → Secrets and variables → Actions → Repository secrets**:

- `OPENAI_API_KEY` — required for the OpenAI independent review and final synthesis.
- `GEMINI_API_KEY` — required for Gemini.
- `ANTHROPIC_API_KEY` — optional third independent reviewer. If omitted, the run records Anthropic as unavailable; it does not invent a review.

Never commit API keys, private keys, seed phrases, signing material or RPC credentials.

## Optional model variables

Under **Repository Settings → Secrets and variables → Actions → Variables**, model identifiers may be overridden with:

- `OPENAI_REVIEW_MODEL`
- `OPENAI_SYNTH_MODEL`
- `GEMINI_MODEL`
- `CLAUDE_MODEL`

The defaults are intentionally configurable because provider model names change over time.

## Review outputs

The `ai-reviews` branch contains:

- `reviews/latest_openai.json`
- `reviews/latest_gemini.json`
- `reviews/latest_anthropic.json`
- `reviews/latest_consensus.json`
- `reviews/latest_run_summary.json`
- timestamped copies under `reviews/runs/`

The workflow hashes each source report and skips an identical report, avoiding unnecessary repeated API cost.

## Implementation stage

The present lane stops at evidence-weighted consensus and a `CODE_CHANGE_CANDIDATE`. The next implementation lane should only create a separate branch/draft PR, run CI and shadow/replay tests, and attach evidence showing whether the candidate improves net P&L without worsening execution risk.

It should **not** merge to `main` automatically. In this repository, `main` is tied to the VPS deployment workflow, so automatic merging would turn an AI research opinion into a live trading change without an adequate control boundary.
