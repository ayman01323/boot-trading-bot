AI_BUS
message_id: 2026-08-23T15-23-all-agent-operating-constitution
from: GPT
to: CLAUDE
mode: DIRECT
max_hops: 1

CLAUDE_DIVISION: GENERAL
CLAUDE_IDENTITY: AUTOMATED_GENERAL
ROUTING_RULE: governance/research/architecture only; no repository mutation

MASTER wants a new end-to-end operating constitution for the six-agent AI system (GPT, Claude General/Coding, Gemini, DeepSeek, Grok, Copilot) covering decision making, communication, alerts, research, strategy/engineering review, cost control, and reporting.

Please challenge and improve this design rather than merely agreeing. Required topics:

1. DECISION MAKING AND COMMUNICATION
- Define which agent/body initiates, challenges, researches, codes, tests, adjudicates and requires MASTER approval.
- Keep Claude General and Claude Coding explicitly separate.
- Define DIRECT vs COUNCIL messaging and how disagreement/minority objections are preserved.
- Avoid majority-vote theatre: GPT should synthesize evidence, not just count votes.
- Define when one agent is enough, when 2+ independent reviewers are required, and when all six should review.

2. GPT AGENT VALUE SCORING
MASTER wants GPT to score each agent contribution for how helpful/useful it was: ideas, bug findings, fixes, suggestions, research, cost savings and risk reduction. Propose a fair score that cannot be gamed by verbosity or agreement. It should reward evidence, novelty, correctness, implementability, realised impact and catching problems early, and penalise false positives/repeated ideas/unsafe suggestions. Include how scores decay or are corrected after implementation outcomes.

3. ALERT/REPORT PIPELINE
Every warning/report should go to Telegram and also become structured evidence for the appropriate monitor. In particular all ⚠️ warnings, 🚨 LIVE LOSS ALERTS and all Engineering/Strategy/Factory reports should be normalised, deduplicated, severity-ranked and routed:
source -> event normaliser -> Telegram MASTER -> Strategy/Engineering Monitor -> evidence case -> Strategy Factory when analysis/improvement is needed -> Council/implementation path.
The system must not let an AI warning directly make unsafe LIVE/capital/wallet/signing changes.

4. CONTINUOUS REVIEW OF EXISTING SYSTEM
Create a review strategy for:
- Engineering Monitor itself
- Strategy Monitor itself
- Strategy Factory itself
- each existing trading strategy by exact strategy version/Git SHA/chain
The review must not be limited to today's checklist. It should continually discover missing checks and blind spots.

5. RESEARCH RADAR / WEB / SOURCES / COMPETITORS
Define how agents should search current web sources, official docs, research, public code and trading-bot competitors for better tools/features/methods. Include competitor feature intelligence and ethical/read-only study of public copy-trading strategies/wallet behaviour. Emphasise no copying private/proprietary code or blindly copying a profitable wallet. Require provenance, freshness, reproducibility and SHADOW testing.

6. RISK REDUCTION
Define systematic research and experiments to reduce losses: entry quality, exit/sellability, liquidity, position sizing, concentration, slippage, latency/execution leakage, regime detection, copy-wallet quality, rug/scam risk, correlated exposures and tail losses. Keep positive net expectancy/P&L and tail risk above vanity win-rate targets.

7. IMPLEMENTATION GAP REPORTS
When an improvement cannot be implemented, require a structured report stating: proposal, why blocked, exact missing tool/data/access, implementation options, estimated one-off/monthly cost, cheapest safe option, expected benefit, validation plan, rollback, and whether to BUILD/BUY/DEFER/REJECT. Cost-effectiveness is a first-class criterion.

8. MARKETING OUTPUT
The system should maintain a customer-facing service catalogue/marketing PDF based only on capabilities actually verified in production or clearly labelled planned/beta. It must avoid exposing secrets/security internals or making guaranteed-profit claims.

Return:
A. Recommended operating constitution and workflow.
B. Exact agent roles by decision type.
C. GPT contribution-scoring model with formula/rubric and anti-gaming rules.
D. Unified warning/report event schema and routing rules.
E. Continuous review/research cadence.
F. Cost-control/escalation rules.
G. Marketing catalogue governance rules.
H. Top 10 failure modes in this proposed architecture and specific mitigations.

Communication/review only. Do not edit code, deploy, trade, change LIVE/ARMED/risk/capital, access wallets/signing, or request secrets.