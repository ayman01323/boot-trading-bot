# SiLearn — 2026-08-29 13:28 BST — Subject: Downgrade LP lock/provider warnings to Telegram reference only

Baseline main SHA: `29da3cf2e138338b1da4aa67ae63dbb2c9d16937`

Owner instruction:
- LP locking level is warning-only.
- RugCheck LP-specific severe warning is warning-only.
- LP provider diversification/concentration is warning-only.
- These signals must not block LIVE by themselves.
- Telegram should show them as reference warnings.
- Continue enforcing all other existing PoolCheck conditions and structural token safety checks.

Preserved blocking controls include structural token risks (mint/freeze authority, permanent delegate, honeypot, blacklist, non-transferable/default-account-state/malicious transfer mechanics), provider/data failures, pool age/cooling, liquidity collapse, material depth/indexing, fresh-pool volume/liquidity and cross-price checks, Jupiter reverse-depth sellability, impact/round-trip-loss checks, execution/simulation/reserve/fee/slippage protections, and existing stress-exit protections.

Rollback: revert the single squash commit for this change, or restore branch `rollback/SiLearn-2026-08-29T1328BST-pre-lp-warning-only`.
