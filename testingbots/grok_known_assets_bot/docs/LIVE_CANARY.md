# Grok LIVE Canary — manual-confirmation real-money mode

Default **OFF**. The LIVE canary is the only path in this bot that signs and
broadcasts a real Solana transaction. Every entry **and** every exit needs a
single-use approval. `/grokarm` and `/groklivecanary` alone never broadcast.

## What has to be true for a broadcast

1. Process started with `--enable-live-canary` (systemd unit is disabled by default).
2. Control file: `armed` **and** `live_readiness_enabled` **and** `live_canary_enabled` all true.
3. The PAPER runner (in LIVE_READINESS mode) emitted a fresh `LIVE_READY` ticket.
4. The canary runner turned it into a `PENDING_APPROVAL` with persisted route evidence and a short TTL (`GROK_LIVE_CANARY_APPROVAL_TTL_SECONDS`, default 90, clamped 30–300).
5. An authorised approver sent `/grokapprove <id> CONFIRM` before the TTL elapsed.
6. The runner atomically claimed `APPROVED → EXECUTING`, then **re-ran** the full readiness preflight (fresh quotes, reverse + 3× stress, impact and round-trip-loss caps), a funding/reserve check, a pre-broadcast local simulation, and a route-degradation check (`min_out` may not drop below the approved value).
7. Only then: sign locally (single-signer only), broadcast once.

## Hard invariants

- **Hard cap: 0.009 SOL** per approved trade — an integer-lamports module constant in `live_canary.py`. No config / env / CSV / Telegram argument can raise it. A request above it is refused, not clamped.
- **Max 1 open canary position**, enforced by a DB check + partial unique indexes on the approvals ledger.
- **Single-use approvals** — every status transition is guarded `WHERE status=<expected>` in a transaction.
- **Restart safety** — on start every `PENDING` ticket becomes `EXPIRED` and every `APPROVED` / `EXECUTING` / `BROADCAST_SUBMITTED` ticket becomes `RECONCILIATION_REQUIRED`. A broadcast is never auto-resumed.
- **Broadcast ambiguity** — a pre-broadcast failure → `SIMULATION_FAILED` (canary stays on). A failure at or after send, or a landed-but-unproven result → `UNKNOWN_OUTCOME` / `RECONCILIATION_REQUIRED`, the canary is **disabled**, and a human must reconcile on-chain before re-enabling.
- **Every exit is approval-gated** (`/grokexit <position_id> CONFIRM`). There is no automatic sell in canary v1.

## Approver identity

`GROK_LIVE_CANARY_APPROVER_USER_IDS` — comma-separated Telegram **user** ids. If
set, the sender's user id must be on the list. If unset, approval is only
accepted from a private chat (`chat.id == from.id`) that is also on
`GROK_TELEGRAM_CHAT_IDS`. Both chat id and user id are recorded on the ticket.

## Telegram controls

| Command | Effect |
|---|---|
| `/groklivecanary on CONFIRM` | Enable the canary (needs arm + live-readiness already on). Approves nothing. |
| `/groklivecanary off` | Disable the canary, cancel unclaimed tickets. In-flight broadcasts are left to reconcile. |
| `/grokpending` | List pending / approved tickets and their ids. |
| `/grokapprove <id> CONFIRM` | Approve one entry ticket (authorised approvers only). |
| `/grokexit <position_id> CONFIRM` | Approve a sell of the open position (authorised approvers only). |
| `/grokstop` | Everything off, cancel unclaimed tickets. |

## Environment

Required for the canary process (never for PAPER):

- `GROK_BOOT_REPO_ROOT` — path to the parent repo so `learnerbot` (the audited Solana executor) is importable.
- `GROK_LEARNERBOT_CSV_DIR`, `GROK_LEARNERBOT_DATA_DIR` — the shared learnerbot dirs holding the encrypted wallet + Solana settings.
- `GROK_LIVE_CANARY_TELEGRAM_ID` — the telegram id whose encrypted Solana signing wallet the canary uses.
- `SOLANA_RPC_URL` — a dedicated RPC endpoint (the executor uses a single endpoint; point it at a provider with its own failover).
- Optional: `GROK_LIVE_CANARY_APPROVER_USER_IDS`, `GROK_LIVE_CANARY_APPROVAL_TTL_SECONDS`.
- `pip install -e '.[live]'` for `requests` / `solders` / `cryptography` / `base58`.

## Deployment leaves it OFF

Ship the code with the systemd canary unit disabled and the control file's
`live_canary_enabled=false`. Enabling requires the operator to run
`/groklivecheck on CONFIRM` then `/groklivecanary on CONFIRM` and for the canary
service to be started with `--enable-live-canary`.
