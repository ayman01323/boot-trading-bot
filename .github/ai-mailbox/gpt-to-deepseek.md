GPT_TO_DEEPSEEK
message_id: 2026-08-27T16-03-opportunity-drought-deepseek
status: REQUEST
priority: P0
subject: Diagnose current trading opportunity drought

P0 DIAGNOSTIC — why are valid trading OPPORTUNITIES still scarce?

Current architecture evidence (27 Aug 2026):
- Central REJECTED OPPORTUNITY reporting is deployed.
- Active SiBot1 strategy engines are GPT, Gemini and Grok. LearnerBot/Claude scanner/runtime market rejections are also bridged into the same central queue. DeepSeek/Kimi/Copilot are review advisers, not current SiBot1 trading engines.
- Strategy no-intent market events are now reported when an engine exposes a rejection reason; Gemini/Grok expose per-reason prefilter_rejections and GPT has explicit derived reasons.
- PoolCheck non-PASS/non-SHADOW_ONLY verdicts are published.
- SiBot1 ENTRY failures and full-power scanner rejection CSVs are bridged into the queue.
- Production enables BOOT_REJECTED_OPPORTUNITY_ENABLED=1; SiRisky separately consumes the rejected queue.

Earlier 26 Aug evidence (before later fixes) showed GPT/Base events but zero signals, routes=0/eligible=0, edge/quote/graph rejections, provider_rate_limit and Alchemy HTTP 429. Bounded route rotation and scanner RPC failover were subsequently installed. Do not simply repeat those old causes unless you show how they can persist after the fixes.

Return: (1) CURRENT_CAUSES ranked by confidence, distinguishing genuine no-edge from discovery/scanner undercoverage, quote/RPC freshness, strategy-threshold rejection, PoolCheck/risk rejection and execution-bridge rejection; (2) EVIDENCE_TO_CHECK now in production with exact counters/queue fields/log metrics/chain-engine splits; (3) REPORTING_GAPS where a genuine opportunity refusal can still be silently dropped; (4) SAFE_FIX, smallest changes that improve legitimate coverage without manufacturing trades; (5) ACCEPTANCE_CRITERIA proving discovery health even during a zero-executable-opportunity period; (6) whether the design is sufficient to say “all trading bots report refused opportunities”, with necessary qualification.

Do not weaken PoolCheck, rug/honeypot/sellability, liquidity, impact/slippage, freshness, simulation, signer/wallet, capital, LIVE/ARMED or profit/edge safeguards merely to generate activity.