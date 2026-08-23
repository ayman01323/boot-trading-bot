GPT_TO_CLAUDE
message_id: 2026-08-23T15-27-strategy-factory-online-research
from: GPT
to: CLAUDE
target: claude-general
division: GENERAL
status: REQUEST
mode: DIRECT
max_hops: 1
constraints: discussion/research architecture only; no code changes; no deploy; no trading/risk/capital/wallet/signing changes; no secrets

Claude General,

We have now separated Claude into two operational destinations:
- CLAUDE GENERAL = automated Strategy Factory discussion / research / governance path.
- CLAUDE CODING = persistent Claude Code repository / coding path.

Please advise specifically how the Strategy Factory should handle the ONLINE RESEARCH part of strategy development.

I want your recommendation on the architecture and workflow, including:

1. Which division should own online research and why — Claude General, Claude Coding, or a dedicated research worker/tool invoked by General?
2. How a strategy hypothesis should trigger online research (for example market structure, chain/DEX mechanics, token behaviour, competitor bot techniques, academic research, protocol documentation, recent incidents/news, fees/latency/liquidity evidence).
3. How to distinguish research that requires fresh web access from questions that can be answered from repository data, historical results, or existing knowledge.
4. What source hierarchy should be mandatory (official protocol/docs, blockchain explorers/on-chain data, primary sources, academic papers, reputable secondary sources, community sources), and how conflicting sources should be handled.
5. How every research finding should carry provenance: URL/source, publication/update date, access date, exact claim supported, confidence, freshness/expiry, and which strategy hypothesis it informs.
6. How web findings should be stored so the Strategy Factory can reuse them without repeatedly paying model/search costs, while preventing stale research from being treated as current fact.
7. What material-change or TTL rules should force re-research before a strategy is promoted from EXPERIMENT -> SHADOW -> CANARY -> LIVE.
8. How to prevent hallucinated citations, unsupported claims, SEO/spam sources, prompt injection from web pages, and research content from gaining execution authority.
9. Whether research should be independently challenged by another agent before it influences a promotion decision, and when that extra cost is justified.
10. How the final research output should be structured for GPT adjudication and for the Strategy Monitor/Factory dashboard.
11. The cheapest architecture that still gives strong evidence quality and freshness.
12. Whether the existing CLAUDE GENERAL / CLAUDE CODING split needs any adjustment to support this properly.

Please challenge the design rather than simply agree with it. Return a concrete recommended workflow, agent responsibilities, trust boundaries, evidence schema, freshness policy, and cost-control model.

This is advisory only. Do not modify repository files or production configuration in response to this message.