# Gemini strategy review

Architecture-only review completed. The common SHADOW evaluators correctly require positive cost-adjusted edge and fail Solana observations closed when current executable edge is unavailable. However, production leader-copy admission remains substantially historical rather than contemporaneously executable, Solana fee budgeting uses a configured expected-profit margin, and EVM Strategy Lab accounting can treat BROADCAST or expected P&L as realised performance. These issues can confound durable money-weighted NET P&L assessment. STRATEGY/MARKET loss means a confirmed, reconciled round trip executed as intended but produced negative net P&L after all costs. EXECUTION/INFRASTRUCTURE failure means rejection, revert, dropped or unreconciled broadcast, material quote-to-fill deterioration, stale detection, RPC/API failure, or missing balance reconciliation. No fresh runtime forensics exist, so profitability and CANARY/LIVE readiness are not established.

## REWORK — Direct Market Arbitrage
Treating BROADCAST as a confirmed executed transaction and falling back to expected_net_base when realised_net_base is missing creates a risk of overstating net P&L and masking execution failures. A broadcast is a pending commitment, not a settled financial outcome.

## SHADOW_MORE — SiBot EVM Leader Copy
EVM leader copying can lose its edge due to detection delay, execution latency, and price impact, especially on high-fee networks like Ethereum. Relying purely on historical average returns as expected edge assumes zero decay between leader action and follower execution.

## NEW_SHADOW — Solana Leader Copy Executable Edge
While the Solana adapter correctly abstains from trading when contemporaneous executable return data is missing, the execution path computes fee ceilings using generic configured margins. Integrating real-time Jupiter quotes will establish signal-specific executable edge covering fees, rent, price impact, and failed transaction costs.

## IMPROVE — Cross-chain Strategy Evaluation
We must not lower strategy thresholds or alter models based on execution and infrastructure failures (e.g. RPC drops, transaction reverts, or signature timeouts), nor should we excuse actual adverse price action as infrastructure issues. Accurate causal attribution is key to evaluating genuine edge.
