# SiLearn — 2026-08-29 12:23:54 BST — Subject: One-time rejection alerts + activate LP-unlocked LIVE revalidation

## Baseline / rollback
- Repository: `ayman01323/boot-trading-bot`
- Baseline commit: `98f570a47d9521d527f00ba543c09c235f120ab5`
- Permanent rollback branch: `rollback/SiLearn-2026-08-29T122354BST-pre-reject-visibility-lp-revalidation`
- Working branch: `gpt/SiLearn-2026-08-29T122354BST-reject-visibility-lp-revalidation`

## Owner-requested behaviour
1. Telegram rejection deduplication is **once only** for the same Telegram account + mint + leader + rejection reason.
2. Transaction/signature is excluded from the duplicate key, so repeated leader signals do not repeat the same alert.
3. The once-only marker is persisted in the existing Solana SQLite `state` table, so service restarts do not resend the same condition.
4. A different mint, leader, or rejection reason remains a new alert.
5. Preserve the full clickable Asset, Leader and Signal IDs from Change Set 5.
6. Activate the already-approved Change Set 4 runtime on the isolated Google Learner so `LP_CONCENTRATION_RISK / Large Amount of LP Unlocked` becomes a revalidation trigger rather than an immediate standalone LIVE refusal.
7. Structural RugCheck hard blocks and all execution protections remain unchanged.
8. No test trade is broadcast.

## Change Set 4 runtime requirements
The isolated Google Learner must print all of these after deployment:
- `[owner-changeset-4] approved=2026-08-29T10:38:58Z ... lp_unlocked=revalidate ...`
- `[owner-changeset-4-exit-safety] ...`
- `[owner-changeset-4-integrity] OK approved=2026-08-29T10:38:58Z ...`
- `[learner-reject-once] bot=SiLearn approved=2026-08-29T11:23:54Z ... persistent=true ...`

## Rollback
Revert the final squash commit for this change or restore `rollback/SiLearn-2026-08-29T122354BST-pre-reject-visibility-lp-revalidation`. The Google Learner deployment also creates a server-side pre-deploy backup before replacing any runtime code.
