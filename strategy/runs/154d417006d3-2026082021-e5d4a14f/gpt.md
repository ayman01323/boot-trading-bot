# GPT strategy review

Architecture-only review completed. EVM execution has fresh wallet-specific simulation, conservative gas reservation, exact pre-broadcast eth_call and receipt-based accounting. Solana Strategy Lab correctly fails closed because it lacks contemporaneous executable edge. However, learned EVM route statistics exclude proven losses, Solana shadow economics lack current quote-cost features, and runtime forensics do not provide a unified money-weighted attribution of STRATEGY/MARKET losses versus EXECUTION/INFRASTRUCTURE failures. No profitability, CANARY-readiness or LIVE-readiness conclusion is supported.

## REWORK — Learned Route Replication
Excluding losing observations creates survivorship bias and can label a route historically positive even when its money-weighted net result is negative. Compute route statistics over every comparable proven outcome, including failed paid-gas attempts where attribution is possible, and retain separate strategy-market and execution-infrastructure loss totals.

## NEW_SHADOW — Solana Costed Copy Signal
Leader profitability and a fresh BUY signal do not establish follower edge. Solana requires a contemporaneous round-trip quote at proposed size, price-impact and sellability checks, priority/Jito/platform costs, latency reserve, failure-cost expectation, and separate refundable rent exposure.

## SHADOW_MORE — Cross-Chain Net-Edge Strategies
Identical bps floors are not economically equivalent across EVM gas markets and Solana priority-fee/congestion states. Promotion thresholds should be based on conservative lower-bound expected value and absolute retained net profit after chain-specific failure costs, not a universal bps cutoff.

## IMPROVE — Money-Weighted Loss Forensics
A losing completed position is strategy/market loss when execution behaved as specified; reverts, timeouts, stale quotes, RPC faults, sell failures and paid-gas failures are execution/infrastructure failures. Keeping both ledgers separate prevents strategy changes from masking operational defects while still charging every cost to total NET P&L.
