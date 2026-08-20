# GPT full-program audit

Six concrete defects were identified. Most critically, ordinary startup grants a hard-coded Telegram identity MASTER privileges and arms live execution. Solana safety faults can strand positions, successful but incompletely reported swaps can become untracked, and non-transactional CSV and nonce handling permit races affecting authorization, limits, accounting, and execution.

## P0 — Normal startup grants a hard-coded Telegram ID MASTER privileges and arms live trading
Every fresh deployment, restored deployment missing the marker, or changed DATA_DIR silently gives a repository-embedded external identity full administrative Telegram control and real-money execution authorization. That identity can also operate MASTER-only platform gates.

Corrective action: Remove identity-specific migrations from runtime imports. Require an explicit, authenticated, auditable administrative command that verifies the target identity and never auto-promotes a missing user. Fail startup if an unapplied privilege migration is required.

## P1 — Solana circuit breaker disables position monitoring and emergency exits together with new entries
Automatic disablement caused by uncertain execution output—or a user's manual disarm—also disables risk-reducing exits for already-open positions, leaving capital exposed precisely when execution reliability is suspect.

Corrective action: Separate entry authorization from exit authorization. Disabling LIVE must block new buys while the monitor continues guarded exits for existing verified positions, with an independent emergency-exit kill switch reserved for signing compromise.

## P1 — A successful Solana BUY with incomplete Jupiter result metadata is not reconciled into a position
If Jupiter changes or omits response fields while the signed transaction succeeds, acquired tokens can exist in the wallet without a tracked cost basis, wallet binding, position monitor, or automatic exit path.

Corrective action: Persist a submitted/uncertain attempt before broadcast. Resolve the signature through RPC and compare pre/post token and SOL balances; create a reconciled position when acquisition is proven, or quarantine an explicit unknown holding for mandatory operator recovery.

## P1 — Concurrent EVM submissions from one wallet can sign the same pending nonce
Two operations for the same wallet and chain can receive the same nonce. One may replace or invalidate the other, causing failed exits, approval/swap sequencing failures, misleading broadcast reports, or unintended fee bidding competition.

Corrective action: Introduce a per-chain/per-address inter-process nonce manager that locks acquisition through broadcast, tracks locally reserved nonces, reconciles pending transactions, and handles replacement explicitly.

## P1 — Critical CSV authorization, accounting, and rate-limit state uses unlocked read-modify-replace operations
Overlapping writers can lose settings or execution rows, collide on the shared temporary file, or overwrite newer data. Lost execution rows undercount hourly trades/gas and can permit limits to be exceeded; lost settings can re-enable stale values or produce misleading reports.

Corrective action: Move mutable operational state and ledgers to SQLite transactions with uniqueness constraints. At minimum, use a shared inter-process lock, unique temporary files, reload under lock, and fsync the containing directory.

## P1 — Single-use activation codes can be redeemed concurrently more than once
Concurrent Telegram requests can exceed an activation code's authorized usage count and create unauthorized active trading accounts. Misconfigured codes are also irreversibly consumed without successful activation.

Corrective action: Store activation codes and users in a transactional database. Atomically validate the code and fee plan, increment usage conditionally, and activate exactly one identity in one transaction.
