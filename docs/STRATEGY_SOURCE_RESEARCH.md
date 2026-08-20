# Strategy Lab Source Research

## Purpose

Strategy Lab now has a governed research-source layer.  It prefers primary/raw data, official APIs and WebSockets,
reputable open-source quant/algo/backtesting/execution frameworks, on-chain infrastructure and academic research.
It deliberately rejects influencer trade calls, anonymous signal sellers, closed-source black-box bots and unsupported
marketing claims as Strategy Lab evidence.

The source layer is research-only.  It does not install third-party packages, execute external repositories, connect
exchange accounts, load credentials, submit transactions, change capital/risk settings or enable LIVE trading.

## Current curated catalogue

The catalogue in `learnerbot/strategy_source_catalog.py` includes the existing on-chain sources (Dune, DEX Screener,
Etherscan V2, DefiLlama, Jupiter and GitHub public code search) plus:

- primary/raw data: Binance Public Data and Tardis.dev;
- normalized market-data/API libraries: Cryptofeed and CCXT / CCXT Pro WebSockets;
- backtesting/simulation: Freqtrade, Jesse, Backtrader and VectorBT;
- execution/market-making framework reference: Hummingbot;
- EVM/on-chain infrastructure: Web3.py, ethers.js, Foundry, The Graph and Flashbots;
- quant/academic research: QuantConnect LEAN, SSRN and arXiv.

Frameworks are references and offline/SHADOW research tools; their presence in the catalogue is never permission to
run their code in production or bypass the bot's existing execution architecture.

## Continuous three-agent source discovery

`.github/workflows/strategy-source-research.yml` runs daily at 05:37 UTC and can also be started manually.
GPT, Gemini and Copilot independently look for useful new sources or corrections to existing sources.  The prompt
requires canonical publisher/project URLs, provenance, intended data/methodology, access/cost/rate-limit information
where known, and explicit risks.  Duplicate recommendations are discouraged.

Copilot is assigned with the repository secret `COPILOT_ASSIGN_TOKEN`, using the same user-scoped assignment path as
the hourly strategy cycle.  Copilot returns a report-only draft PR containing exactly two files under
`.ai/source-research/copilot/`.

`.github/workflows/strategy-source-master.yml` scans for completed Copilot source-report PRs and reconciles all three
reports.  GPT Master can ACCEPT, REJECT or DEFER each candidate source.  A deterministic policy gate then requires:

- at least two independent supporting agents;
- confidence of at least 0.85;
- a canonical HTTPS URL;
- an allowed source class;
- `research_only=true`;
- `automatic_execution_allowed=false`.

Accepted sources are stored on the `ai-reviews` branch in `source-research/latest_approved_sources.json` and in the
immutable run directory.  This approval means the source is acceptable for research/reference use; it still does not
authorise package installation, account connectivity, execution, deployment or LIVE trading.

## Safety principle

A research source can generate a hypothesis, but it cannot generate permission to trade.  Every strategy hypothesis
still enters Strategy Lab through SHADOW testing and must pass executable-cost, liquidity, sellability, simulation,
out-of-sample and human-approval gates before any separate decision about CANARY/LIVE use.
