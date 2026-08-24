from __future__ import annotations

import html
import threading

from . import sibot as _sibot
from . import sibot_leader_quality_hard_floor_patch as _quality_floor
from . import telegram_sibot_patch as _tg
from . import telegram_ui as _ui
from .operator_control import audit
from .user_registry import all_users, require_user, set_user_setting, user_setting

SOURCE_KEY = "sibot_settings_source"
SOURCE_SELF = "SELF"
SOURCE_PRIMARY_MASTER = "PRIMARY_MASTER"

# Preserve the exact pre-hard-floor settings resolver. The hard-floor wrapper remains
# the public/final _sibot.user_settings identity; only its inner source is changed.
_PREV_BASE_USER_SETTINGS = _quality_floor._PREV_USER_SETTINGS
_PREV_SETTINGS_PAGE = _tg.settings_page
_PREV_SETTINGS_KEYBOARD = _tg.settings_keyboard
_PREV_HANDLE_UPDATE = _ui.handle_update


def primary_master_id(csv_dir):
    """Return the original/first MASTER in users.csv, preferring an ACTIVE one.

    users.csv preserves account insertion order. The original MASTER is therefore
    the first MASTER row; later-added MASTER accounts never become the source merely
    because they were activated more recently.
    """
    masters = [
        row for row in all_users(csv_dir)
        if str(row.get("role") or "").strip().upper() == "MASTER"
        and str(row.get("telegram_id") or "").strip()
    ]
    if not masters:
        return None
    for row in masters:
        if str(row.get("status") or "").strip().upper() == "ACTIVE":
            return str(row.get("telegram_id") or "").strip()
    return str(masters[0].get("telegram_id") or "").strip() or None


def settings_source(app, telegram_id) -> str:
    raw = str(user_setting(app.csv_dir, telegram_id, 0, SOURCE_KEY, SOURCE_SELF) or SOURCE_SELF).strip().upper()
    return SOURCE_PRIMARY_MASTER if raw == SOURCE_PRIMARY_MASTER else SOURCE_SELF


def source_telegram_id(app, telegram_id) -> str:
    tid = str(telegram_id)
    if settings_source(app, tid) != SOURCE_PRIMARY_MASTER:
        return tid
    master = primary_master_id(app.csv_dir)
    return str(master or tid)


def user_settings_with_source_base(app, telegram_id, chain_id=0) -> dict:
    """Resolve SiBot strategy tuning from SELF or the original MASTER.

    Execution authority is deliberately local to the requesting account. Inherited
    settings never copy sibot_enabled or sibot_auto_trade_enabled, so selecting the
    original MASTER cannot arm LIVE/AUTO, change wallets, or grant signing rights.
    """
    tid = str(telegram_id)
    source_tid = source_telegram_id(app, tid)
    cfg = dict(_PREV_BASE_USER_SETTINGS(app, source_tid, chain_id))
    own = dict(_PREV_BASE_USER_SETTINGS(app, tid, chain_id))
    cfg["enabled"] = str(own.get("enabled", "false"))
    cfg["auto_trade_enabled"] = str(own.get("auto_trade_enabled", "false"))
    cfg["_settings_source"] = settings_source(app, tid)
    cfg["_settings_source_tid"] = source_tid
    return cfg


def _source_label(app, tid) -> str:
    source = settings_source(app, tid)
    primary = primary_master_id(app.csv_dir)
    if source == SOURCE_PRIMARY_MASTER and primary and str(primary) != str(tid):
        return "👑 Main Master settings"
    if source == SOURCE_PRIMARY_MASTER:
        return "👑 Main Master settings (this ID)"
    return "👤 My settings"


def settings_page(app, tid):
    base = _PREV_SETTINGS_PAGE(app, tid)
    primary = primary_master_id(app.csv_dir)
    lines = [
        "<b>🧭 SiBot settings</b>",
        f"Source: <b>{html.escape(_source_label(app, tid))}</b>",
        "Strategy/quality tuning may follow the original Main Master. Your own LIVE, AUTO, wallet and signing permissions always remain separate.",
    ]
    if not primary:
        lines.append("⚠️ No MASTER account is currently available as a source.")
    elif settings_source(app, tid) == SOURCE_PRIMARY_MASTER and str(primary) != str(tid):
        lines.append("Your previous personal SiBot tuning remains stored but is ignored while Main Master settings are selected.")
    return "\n".join(lines) + "\n\n" + base


def settings_keyboard(app, tid):
    kb = _PREV_SETTINGS_KEYBOARD(app, tid)
    rows = list(kb.get("inline_keyboard") or [])
    source = settings_source(app, tid)
    primary = primary_master_id(app.csv_dir)
    chooser = [
        {
            "text": ("✅ " if source == SOURCE_SELF else "") + "👤 My settings",
            "callback_data": "sibot:source:self",
        }
    ]
    if primary:
        chooser.append({
            "text": ("✅ " if source == SOURCE_PRIMARY_MASTER else "") + "👑 Main Master",
            "callback_data": "sibot:source:primary",
        })
    return {"inline_keyboard": [chooser] + rows}


def _refresh_async(app, tid):
    def run():
        try:
            _sibot.refresh_all_rankings(app, tid)
        except Exception as exc:
            print("[sibot-settings-source-refresh]", type(exc).__name__, str(exc)[:240])
    threading.Thread(target=run, daemon=True, name=f"sibot-settings-source-{tid}").start()


def _render_settings(app, tid, cb):
    _tg._render(app, tid, _tg.settings_page(app, tid), _tg.settings_keyboard(app, tid), cb)


def handle_update(app, update):
    cb = update.get("callback_query")
    if cb:
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        data = str(cb.get("data") or "")
        if data in {"sibot:source:self", "sibot:source:primary"}:
            require_user(app.csv_dir, tid, active=False)
            old = settings_source(app, tid)
            if data.endswith(":primary"):
                primary = primary_master_id(app.csv_dir)
                if not primary:
                    _tg._answer(app, cb, "No Main Master account is available")
                    _render_settings(app, tid, cb)
                    return
                new = SOURCE_PRIMARY_MASTER
            else:
                new = SOURCE_SELF
            set_user_setting(
                app.csv_dir,
                tid,
                SOURCE_KEY,
                new,
                chain_id="*",
                description="SiBot strategy/quality settings source; execution authority stays per-user",
            )
            audit(
                app.csv_dir,
                tid,
                "USER_SIBOT_SETTINGS_SOURCE",
                SOURCE_KEY,
                old,
                new,
                "Does not change LIVE/AUTO/wallet/signing permissions",
            )
            _tg._answer(app, cb, "SiBot settings source updated")
            _refresh_async(app, tid)
            _render_settings(app, tid, cb)
            return

        # In inherited mode, keep dormant personal overrides untouched and block
        # accidental edits until the user explicitly switches back to My settings.
        if (data == "sibot:partial:toggle" or data.startswith("sibot:set:")) and settings_source(app, tid) == SOURCE_PRIMARY_MASTER:
            primary = primary_master_id(app.csv_dir)
            if primary and str(primary) != str(tid):
                _tg._answer(app, cb, "Using Main Master settings. Select My settings to edit.")
                _render_settings(app, tid, cb)
                return

    return _PREV_HANDLE_UPDATE(app, update)


def install():
    if getattr(_ui, "_sibot_settings_source_patch_installed", False):
        return
    # Keep the final quality-floor wrapper authoritative, but feed it source-aware
    # effective settings. This preserves all hard floors/ceilings already audited.
    _quality_floor._PREV_USER_SETTINGS = user_settings_with_source_base
    _tg.user_settings = _sibot.user_settings
    _tg.settings_page = settings_page
    _tg.settings_keyboard = settings_keyboard
    _ui.handle_update = handle_update
    _ui._sibot_settings_source_patch_installed = True
    print("[sibot-settings-source] self-or-primary-master tuning enabled; execution gates remain per-user")


install()
