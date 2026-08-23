# Strategy Factory online research worker

## Purpose

Strategy Factory uses a third, narrowly scoped research identity for public online evidence:

`STRATEGY_RESEARCH_READONLY`

It is separate from both Claude General and Claude Coding.

- **Claude General / Strategy Factory** formulates hypotheses, identifies missing evidence and interprets validated findings.
- **Research Worker** performs bounded public read-only retrieval and returns inert structured evidence.
- **Claude Coding** implements authorised repository changes. It does not receive arbitrary raw webpages through the research worker; it may consume validated/sanitised findings and approved official-document extracts.

The worker has no repository/configuration writer, no trading primitive, no LIVE/ARMED authority, no wallet/signing primitive and no deployment primitive.

## Routing and cost control

Questions are classified before network use:

- current fees, liquidity, protocol versions, incidents, latency, market/route conditions -> `FRESH_WEB`;
- internal backtests, repository history and recorded bot evidence -> `REPO_ONLY`;
- stable external methodology/academic questions -> `CACHE_FIRST`.

The trusted ingestion layer checks the expiry-aware cache first. An expired cache entry is a miss. This avoids repeated model/web spend while preventing stale evidence being represented as current.

## Source and provenance boundary

The existing external collector remains host allow-listed and bounded. Research Worker findings carry:

- hypothesis id and source id;
- canonical HTTPS URL;
- source tier;
- publication/update date where the source exposes it;
- access time;
- normalised claim/description;
- short bounded supporting evidence excerpt;
- content hash;
- confidence;
- TTL expiry;
- disputed/core-assumption flags;
- worker identity;
- `instruction_authority: false` and `research_only: true`.

Fetched content is data, never instructions. The worker cannot execute third-party code or act on instruction-like text embedded in public content.

## Trust and storage

The worker never writes its own findings. `strategy_research_ingestion.py` is the trusted schema/ingestion boundary. It validates identity, provenance, HTTPS source URLs, source tier, TTL and all no-authority flags before an atomic write beneath the bot data directory.

This separation prevents a webpage from directly becoming a repository or runtime mutation path.

## Promotion research gates

Research gates are additive; they never replace existing Strategy Lab, MASTER, Engineering Monitor, execution, quote/simulation, positive-edge, liquidity, sellability, slippage/impact, reserve, nonce, wallet/signing, reconciliation, stop-loss or circuit-breaker controls.

- **EXPERIMENT / SHADOW:** research is advisory; exploration remains possible.
- **CANARY:** validated current evidence is required by the research gate, including at least one current tier-1/2 source. Expired or disputed findings fail closed.
- **FULL LIVE:** CANARY research conditions must still pass and the independent challenge status must be `PASS`.

A research-gate PASS is not permission to trade. It is only one prerequisite for an otherwise independently authorised promotion.

## Disputes and challenge

Same-tier conflicting evidence should be marked disputed and cannot satisfy a capital-risk research gate. The trusted layer can record a bounded challenge result (`PASS`, `FAIL`, `DISPUTED`) without changing any strategy/runtime state.

Independent challenge is reserved for capital-risk gates to avoid unnecessary AI cost during ordinary exploration.

## Dashboard/report contract

The Strategy Lab research report exposes:

- `online_research_worker`;
- `online_research_cache_hit`;
- `online_research_ingestion`;
- `online_research_freshness`;
- `research_promotion_gates` for SHADOW, CANARY and LIVE;
- the legacy `external_source_research` key for compatibility.

GPT adjudication and dashboards should consume the same structured evidence rather than separate narrative-only claims.
