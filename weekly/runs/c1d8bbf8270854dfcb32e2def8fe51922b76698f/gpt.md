# Gpt engineering audit

Found concrete execution-recovery, configuration, concurrency, lifecycle-accounting, deployment, packaging, and CI supply-chain defects. The most serious can cause an already-broadcast EVM trade to be retried, silently arm real-money trading during state restoration, or interrupt an in-progress VPS deployment.

Status: ISSUES_FOUND

## P1 — Receipt uncertainty is recorded as REJECTED and permits duplicate automatic execution
A transaction that landed after an RPC timeout is treated as never submitted. The same wallet can execute another route on the next cycle, exceeding intended trade frequency/capital exposure while the original trade remains untracked.

## P1 — Fresh or partially restored data directories automatically create a hard-coded MASTER and arm LIVE trading
A disaster recovery, new host, lost marker volume, or data-directory migration can silently recreate privileged identity and real-money execution settings. Repository code, rather than deployment authorization, becomes sufficient to arm LIVE operation.

## P2 — Shared CSV read-modify-replace operations are not synchronized across runtime threads
Concurrent writers can lose rows, overwrite newer settings, raise FileNotFoundError while replacing the same temporary file, or corrupt audit/cooldown evidence. Missing execution rows can subsequently weaken rate limits and accounting reconciliation.

## P2 — Re-registering an unchanged strategy creates phantom versions and resets lifecycle status
Every process restart can manufacture a new strategy version despite identical code/specification and transiently revert REWORK, REPLACE, or PROMOTION_CANDIDATE to SHADOW. Trade provenance and lifecycle histories no longer identify actual strategy changes reliably.

## P1 — A newer deployment cancels an in-progress VPS deployment
A second push or dispatch can terminate installation/restart work midway, leaving a partially updated environment or ambiguous running commit while the replacement deployment begins.

## P2 — Published project metadata omits the mandatory Solana runtime dependency
Installing the package with pip/build metadata rather than requirements.txt produces an environment that cannot start the bot, including EVM-only commands because the Solana patch stack is imported globally.

## P2 — Privileged AI workflows execute unpinned latest CLI packages with repository credentials
An upstream package compromise or incompatible release is immediately executed in a credentialed job and can exfiltrate API tokens, manipulate review artifacts, or misuse repository write/dispatch authority.

## P3 — Direct-market Strategy Lab largest loss records aggregate losses instead of the largest individual loss
Risk reports overstate single-trade loss magnitude and may drive incorrect Strategy Lab decisions or operator investigation.
