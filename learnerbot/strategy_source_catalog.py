from __future__ import annotations

"""Governed research-source catalogue for Strategy Lab.

The catalogue is RESEARCH ONLY.  Nothing here is an execution connector, credential
loader, package installer, trading adapter, or permission to run third-party code.
Sources may contribute ideas, public/raw data specifications, backtesting methods, or
execution-engine design references.  Any strategy derived from them still starts in
SHADOW and remains subject to the bot's existing cost, sellability, simulation and
human-approval gates.
"""

SOURCE_DISCOVERY_POLICY = {
    "objective": (
        "Continuously improve Strategy Lab research inputs using primary/raw market data, "
        "official APIs/WebSockets, reputable open-source quant/algo frameworks and academic research."
    ),
    "preferred_source_classes": [
        "PRIMARY_RAW_DATA",
        "OFFICIAL_API_WEBSOCKET",
        "OPEN_SOURCE_DATA_LIBRARY",
        "OPEN_SOURCE_BACKTEST_FRAMEWORK",
        "OPEN_SOURCE_EXECUTION_FRAMEWORK",
        "ONCHAIN_INFRASTRUCTURE",
        "ACADEMIC_RESEARCH",
    ],
    "avoid": [
        "influencer_or_social_media_trade_calls",
        "anonymous_signal_services",
        "closed_source_ready_made_bots_without_auditable_code",
        "marketing_claims_without_reproducible_data_or_methodology",
        "automatic_execution_of_third_party_repository_code",
    ],
    "new_source_requirements": [
        "prefer official publisher/project documentation or the canonical repository",
        "identify publisher/maintainer and canonical URL",
        "state exact data or methodology contributed to Strategy Lab",
        "record access model, authentication, rate limits and material cost where known",
        "record history depth, latency/granularity and reproducibility limits where relevant",
        "record licence/terms/security and data-quality risks before integration",
        "separate research/reference use from any execution connectivity",
        "never copy credentials, signing material, executable snippets or live settings into an AI report",
    ],
    "consensus": {
        "minimum_independent_agents": 2,
        "master_reconciliation_required": True,
        "automatic_live_use": False,
        "automatic_package_install": False,
        "automatic_external_code_execution": False,
        "automatic_exchange_account_connection": False,
        "approved_use": "READ_ONLY_RESEARCH_OR_SHADOW_DESIGN_ONLY",
    },
}


def _source(
    name: str,
    source_class: str,
    url: str,
    use: str,
    *,
    chains: list[str],
    access: str,
    trust_basis: str,
    safe_mode: str = "READ_ONLY_RESEARCH",
    notes: str = "",
) -> dict:
    return {
        "tool": name,
        "source_class": source_class,
        "url": url,
        "chains": chains,
        "use": use,
        "access": access,
        "trust_basis": trust_basis,
        "safe_mode": safe_mode,
        "notes": notes,
    }


CURATED_STRATEGY_SOURCES = [
    # Existing on-chain / DEX research sources.
    _source(
        "Dune",
        "PRIMARY_RAW_DATA",
        "https://docs.dune.com/",
        "Query public on-chain DEX trades, wallet cohorts, routes and realised outcomes for reproducible research.",
        chains=["EVM", "SOLANA"],
        access="API/query platform; key or plan may be required for automation",
        trust_basis="canonical Dune documentation",
    ),
    _source(
        "DEX Screener API",
        "OFFICIAL_API_WEBSOCKET",
        "https://docs.dexscreener.com/api/reference",
        "Discover pairs and measure liquidity, volume, transactions, price change and pool age for shadow feature research.",
        chains=["EVM", "SOLANA"],
        access="public API subject to published limits",
        trust_basis="canonical DEX Screener API documentation",
    ),
    _source(
        "Etherscan API V2",
        "OFFICIAL_API_WEBSOCKET",
        "https://docs.etherscan.io/",
        "Reconstruct EVM account transactions and contract/route interactions for cohort and execution research.",
        chains=["EVM"],
        access="API key",
        trust_basis="canonical Etherscan documentation",
    ),
    _source(
        "DefiLlama",
        "PRIMARY_RAW_DATA",
        "https://defillama.com/docs/api",
        "Measure chain/protocol TVL, DEX volume, fees and activity regimes for regime-conditioned strategy testing.",
        chains=["EVM", "SOLANA"],
        access="public datasets; paid API options may exist",
        trust_basis="canonical DefiLlama API documentation",
    ),
    _source(
        "Jupiter",
        "OFFICIAL_API_WEBSOCKET",
        "https://dev.jup.ag/",
        "Obtain Solana quote/route information for executable-edge, impact, sellability and route-quality SHADOW research.",
        chains=["SOLANA"],
        access="follow current Jupiter API requirements",
        trust_basis="canonical Jupiter developer documentation",
        safe_mode="QUOTE_AND_SIMULATION_ONLY",
    ),
    _source(
        "GitHub public code search",
        "OPEN_SOURCE_DATA_LIBRARY",
        "https://docs.github.com/en/search-github/searching-on-github/searching-code",
        "Study public algorithm, simulator, connector and risk-control architecture without executing untrusted code.",
        chains=["GENERAL"],
        access="public search; authenticated access may provide higher limits",
        trust_basis="GitHub canonical documentation plus canonical project repositories",
        safe_mode="READ_ONLY_IDEA_EXTRACTION",
    ),

    # Primary/raw CEX market data and normalized feeds requested for the Lab.
    _source(
        "Binance Public Data",
        "PRIMARY_RAW_DATA",
        "https://data.binance.vision/",
        "Download official historical trades, aggregate trades and kline archives for reproducible CEX backtests and feature research.",
        chains=["CEX"],
        access="public bulk downloads",
        trust_basis="official Binance public market-data archive",
    ),
    _source(
        "Tardis.dev",
        "PRIMARY_RAW_DATA",
        "https://tardis.dev/",
        "High-resolution historical trades, incremental/snapshot order books, liquidations, quotes and derivatives data across exchanges.",
        chains=["CEX"],
        access="commercial API/datasets; sample data available",
        trust_basis="provider documentation and documented exchange WebSocket capture methodology",
    ),
    _source(
        "Cryptofeed",
        "OPEN_SOURCE_DATA_LIBRARY",
        "https://github.com/bmoscon/cryptofeed",
        "Normalize real-time public exchange WebSocket feeds for trades, books and market events used in research adapters.",
        chains=["CEX"],
        access="open-source Python library; exchange endpoints remain subject to exchange terms",
        trust_basis="canonical open-source repository maintained by the project",
        safe_mode="READ_ONLY_MARKET_DATA_LIBRARY",
    ),
    _source(
        "CCXT",
        "OPEN_SOURCE_DATA_LIBRARY",
        "https://github.com/ccxt/ccxt",
        "Reference normalized exchange REST/WebSocket market-data and trading APIs; use public data interfaces for research unless separately approved.",
        chains=["CEX"],
        access="open-source library; exchange credentials only for separately approved execution integrations",
        trust_basis="canonical CCXT repository and manual",
        safe_mode="PUBLIC_MARKET_DATA_AND_INTERFACE_RESEARCH",
    ),
    _source(
        "CCXT Pro / WebSocket API",
        "OPEN_SOURCE_DATA_LIBRARY",
        "https://github.com/ccxt/ccxt/wiki/ccxt.pro.manual",
        "Study normalized low-latency WebSocket order-book, trade and user-data interfaces for execution-model design.",
        chains=["CEX"],
        access="follow current CCXT licensing/distribution and exchange requirements",
        trust_basis="canonical CCXT WebSocket documentation",
        safe_mode="INTERFACE_RESEARCH_ONLY",
    ),

    # Backtesting and simulation frameworks.
    _source(
        "Freqtrade",
        "OPEN_SOURCE_BACKTEST_FRAMEWORK",
        "https://www.freqtrade.io/",
        "Reference event-aware crypto backtesting, dry-run/paper trading, strategy analysis and optimisation workflows.",
        chains=["CEX"],
        access="open source",
        trust_basis="canonical Freqtrade documentation/repository",
        safe_mode="REFERENCE_AND_OFFLINE_VALIDATION_ONLY",
    ),
    _source(
        "Jesse",
        "OPEN_SOURCE_BACKTEST_FRAMEWORK",
        "https://docs.jesse.trade/",
        "Reference clean strategy/execution separation, historical import and event-driven backtesting patterns.",
        chains=["CEX"],
        access="open-source framework with optional services",
        trust_basis="canonical Jesse documentation/repository",
        safe_mode="REFERENCE_AND_OFFLINE_VALIDATION_ONLY",
    ),
    _source(
        "Backtrader",
        "OPEN_SOURCE_BACKTEST_FRAMEWORK",
        "https://www.backtrader.com/",
        "General-purpose event-driven Python backtesting reference for execution-timing and order-model experiments.",
        chains=["GENERAL"],
        access="open source",
        trust_basis="canonical Backtrader project documentation",
        safe_mode="REFERENCE_AND_OFFLINE_VALIDATION_ONLY",
    ),
    _source(
        "VectorBT",
        "OPEN_SOURCE_BACKTEST_FRAMEWORK",
        "https://vectorbt.dev/",
        "Fast vectorized hypothesis screening and parameter-space exploration before event-driven/forward SHADOW validation.",
        chains=["GENERAL"],
        access="open-source/community and commercial variants",
        trust_basis="canonical VectorBT documentation",
        safe_mode="OFFLINE_HYPOTHESIS_SCREENING_ONLY",
    ),

    # Execution-engine and market-making framework references.
    _source(
        "Hummingbot",
        "OPEN_SOURCE_EXECUTION_FRAMEWORK",
        "https://hummingbot.org/docs/",
        "Reference market-making, CLOB/AMM connectors, execution architecture and Quants Lab research patterns.",
        chains=["CEX", "EVM", "SOLANA"],
        access="open source",
        trust_basis="Hummingbot Foundation canonical documentation/repositories",
        safe_mode="REFERENCE_ONLY_NO_AUTOMATIC_CONNECTOR_USE",
    ),

    # On-chain / DeFi infrastructure and execution research.
    _source(
        "Web3.py",
        "ONCHAIN_INFRASTRUCTURE",
        "https://web3py.readthedocs.io/",
        "Reference canonical Python EVM RPC, transaction and contract interaction patterns for simulation/execution design.",
        chains=["EVM"],
        access="open source",
        trust_basis="canonical Web3.py documentation/repository",
        safe_mode="REFERENCE_AND_TESTING_ONLY",
    ),
    _source(
        "ethers.js",
        "ONCHAIN_INFRASTRUCTURE",
        "https://docs.ethers.org/",
        "Reference JavaScript/TypeScript EVM provider, contract and transaction interfaces.",
        chains=["EVM"],
        access="open source",
        trust_basis="canonical ethers documentation/repository",
        safe_mode="REFERENCE_AND_TESTING_ONLY",
    ),
    _source(
        "Foundry",
        "ONCHAIN_INFRASTRUCTURE",
        "https://getfoundry.sh/",
        "Use as a reference/test toolkit for deterministic Solidity unit, fork and simulation testing of smart-contract interactions.",
        chains=["EVM"],
        access="open source",
        trust_basis="canonical Foundry documentation/repository",
        safe_mode="LOCAL_OR_CI_TESTING_ONLY",
    ),
    _source(
        "The Graph",
        "OFFICIAL_API_WEBSOCKET",
        "https://thegraph.com/docs/",
        "Query indexed protocol swaps, pools, liquidity and fees through subgraphs for historical/on-chain research.",
        chains=["EVM", "SOLANA"],
        access="GraphQL/subgraph endpoints; pricing and coverage vary",
        trust_basis="canonical The Graph documentation plus individual subgraph provenance",
    ),
    _source(
        "Flashbots",
        "ONCHAIN_INFRASTRUCTURE",
        "https://docs.flashbots.net/",
        "Research private transaction submission, MEV protection and builder/relay economics for EVM execution modelling.",
        chains=["EVM"],
        access="public documentation and network endpoints subject to current requirements",
        trust_basis="canonical Flashbots documentation/repositories",
        safe_mode="EXECUTION_ARCHITECTURE_RESEARCH_ONLY",
    ),

    # Quant research / academic sources.
    _source(
        "QuantConnect LEAN",
        "OPEN_SOURCE_BACKTEST_FRAMEWORK",
        "https://www.quantconnect.com/docs/",
        "Reference open-source LEAN algorithms, backtesting methodology and cross-asset quant research ideas for crypto adaptation.",
        chains=["GENERAL", "CEX"],
        access="open-source LEAN engine plus cloud platform",
        trust_basis="canonical QuantConnect documentation/repository",
        safe_mode="REFERENCE_AND_OFFLINE_RESEARCH_ONLY",
    ),
    _source(
        "SSRN",
        "ACADEMIC_RESEARCH",
        "https://www.ssrn.com/",
        "Find working papers on crypto momentum, market microstructure, arbitrage and risk; validate claims independently before Strategy Lab use.",
        chains=["GENERAL"],
        access="paper abstracts/downloads subject to publisher availability",
        trust_basis="paper authorship, methodology and citations; not automatically peer reviewed",
    ),
    _source(
        "arXiv",
        "ACADEMIC_RESEARCH",
        "https://arxiv.org/",
        "Find reproducible quantitative/market-microstructure research and derive falsifiable SHADOW hypotheses.",
        chains=["GENERAL"],
        access="public preprint repository",
        trust_basis="paper methodology/data/code where provided; preprint status must be recorded",
    ),
]


def source_catalogue() -> dict:
    return {
        "policy": SOURCE_DISCOVERY_POLICY,
        "sources": [dict(row) for row in CURATED_STRATEGY_SOURCES],
        "source_count": len(CURATED_STRATEGY_SOURCES),
        "live_execution_authorised": False,
        "third_party_code_auto_execution": False,
    }
