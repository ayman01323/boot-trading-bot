from __future__ import annotations

from . import ai_health_compact_report_patch as _compact
from . import telegram_master_ai_dashboard_patch as _master


def master_ai_dashboard_text(_app) -> str:
    """Final MASTER dashboard presentation.

    The underlying engineering, strategy and Strategy Factory health collectors
    remain unchanged. Only the top-level Telegram presentation is collapsed to
    one aggregate status light per lane.
    """
    return _compact.dashboard_text()


def install() -> None:
    _master.master_ai_dashboard_text = master_ai_dashboard_text
    _master._compact_health_tree_installed = True


install()

# Final mobile presentation: shorter lines, deliberate section spacing, and one
# deployment-time MASTER refresh message. Presentation only; health/trading truth
# collectors remain unchanged.
from . import telegram_ai_health_mobile_layout_patch as _mobile_health_layout  # noqa: E402,F401

# MASTER-only launcher for the isolated Google learner Telegram bot. This adds
# only navigation; learner private keys are never accepted by the production bot.
from . import telegram_google_learner_launcher_patch as _google_learner_launcher  # noqa: E402,F401
