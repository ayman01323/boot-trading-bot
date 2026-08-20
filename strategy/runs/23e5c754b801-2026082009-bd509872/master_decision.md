# GPT Master strategy decision

Architecture-only evidence supports draft SHADOW accounting, failure-attribution and chain-cost calibration work. Profitability, promotion and live readiness remain unproven. EVM reconstruction needs measured bias evidence. Both proposed new signals duplicate existing Strategy Lab strategies.

## ACCEPT — Strategy Lab cost and outcome accounting
Code confirms record_window accepts caller-supplied net profit and otherwise deducts only aggregate fees and slippage. EVM realised_net_base excludes the separately calculated profit share and settlement gas. Solana also retains fixed estimated-exit-fee fallbacks, although the claim that 0.00002 SOL is always a vast underestimate is unsupported. Accept comprehensive reconciled SHADOW accounting, not the prescribed API or three-day validation shortcut.

## ACCEPT — Outcome attribution and promotion governance
Code stores only an aggregate execution_failures count, promotion evaluation does not use execution reliability, and EVM exceptions collapse materially different outcomes into REJECTED. A mutually exclusive terminal taxonomy is necessary before promotion evidence is trustworthy.

## ACCEPT — Cross-chain executable-edge calibration
The shared signal layer uses static 3-5 bps floors while chain, route, congestion and notional costs differ. EVM missing impact and latency fields become zero; Solana leader-event features correctly fail closed because executable quotes are absent. Calibration must preserve abstention and use quote-to-terminal evidence.

## DEFER — EVM SiMo leader-history reconstruction
Code intentionally reconstructs only direct native-to-token FIFO trades and therefore excludes token-to-token routes. That limitation can bias leader rankings, but no measured sample establishes the claimed direction or magnitude, and comprehensive historical token valuation is nontrivial. Run a read-only comparison before reworking the strategy.

## REJECT — Profitable-wallet cohort momentum signal
This duplicates the existing Flow Acceleration strategy, which already requires independent wallet sources, dispersion, liquidity and positive net edge; repository guidance also already requests profitable-wallet cohort research. The 55% claim and proposed eight-trade threshold have no measured support and are too sparse.

## REJECT — New-liquidity quality filter for early-pool tokens
The checked-out source already implements New Liquidity Quality using pool age, liquidity, sellability, dispersion and positive net edge. The proposed thresholds and profitability hypothesis are unsupported, so creating another strategy would be duplicative.

## ACCEPT — Existing SHADOW strategy governance
With no fresh strategy_lab_windows evidence, no strategy can be promoted. Code enforces at least three windows and eight trades, but those are minimum gates rather than sufficient profitability proof; complete cost, reliability and out-of-sample requirements must also pass.
