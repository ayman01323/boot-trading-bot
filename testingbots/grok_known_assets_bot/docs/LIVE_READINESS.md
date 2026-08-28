# Grok LIVE Readiness Mode

`LIVE_READINESS` is a real-market execution-readiness mode that stops before wallet access, signing, or transaction broadcast.

## Telegram controls

- `/groklivecheck on CONFIRM` — arm live-readiness checks.
- `/groklivecheck off` — disable live-readiness checks and disarm.
- `/grokstatus` — show current mode and latest decision.
- `/grokstop` — fail closed; disable PAPER entries and live-readiness checks.

## Native SOL preflight

For `solana:SOL:NATIVE`, a qualified strategy entry is revalidated using fresh public Jupiter routes:

1. Estimate the USDC required for a 0.0005 SOL canary target.
2. Quote USDC → SOL at the configured slippage cap.
3. Quote the complete returned SOL amount back to USDC.
4. Quote a 3× SOL exit stress route.
5. Enforce signal age, entry impact, reverse impact, stress impact and estimated round-trip-loss limits.
6. Emit `LIVE_READY` only when every check passes; otherwise emit `LIVE_PREFLIGHT_REJECT` with the blocking reason.

Hard safety defaults:

- Canary target: 0.0005 SOL.
- Hard canary ceiling: 0.001 SOL.
- Maximum signal age: 20 seconds.
- Maximum entry impact: 100 bps.
- Maximum full-reverse impact: 200 bps.
- Maximum 3× stress impact: 500 bps.
- Maximum estimated round-trip loss: 3%.

## Security boundary

This mode does not load a wallet or private key. It does not create a signed transaction and does not call a transaction-broadcast endpoint. Every readiness payload and Telegram ticket explicitly reports signing and broadcast as disabled.
