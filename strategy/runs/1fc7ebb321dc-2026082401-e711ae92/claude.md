# Claude strategy review

Architecture-only review (no runtime forensics available this cycle). Primary LIVE strategy is single-family leader-wallet copy-trading on both chains, with a documented diversification goal unmet since the alternative quant signal family remains SHADOW-only. Chain-specific execution cost controls (slippage, gas/priority fees, liquidity/sellability gates) exist and are incident-derived (HOOD post-mortem), but two measurement/infrastructure gaps could distort durable NET P&L: EVM gross-only P&L fallback states, and absent EVM nonce dedupe/retry logic. All proposals are architecture-level hypotheses requiring SHADOW evidence before any live change; no live-readiness or profitability claim is made.

## RESEARCH_MORE — leader-copy-evm
If a material share of EVM closed trades fall into the gross-only proof states, reported net P&L is upward-biased for that subset since real gas/builder costs were paid but never subtracted, directly undermining the objective of durable money-weighted NET P&L after fees.

## SHADOW_MORE — cross-chain-quant-signal-family
Current live capital allocation is concentrated in a single signal family (leader-copy) structurally correlated to tracked wallets' own edge decay, detection, or front-running risk. The documented alternative quant signal family remains unpromoted SHADOW-only, so the bot's own stated diversification goal is unmet, leaving no fallback signal source if leader-copy edge degrades.

## IMPROVE — execution-infrastructure-evm
Pending-nonce reads without a dedupe/idempotency layer create a plausible path to double-submission or nonce collisions if a transaction is slow to confirm and a retry path fires (watchdog restart, manual re-trigger, concurrent worker race). This would surface as EXECUTION/INFRASTRUCTURE loss (wasted gas, orphaned tx, stuck nonce blocking the queue) rather than a strategy signal problem, and is architecturally plausible though not confirmed by runtime evidence this cycle.

## RESEARCH_MORE — leader-copy-solana
If slippageBps is only enforced as a post-hoc rejection check rather than passed to Jupiter's own routing at quote/order time, worse-than-necessary fills could still be attempted and only rejected after the fact (or accepted within the post-hoc ceiling but worse than optimal), directly affecting realized slippage cost — a direct NET P&L driver on Solana.

## RESEARCH_MORE — leader-copy-solana
A fixed-nominal Solana position size does not scale with account capital growth or per-opportunity liquidity/volatility, unlike EVM's percentage-based approach. It is unclear from architecture alone whether this is a deliberate HOOD-incident-driven risk containment choice (learnerbot/solana_pool_risk_gate.py was built from that post-mortem) or a legacy default, which matters for interpreting cross-chain net-P&L comparisons under a like-for-like economic lens.
