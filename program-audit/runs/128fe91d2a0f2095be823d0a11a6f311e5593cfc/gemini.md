# Gemini full-program audit

Audited EVM and Solana execution, P&L accounting, and economic safety guards. Identified a critical P1 bug masking exact SOL losses on dust exits, a P2 bypass of priority fee controls during Jupiter rate-limit recovery, and a P3 defect classifying break-even trades as losses.

## P1 — Exact negative wallet SOL delta is discarded, artificially inflating P&L
If a sell transaction results in a net loss of SOL (e.g. a dust exit where the Solana priority fee exceeds the swap return), the exact negative `delta` is discarded. The code silently falls back to an estimate based on `out_lamports` (which excludes the priority fee), artificially padding the realised P&L and masking the loss.

Corrective action: Change the condition to `if delta_raw is not None:` and ensure exact negative deltas translate to negative proceeds.

## P2 — HTTP 400 recovery retry omits maximum priority fee and tip limits
When Jupiter responds with HTTP 400 on an order carrying manual fee caps, the recovery logic retries using `base_params`. Because `base_params` strips out `priorityFeeLamports`, `jitoTipLamports`, and `broadcastFeeType`, the retry silently drops explicit Jito tips and delegates back to Jupiter's default unmanaged fee. While subsequent guards may reject the returned order, the broadcast cap intent is bypassed.

Corrective action: Preserve explicit `priorityFeeLamports` limits on retry, or reject the fallback entirely if `broadcastFeeType` cannot be securely supported.

## P3 — Break-even trades are improperly classified as losses in profit metrics
Trades with exactly zero `realised_net_sol` increment `closed` but not `wins`. The simple `closed - wins` calculation counts them as losses. This deflates the reported win rate and could prematurely trigger negative performance metrics.

Corrective action: Calculate losses explicitly using `sum(1 for n, _ in vals if n < 0)`.
