GPT_TO_GROK
message_id: 2026-08-28T16-59-grok-bot-no-trading-diagnostic
status: REQUEST
priority: P0
subject: Grok known-assets bot — why no trading

DIAGNOSTIC / REVIEW ONLY. Do not change code, deploy, restart services, alter risk settings, arm LIVE execution, access private keys, or place any trade.

The owner reports that the Grok bot is not trading. Find the exact current reason(s), using the deployed/current repository and runtime evidence available to you. Do not give generic possibilities where a concrete check is possible.

Repository: https://github.com/ayman01323/boot-trading-bot
Bot root: testingbots/grok_known_assets_bot/
Target deployment directory: /home/ayman01323/BOOT/testingbots/grok_known_assets_bot

Check the complete pipeline in order:

1. RUNTIME / MODE
- Is the service/process actually running?
- Which commit/version is deployed?
- Is it PAPER/SHADOW only, or does any LIVE execution path actually exist?
- Confirm whether the current CLI still refuses `run` without `--paper`.
- Confirm whether the bot has any signer/broadcast/order-placement capability at all.

2. DATA FEEDS
- Are real provider collectors connected through SafeSnapshotBuilder, or is the bot still depending on sample/static snapshots?
- Are snapshots arriving continuously for enabled allow-listed assets?
- For each asset, report last snapshot timestamp/age and whether freshness validation passes.
- Check Jupiter executable entry quote and reverse sell route availability where applicable.
- Check pool/RugCheck binding evidence for non-native assets.

3. ASSET CONFIGURATION
- List every enabled asset actually eligible to trade.
- Confirm canonical chain + contract/mint addresses.
- Identify any disabled placeholders or config validation failures.

4. ENTRY FUNNEL — LAST 24 HOURS
Give counts for each stage, not just final trades:
- snapshots observed
- assets passing allow-list/config
- fresh quote
- reverse sell path
- liquidity pass
- volume pass
- spread pass
- impact pass
- 15m trend pass
- 5m momentum pass
- 1m reversal pass
- positive edge after round-trip costs
- Grok research QUALIFY / REJECT
- host entry accepted
- PAPER position opened
- PAPER position closed
For every rejection stage, include count and the top exact rejection reasons with representative evidence.

5. RISK / BREAKERS / SIZING
- Check daily realised-loss breaker, consecutive-loss breaker, max positions, gross exposure, chain exposure, liquidity participation, quote age, and any zero/invalid size outcome.
- Confirm whether a breaker or existing open position is blocking entries.

6. RESEARCH GATE
- Report current `research-min-confidence` and actual recent Grok confidence/quality scores.
- Compare how many entries would pass with the research gate versus `--no-research-gate` in PAPER-only analysis. Do not disable the gate in production.

7. EXECUTION BOUNDARY
- Distinguish clearly between:
  A) no opportunities qualifying for PAPER entry;
  B) PAPER entries qualify but runtime is not consuming data;
  C) PAPER trading works but there is intentionally no LIVE execution implementation;
  D) a concrete runtime/configuration defect.

8. ROOT CAUSE AND FIX ORDER
Return the smallest evidence-based fix sequence needed to make the bot produce valid PAPER trades first. Do not recommend weakening risk controls merely to force trades.

Return exactly:
1. STATUS: RUNNING / STOPPED / UNKNOWN.
2. DEPLOYED_COMMIT: <sha or UNKNOWN>.
3. MODE: PAPER_ONLY / SHADOW_ONLY / LIVE_CAPABLE / UNKNOWN.
4. LAST_24H_FUNNEL: stage counts table.
5. TOP_BLOCKERS: ranked list with evidence.
6. ROOT_CAUSE: one concise paragraph.
7. FIXES: P0/P1 ordered actions.
8. LIVE_NOTE: whether LIVE trading is technically implemented at all.
9. EVIDENCE: commands/files/log excerpts used.

Do not claim a runtime fact unless you actually observed it. If runtime access is unavailable, state that explicitly and separate repository-proven facts from runtime-unknown facts.