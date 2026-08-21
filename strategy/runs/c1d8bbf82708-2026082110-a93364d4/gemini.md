# Gemini strategy review

Completed architecture-only review of multi-chain copy trading strategy. Due to the absence of fresh runtime forensics, the review relies on static analysis of the MultiChain_Top20_Copy_Behaviour_Bot design in learnerbot/solana_sibot.py and EVM variants. Execution/slippage protections appear structurally sound via break-even floor and take-profit logic, but live promotion is strictly prohibited until durable net money-weighted P&L after network fees and failure rates can be evaluated with real evidence.

## SHADOW_MORE — Solana Top20 Leader Copy Behaviour
Before trusting real capital to copy-behavior strategies on Solana, we must measure specific slippage and priority fee drift. Wait for a full evidence cycle.

## SHADOW_MORE — EVM Top20 Leader Copy Behaviour
Base/Ethereum transaction fees and sandwich attacks must be quantified in shadow execution before live rollout.
