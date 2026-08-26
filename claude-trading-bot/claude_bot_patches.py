"""Single source of truth for what must happen in the child process before
learnerbot's own patch chain runs.

Imported by both bootstrap_run.py (the real exec target) and
verify_bootstrap_composition.py (the test proving all of it survives the
full chain), so the two can never silently drift apart -- adding a new
patch or quarantine step here automatically covers both.

Order matters and is enforced by install_all() itself, not left to caller
discipline: quarantine + secret-blocking MUST happen before learnerbot's
patch chain is ever imported (see claude_bot_quarantine.py for why), and
identity/risk guards must be installed before that same import too (see
identity_patch.py / solana_execution_risk_patch.py for why -- os.execvpe()
wipes anything done in a parent process, so this all has to happen fresh in
this exact process, in this exact order, every time).
"""

from __future__ import annotations

import claude_bot_quarantine
import evm_execution_guard_patch
import identity_patch
import solana_execution_risk_patch


def install_all(app) -> None:
    claude_bot_quarantine.quarantine_historical_migrations(app)
    claude_bot_quarantine.block_production_env_fallback()
    identity_patch.install()
    solana_execution_risk_patch.install()
    evm_execution_guard_patch.install()
