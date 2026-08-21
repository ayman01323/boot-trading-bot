# Gemini strategy review

Completed a comprehensive architecture-only strategic review. Due to missing runtime forensics, no strategy is promoted to canary/live lanes. Identified three major improvement areas: restricting Dislocation Mean Reversion to negative-only dislocations for long-only spot-buys, developing an active quote/price adapter for Solana leader events to bypass the current fail-closed zero-value default, and tightening the New Liquidity Quality pool age window from 24 hours to 4 hours to lower rugpull exposure.

## IMPROVE — Dislocation Mean Reversion
A long-only directional spot strategy can only profit from buying undervalued assets. Evaluating absolute dislocation means the bot may buy overvalued assets (dislocation_z > 1.5) expecting them to reversion-drop, causing immediate losses upon buy.

## REWORK — Solana SIBOT / Market Feature Adaptation
Because Solana feature adaptation lacks contemporaneous quotes, its opportunities fail the platform-wide common executability thresholds. Integrating an RPC/Jupiter price fetcher is required to calculate genuine executable edge.

## IMPROVE — New Liquidity Quality
A 24-hour window is too wide for new liquidity pools. High-risk rugpulls, honeypots, and sudden liquidity drains occur most frequently during initial hours of pool launch. Restricting the evaluation window to the first 4 hours (14,400 seconds) focuses the strategy on fresh, active listings and mitigates stale-honeypot risk.
