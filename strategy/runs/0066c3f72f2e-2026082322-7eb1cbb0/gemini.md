# Gemini strategy review

Completed a comprehensive architecture-only strategy review of the copy-trading engines (EVM and Solana) and the Strategy Lab. Due to the lack of runtime forensics (MISSING_RUNTIME_FORENSICS), we maintain all strategies in SHADOW/Research mode and focus on addressing critical infrastructure and network efficiency bottlenecks in block polling on EVM and block parsing on Solana.

## IMPROVE — SiBot / SiMo Copy Trading
Rather than polling entire blocks sequentially, we should upgrade the polling loop to subscribe directly to target leader address transactions via RPC WebSockets (or address filters) or leverage webhooks to minimize CPU/network overhead and latency.

## REWORK — Solana SiBot
Fetching full Solana blocks containing thousands of irrelevant transactions consumes excessive server memory and API credits. Reworking the discovery engine to target leaders directly via getSignaturesForAddress or WebSocket subscriptions for leader accounts isolates the parsing logic exclusively to target leaders.

## KEEP — Strategy Lab non-leader families
Keeping these strategies strictly in SHADOW mode is mandatory and appropriate because there are no runtime forensics. This prevents premature exposure of real assets to unverified strategies.
