# Gemini strategy review

Architecture-only review completed. Due to the absence of fresh runtime forensics (evidence.json is flagged as MISSING_RUNTIME_FORENSICS), no strategy is shown to be profitable, canary-ready, or live-ready. Our analysis reveals two major architectural limitations in strategy evaluation: (1) severe selection bias in Learned Route Replication where historical route averages ignore negative outcomes, and (2) circular scoring in the shadow strategy executor where simulated performance is derived directly from decision-time expected edge rather than independent future price outcomes. Additionally, Solana leader copying lacks contemporaneous follower-size quotes, failing closed at zero edge. We propose four structured reworks to establish unbiased, falsifiable, and chain-specific net-P&L metrics.

## REWORK — Learned Route Replication
The existing pattern learning algorithm exhibits severe selection bias. It averages only positive-profit proven transactions and suppresses any route or pattern with a non-positive historical net average. This results in highly over-optimistic expected edge estimates and prevents the bot from learning to avoid routes that consistently lose money after execution fees and slippage.

## IMPROVE — Cross-chain SHADOW strategy scorecard
Without independent, post-decision price and market data inputs, the shadow scorecard cannot validate the predictive accuracy of the strategy's expected edge. Measuring performance by multiplying notional size by expected edge bps assumes perfect forecast calibration and hides any slippage, price impact, delay degradation, or execution errors.

## NEW_SHADOW — Solana Leader Copy Executable Edge
Solana leader copying currently has no way to evaluate real-time executable edge because leader-event logs do not capture contemporaneous follower-size quotes. A leader might buy in a highly liquid market, but a follower executing seconds later on a delayed block may face severe slippage, high priority fees, or frontrunning, which are completely hidden under the current architecture.

## SHADOW_MORE — Adaptive Cross-Chain Risk Reserve
A static nominal threshold cannot represent chain-specific dynamics. EVM transactions face high revert costs and builder-fee overhead, whereas Solana experiences extremely high block congestion, transaction dropped-rate risks, and rapid price decay. Applying a uniform 3-to-5 bps threshold fails to protect capital when recent network latency or asset volatility exceeds the fixed margin.
