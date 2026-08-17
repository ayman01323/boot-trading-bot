from __future__ import annotations

import html

from . import solana_sibot as _sol
from . import telegram_ui as _ui

_PREV_CHAINS_PAGE = _ui.chains_page


def _is_on(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _solana_line(app) -> str:
    try:
        cfg = _sol.settings(app)
        enabled = _is_on(cfg.get("enabled"), True)
        rpc = str(cfg.get("rpc_url") or "").strip()
        rpc_status = "configured" if rpc else "MISSING"
        if enabled:
            return (
                "• 🟣 <b>Solana</b> — <b>✅ ACTIVE</b>, "
                f"mode <b>SHADOW</b>, RPC {html.escape(rpc_status)}, base <b>SOL</b>"
            )
        return (
            "• 🟣 <b>Solana</b> — <b>🔴 INACTIVE</b>, "
            f"mode <b>SHADOW</b>, RPC {html.escape(rpc_status)}, base <b>SOL</b>"
        )
    except Exception as exc:
        return f"• 🟣 <b>Solana</b> — status unavailable ({html.escape(type(exc).__name__)})"


def chains_page(app):
    text = str(_PREV_CHAINS_PAGE(app))
    if "<b>Solana</b>" in text:
        return text
    marker = "\n\nEnable/disable chains in <code>CSVbot/chains.csv</code>."
    line = _solana_line(app)
    note = (
        "\nSolana is configured separately in <code>CSVbot/solana_settings.csv</code>. "
        "ACTIVE means the Solana scanner/ranking/leader monitor is running; "
        "SHADOW means real Solana transactions are still not broadcast."
    )
    if marker in text:
        return text.replace(marker, f"\n{line}{note}{marker}", 1)
    return f"{text}\n{line}{note}"


def install():
    if getattr(_ui, "_solana_chains_patch_installed", False):
        return
    _ui.chains_page = chains_page
    _ui._solana_chains_patch_installed = True


install()
