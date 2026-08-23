# Engineering Monitor, Strategy Monitor and Strategy Factory operating model

## Purpose

This is the operational control plane connecting monitoring evidence to the Strategy Factory. It does not replace any existing deterministic execution, risk, wallet/signing, MASTER approval, research provenance or promotion gate.

The system has three distinct responsibilities:

- **Engineering Monitor** asks whether the bot, infrastructure and execution path are technically correct, reliable, fast enough and economically sensible.
- **Strategy Monitor** asks whether each exact strategy/version/chain is producing durable money-weighted net results after recorded costs and whether the strategy is participating appropriately in genuinely eligible opportunities.
- **Strategy Factory** receives structured problems/opportunities, challenges the diagnosis with independent agents, researches or designs a falsifiable SHADOW response, and sends protected changes to human approval.

The monitors diagnose and escalate. They do not silently repair LIVE trading, loosen thresholds or increase capital.

## End-to-end process

1. **Observe** — collect deterministic evidence from Strategy Lab scorecards, exact strategy/Git attribution, execution failures, execution latency, disk/resource state, bandwidth counters and other existing health/forensic sources.
2. **Separate cause classes** — classify the issue as `STRATEGY`, `MARKET`, `EXECUTION`, `INFRASTRUCTURE`, `DATA` or `RESEARCH`. A losing trade is not automatically a strategy defect.
3. **Measure materiality** — compare money-weighted economics, profit factor, win/loss distribution, eligible opportunity participation, execution failures and latency against adequate samples/baselines.
4. **Create a Problem/Opportunity Package** — every material conclusion carries evidence, scope, strategy/version where applicable, severity, recommended investigation and an explicit acceptance/falsification test.
5. **Factory independent review** — a severity-sized panel independently challenges the package. P0/P1 uses all seven agents; lower severity uses a smaller rotating panel to control cost while retaining GPT and at least three reviewers when a SHADOW draft could result.
6. **GPT adjudication** — GPT is the final evidence adjudicator. This is not majority voting. Deterministic evidence and reproducibility outrank model agreement.
7. **Action boundary** — the Factory may choose no action, continued monitoring, more research, a bounded SHADOW-only draft, or human approval. Engineering/runtime/workflow/LIVE changes cannot use the automatic SHADOW draft path.
8. **SHADOW implementation** — a supported Strategy-Lab-only change with at least three completed reviews may enter the existing Strategy Room draft bridge. The worker has a file allow-list, creates a draft PR only and cannot merge or deploy.
9. **Promotion** — new/materially changed strategies remain subject to the existing `SHADOW -> PROMOTION_CANDIDATE -> MASTER CANARY APPROVAL -> CANARY -> READY FOR FULL LIVE -> MASTER FULL-LIVE APPROVAL -> FULL LIVE` policy.
10. **Continuous review** — FULL LIVE remains monitored. Material deterioration creates a fresh package for rework/replacement rather than silently increasing risk or forcing trades.

## Cadence

### Engineering Monitor

- **Every 15 minutes:** deterministic infrastructure/execution observation. This is a health/measurement loop, not a strategy optimisation loop.
- **Daily:** one rotating member of GPT, Claude, Gemini, DeepSeek, Grok, Kimi and Copilot reviews deterministic Engineering evidence. The reviewer may escalate a supported problem to the Factory but cannot edit/deploy/trade.
- **Weekly:** all seven agents participate in the joint Monitor/Factory Council. The existing deep full-repository engineering audit remains complementary because code-level inspection and deterministic tests are stronger evidence than conversational review alone.

### Strategy Monitor

- **Hourly:** deterministic Strategy Lab portfolio evaluation and exact strategy/version evidence are converted into structured packages when material.
- The existing cost-gated multi-agent strategy review remains a deeper AI review lane; no paid provider call is required merely because a clock tick occurred.
- **Daily public-source research:** the existing read-only Strategy Research/source-research workflow searches for current tools, APIs, academic methods and infrastructure evidence with provenance/freshness controls.

### Strategy Factory

- **Hourly:** review up to three newly queued packages, highest severity first. The panel size scales with severity.
- **Weekly:** all seven agents jointly challenge current findings and, importantly, challenge the monitors themselves for blind spots, stale assumptions and missing measurements.
- The Factory is expected to improve its research questions and monitoring coverage over time. That does not grant it authority to mutate protected runtime paths.

## KPI hierarchy

The primary objective is **durable money-weighted net profitability after all recorded costs**, subject to correctness and safety.

The dashboard also tracks the operator's three requested win/loss views:

1. winning percentage / win rate;
2. quantity of wins versus losses;
3. gross value of wins versus gross value of losses.

Those are important diagnostic KPIs, but they are subordinate to net economics and profit factor. A strategy can have many small wins and a few catastrophic losses; therefore a high win rate cannot justify a money-losing strategy.

Other required measures include eligible opportunities, participation, fees, slippage/impact, execution failures, largest loss/drawdown, realised-versus-modelled edge, latency, RPC/provider behaviour, infrastructure cost, disk utilisation and **bandwidth usage**.

## Bandwidth and infrastructure

Bandwidth is measured from non-loopback host counters and reported as deltas/rates. It must not be described as bot-attributed traffic unless process-level attribution exists, and it must not be called excessive until the actual hosting/provider plan limit is known.

Infrastructure recommendations require measured same-workload comparison. Geography, advertised CPU frequency or a provider marketing page is not proof that a replacement server is faster for this bot.

## Research and freshness

Public-current claims are routed to the existing read-only research lane. Research output is inert evidence with provenance, source tier, freshness/TTL and dispute handling. Expired or disputed evidence cannot satisfy a capital-risk promotion gate.

Research can improve hypotheses and measurements. It is never permission to trade.

## Authority matrix

| Function | Observe/report | Public research | Propose SHADOW | Draft SHADOW PR | Merge/deploy | Change LIVE/capital/risk | Wallet/signing |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Engineering Monitor | Yes | Recommend | No | No | No | No | No |
| Strategy Monitor | Yes | Recommend | Yes | No | No | No | No |
| Strategy Factory | Yes | Yes | Yes | Yes, allow-listed draft only | No | No | No |
| MASTER/operator protected path | Yes | Yes | Yes | Yes | Existing authorised path only | Explicit approval only | Existing protected path only |

## Failure handling

- Missing/stale evidence means **insufficient evidence**, not invented values.
- Strategy losses with execution faults are escalated for Engineering root-cause analysis before strategy thresholds are changed.
- Material execution/infrastructure regressions can be quarantined by existing deterministic safety controls; the Monitor/Factory layer does not invent a new trading bypass.
- A failed/blocked AI provider is recorded as unavailable. Other agents' agreement is never fabricated.
- A Factory review that indicates an engineering, workflow, LIVE, capital, risk, wallet/signing or safety change is marked `HUMAN_APPROVAL_REQUIRED`.

## Operational implementation

The implementation is deliberately split:

- `learnerbot/monitor_factory_pipeline.py` — deterministic findings, deduplication, KPI aggregation, packages and Engineering/Strategy observation.
- `scripts/monitor_factory_operations.py` — rotating daily review, severity-sized Factory panels, GPT adjudication, seven-agent weekly challenge and the existing Strategy Room draft bridge.
- `.github/workflows/monitor-factory-operations.yml` — self-hosted schedules against the production data directory and local seven-agent WebSocket bus.

The GitHub workflow has read-only repository permission. AI review cannot mutate its checkout, and the workflow fails if an unexpected repository change appears.
