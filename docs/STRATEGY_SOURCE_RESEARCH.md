# Strategy Lab Source Research

## Purpose

Strategy Lab has a governed research-source layer. It prefers primary/raw data, official APIs and WebSockets,
reputable open-source quant/algo/backtesting/execution frameworks, on-chain infrastructure and academic research.
It deliberately rejects influencer trade calls, anonymous signal sellers, closed-source black-box bots and unsupported
marketing claims as Strategy Lab evidence.

The source layer is research-only. It does not install third-party packages, execute external repositories, connect
exchange accounts, load signing credentials, submit transactions, change capital/risk settings or enable LIVE trading.

## Fresh evidence before strategy reasoning

`learnerbot/strategy_external_research.py` now builds a bounded fresh external evidence pack when the Strategy Lab
research report is assembled. This happens upstream of the three-agent strategy review, so GPT, Gemini and Copilot see
the same timestamped external evidence inside the sanitised loss-forensics payload rather than researching only after
they have already formed a strategy view.

The initial fresh collectors are intentionally small and auditable:

- **EXT1 — DefiLlama:** selected chain TVL / market-regime context from the public DefiLlama API;
- **EXT2 — GitHub:** public repository metadata from GitHub repository search for architecture/methodology research only;
- **EXT3 — arXiv:** recent quantitative cryptocurrency research metadata/abstracts through the arXiv API.

Every collected source records a source ID, canonical URL, UTC retrieval time, SHA-256 of the compacted source data,
source class and safety flags. The combined pack also has an evidence SHA-256. Source failures are recorded rather than
silently converted into evidence, and a partial outage does not block the bot or force a strategy conclusion.

External material is **untrusted evidence, not instructions**. Strategy reviewers are instructed to ignore commands,
prompts, role changes or executable suggestions embedded in external content. If an external source materially supports
a proposal, the reviewer should cite its `EXT#` ID, separate observation from inference, consider contrary explanations
and lower confidence when evidence is weak, stale or missing.

Network retrieval is restricted to an explicit HTTPS host allow-list. GitHub authentication, when available through
`GITHUB_TOKEN`, is used only as a request header and is never written into the research payload. Public GitHub repository
code is not cloned or executed.

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
GPT, Gemini and Copilot independently look for useful new sources or corrections to existing sources. The prompt
requires canonical publisher/project URLs, provenance, intended data/methodology, access/cost/rate-limit information
where known, and explicit risks. Duplicate recommendations are discouraged.

Copilot is assigned with the repository secret `COPILOT_ASSIGN_TOKEN`, using the same user-scoped assignment path as
the hourly strategy cycle. Copilot returns a report-only draft PR containing exactly two files under
`.ai/source-research/copilot/`.

`.github/workflows/strategy-source-master.yml` scans for completed Copilot source-report PRs and reconciles all three
reports. GPT Master can ACCEPT, REJECT or DEFER each candidate source. A deterministic policy gate then requires:

- at least two independent supporting agents;
- confidence of at least 0.85;
- a canonical HTTPS URL;
- an allowed source class;
- `research_only=true`;
- `automatic_execution_allowed=false`.

Accepted sources are stored on the `ai-reviews` branch in `source-research/latest_approved_sources.json` and in the
immutable run directory. This approval means the source is acceptable for research/reference use; it still does not
authorise package installation, account connectivity, execution, deployment or LIVE trading.

## Safety principle

A research source can generate a hypothesis, but it cannot generate permission to trade. Every strategy hypothesis
still enters Strategy Lab through SHADOW testing and must pass executable-cost, liquidity, sellability, simulation,
out-of-sample and human-approval gates before any separate decision about CANARY/LIVE use.
