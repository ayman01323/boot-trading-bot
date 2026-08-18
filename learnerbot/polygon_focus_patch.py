from __future__ import annotations

import csv
import os
import threading
from pathlib import Path

from . import auto_trader as _auto
from . import fast_market as _fast
from . import full_power_scanner as _power
from . import telegram_sibot_patch as _tg
from . import telegram_ui as _ui
from .user_registry import is_master

POLYGON_CHAIN_ID = 137
_LOCK = threading.RLock()
_ORIGINAL_EXECUTE = _auto.execute_best_live_opportunity
_ORIGINAL_POWER_LOAD = _power.load_kv_scoped
_ORIGINAL_SETTINGS_PAGE = _tg.settings_page
_ORIGINAL_SETTINGS_KEYBOARD = _tg.settings_keyboard
_ORIGINAL_HANDLE_UPDATE = _ui.handle_update


def _focus_path(app) -> Path:
    return Path(app.csv_dir) / "auto" / "polygon_focus.csv"


def focus_enabled(app) -> bool:
    path = _focus_path(app)
    if not path.exists():
        return False
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            row = next(csv.DictReader(f), None) or {}
        return str(row.get("enabled") or "").strip().lower() in {"1", "true", "yes", "on", "y"}
    except Exception:
        return False


def set_focus(app, enabled: bool) -> None:
    path = _focus_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["enabled", "chain_id", "chain_slug", "note"])
            w.writeheader()
            w.writerow({
                "enabled": "true" if enabled else "false",
                "chain_id": str(POLYGON_CHAIN_ID),
                "chain_slug": "polygon",
                "note": "User-controlled candidate focus only; does not enable LIVE/ARMED/signing",
            })
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)


def _polygon_execute(app, opportunities: list[dict]) -> list[dict]:
    if not focus_enabled(app):
        return _ORIGINAL_EXECUTE(app, opportunities)
    polygon = [r for r in opportunities if str(r.get("chain_id") or "") == str(POLYGON_CHAIN_ID)
               or str(r.get("chain_slug") or "").strip().lower() == "polygon"]
    # Fail closed to Polygon while focus is ON. The original executor still applies
    # every platform/user LIVE gate, wallet-specific simulation and profit threshold.
    if not polygon:
        return []
    return _ORIGINAL_EXECUTE(app, polygon)


def _power_settings(path, chain_id):
    cfg = dict(_ORIGINAL_POWER_LOAD(path, chain_id))
    try:
        class _A:
            csv_dir = Path(path).parent
        if focus_enabled(_A()):
            # More coverage while Polygon focus is active. These settings only
            # increase the number of candidates inspected/retained; they do not
            # weaken edge, liquidity, product, price-impact or simulation gates.
            cfg["fast_market_max_candidate_checks"] = str(max(120, int(float(cfg.get("fast_market_max_candidate_checks", "60") or 60))))
            cfg["fast_market_max_routes_per_pass"] = str(max(60, int(float(cfg.get("fast_market_max_routes_per_pass", "20") or 20))))
            cfg["full_power_parallel_chains"] = str(max(5, int(float(cfg.get("full_power_parallel_chains", "5") or 5))))
    except Exception:
        pass
    return cfg


def settings_page(app, tid):
    base = _ORIGINAL_SETTINGS_PAGE(app, tid)
    state = "ON — Polygon only" if focus_enabled(app) else "OFF — all chains"
    return base + "\n\n<b>🟣 POLYGON AUTO FOCUS</b>\n" + \
        f"State: <b>{state}</b>\n" + \
        "When ON, direct AUTO candidates are restricted to Polygon chain 137 and scanner coverage is increased. " + \
        "LIVE/ARMED/signing and all profit/risk checks remain separate."


def settings_keyboard(app, tid):
    kb = _ORIGINAL_SETTINGS_KEYBOARD(app, tid)
    rows = list((kb or {}).get("inline_keyboard") or [])
    if is_master(app.csv_dir, tid):
        on = focus_enabled(app)
        row = [{"text": f"🟣 Polygon-only AUTO {'ON' if on else 'OFF'}", "callback_data": "polygon:focus:toggle"}]
        # Put the focus control just before the Back row when possible.
        idx = max(0, len(rows) - 1)
        rows.insert(idx, row)
    return {"inline_keyboard": rows}


def _answer(app, cb, text=""):
    cqid = (cb or {}).get("id")
    if cqid:
        try:
            _ui.answer_callback_query(app.telegram_bot_token, cqid, text)
        except Exception:
            pass


def handle_update(app, update):
    cb = (update or {}).get("callback_query")
    if cb and str(cb.get("data") or "") == "polygon:focus:toggle":
        tid = ((cb.get("message") or {}).get("chat") or {}).get("id")
        if not _ui._auth(app, tid) or not is_master(app.csv_dir, tid):
            _answer(app, cb, "Master only")
            return
        new_state = not focus_enabled(app)
        set_focus(app, new_state)
        _answer(app, cb, "Polygon-only focus ON" if new_state else "Polygon focus OFF")
        try:
            _tg._render(app, tid, settings_page(app, tid), settings_keyboard(app, tid), cb)
        except Exception:
            _ui._send(app, tid, settings_page(app, tid), settings_keyboard(app, tid))
        return
    return _ORIGINAL_HANDLE_UPDATE(app, update)


def install():
    if getattr(_auto, "_polygon_focus_patch_installed", False):
        return
    _auto.execute_best_live_opportunity = _polygon_execute
    # fast_market imported the executor by name, so patch its bound reference too.
    _fast.execute_best_live_opportunity = _polygon_execute
    _power.load_kv_scoped = _power_settings
    _tg.settings_page = settings_page
    _tg.settings_keyboard = settings_keyboard
    _ui.handle_update = handle_update
    _auto._polygon_focus_patch_installed = True


install()
