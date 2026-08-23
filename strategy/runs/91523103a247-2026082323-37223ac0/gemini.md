# Gemini strategy review

Architecture-only review completed. Stale or missing runtime forensics were detected, which strictly prohibits claiming any strategy is profitable, canary-ready, or live-ready. We find that the repository's Strategy Lab and Canary execution structures are highly robust, utilizing multi-stage gating and strict fail-closed criteria. However, several critical architectural enhancements are proposed: (1) fully allocating all fees, slippage, and execution failure costs to the profit factor denominator to prevent false-positive promotions, (2) implementing a contemporaneous Solana quote-streaming adapter to replace current fail-closed zero values with true executable shadow outcomes, (3) separating lifecycle evaluation by specific blockchain networks to prevent cross-chain subsidy, and (4) properly factoring the economic costs of reverted or failed landing transactions into Canary and Strategy Lab money-weighted Net P&L. These changes will elevate the precision of the bot's risk-reward profile and protect capital from execution drag.

## REWORK — Strategy Lab evaluation accounting
Profit factor must be money-weighted and account for all costs including fees and slippage in the denominator (i.e. gross_profit / (gross_loss + fees + slippage)) to represent true economic feasibility.

## NEW_SHADOW — Solana executable-edge outcome adapter
Solana copy trading cannot be evaluated in shadow or promoted without contemporaneous executable Jupiter quote simulation data (both entry buy quote and exit sell quote) to prove net edge after fees, priority fees, and slippage.

## IMPROVE — Chain-specific lifecycle evaluation and failure attribution
Solana and EVM chains have radically different finality, fee structures, and failure modes. Aggregating them hides chain-specific losses and risks, and failing to block promotion on high execution failures poses severe capital risk.

## REWORK — Canary and Strategy Lab execution failure costing
Failed transactions on EVM (reverts) and Solana (failed landing/expired blocks) still incur transaction fees (gas, priority fees). Ignoring these real-money losses in the net P&L overstates profitability and can lead to premature promotion of high-failure strategies.
