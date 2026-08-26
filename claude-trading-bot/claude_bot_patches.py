"""Single source of truth for which patches must be installed in the child
process before learnerbot's own patch chain runs.

Imported by both bootstrap_run.py (the real exec target) and
verify_bootstrap_composition.py (the test proving they survive the full
chain), so the two can never silently drift apart -- adding a third patch
here automatically covers both.
"""

from __future__ import annotations

import identity_patch
import solana_execution_risk_patch


def install_all() -> None:
    identity_patch.install()
    solana_execution_risk_patch.install()
