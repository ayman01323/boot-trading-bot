"""Single source of truth for the identity/risk/EVM-deny patches installed
in the child process before learnerbot's own patch chain runs.

Quarantine (claude_bot_quarantine.quarantine_before_any_learnerbot_import())
is NOT called here -- it has to run before the FIRST learnerbot import in
this process, and this module itself imports learnerbot submodules
(identity_patch/solana_execution_risk_patch/evm_execution_guard_patch all
import learnerbot.telegram/solana_live_executor/live_executor at module
level). Callers (run.py's parent process AND bootstrap_run.py's child --
sys.modules doesn't survive os.execvpe(), so both must do this) must call
quarantine_before_any_learnerbot_import() themselves, before importing this
module at all, not after.

Imported by both bootstrap_run.py (the real exec target) and
verify_bootstrap_composition.py (the test proving all of it survives the
full chain), so the two can never silently drift apart.
"""

from __future__ import annotations

import identity_patch
import evm_execution_guard_patch
import solana_execution_risk_patch


def install_all() -> None:
    identity_patch.install()
    solana_execution_risk_patch.install()
    evm_execution_guard_patch.install()
