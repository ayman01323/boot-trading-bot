from __future__ import annotations

import csv
from pathlib import Path

from . import sibot1_live_bridge_patch as _bridge
from . import telegram_ui as _ui
from . import live_executor as _live
from .operator_control import set_kv
from .user_registry import is_master, require_user, user_bool

BASE_CHAIN_ID = 8453


def _bool(value, default=False):
    if value in (None, ""):
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _rows(path: Path):
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except Exception:
        return []


def _scope_gate(path: Path, key: str, chain_id: int) -> dict:
    """Return fail-closed global + chain gate semantics.

    A chain-specific TRUE must never override a global emergency FALSE. A missing
    chain row inherits TRUE relative to the global gate, preserving the existing
    wildcard-default behaviour while making the emergency gate authoritative.
    """
    rows = _rows(path)
    global_seen = False
    global_value = False
    chain_seen = False
    chain_value = True

    for row in rows:
        if (row.get("setting") or "").strip() != key:
            continue
        scope = (row.get("chain_id") or "*").strip()
        if scope in {"", "*", "0"}:
            global_seen = True
            global_value = _bool(row.get("value"), False)

    for row in rows:
        if (row.get("setting") or "").strip() != key:
            continue
        if (row.get("chain_id") or "").strip() == str(chain_id):
            chain_seen = True
            chain_value = _bool(row.get("value"), False)

    if not global_seen:
        global_value = False
    if not chain_seen:
        chain_value = True

    return {
        "global": bool(global_value),
        "chain": bool(chain_value),
        "chain_explicit": bool(chain_seen),
        "effective": bool(global_value and chain_value),
    }


def _platform_gates(app, tid) -> dict:
    chain = _bridge._base_chain(app)
    if chain is None:
        return {"ready": False, "reason": "Base chain is not configured"}
    try:
        user = require_user(app.csv_dir, tid, active=True, chain_slug=chain.slug)
    except Exception as exc:
        return {"ready": False, "reason": str(exc)}

    live = _scope_gate(Path(app.csv_dir) / "live_trading_settings.csv", "trading_enabled", chain.chain_id)
    auto = _scope_gate(Path(app.csv_dir) / "auto_trading_settings.csv", "auto_trading_enabled", chain.chain_id)
    user_live = user_bool(app.csv_dir, tid, chain.chain_id, "live_trading_enabled", False)
    user_auto = user_bool(app.csv_dir, tid, chain.chain_id, "auto_trading_enabled", False)
    can_auto = _bool(user.get("can_auto_trade"), True)
    ready = bool(live["effective"] and auto["effective"] and user_live and user_auto and can_auto)
    return {
        "ready": ready,
        "platform_live": live["effective"],
        "platform_auto": auto["effective"],
        "global_live": live["global"],
        "base_live": live["chain"],
        "base_live_explicit": live["chain_explicit"],
        "global_auto": auto["global"],
        "base_auto": auto["chain"],
        "base_auto_explicit": auto["chain_explicit"],
        "user_live": user_live,
        "user_auto": user_auto,
        "can_auto": can_auto,
        "reason": "ok" if ready else "one or more global/Base/user LIVE/AUTO gates are OFF",
    }


def _icon(value):
    return "🟢" if value else "🔴"


def status_text(app, tid):
    r = _bridge.readiness(app, tid)
    ctl = r["control"]
    p = r["platform"]
    base_live_label = "ON" if p.get("base_live") else "OFF"
    base_auto_label = "ON" if p.get("base_auto") else "OFF"
    if not p.get("base_live_explicit"):
        base_live_label = "INHERIT"
    if not p.get("base_auto_explicit"):
        base_auto_label = "INHERIT"

    return "\n".join([
        "<b>🚦 SiBot 1 — LIVE Readiness</b>",
        _bridge._sibot1.DIV,
        "",
        "<b>Protected execution bridge:</b> Base/EVM canary",
        "AI private-key access: <b>OFF</b>",
        "Solana SiBot 1 LIVE: <b>NOT ENABLED IN THIS CANARY</b>",
        "",
        f"{_icon(_bool(ctl.get('armed')))} SiBot 1 ARMED: <b>{'YES' if _bool(ctl.get('armed')) else 'NO'}</b>",
        f"{_icon(_bool(ctl.get('live_enabled')))} SiBot 1 LIVE: <b>{'YES' if _bool(ctl.get('live_enabled')) else 'NO'}</b>",
        f"{_icon(_bool(ctl.get('auto_enabled')))} SiBot 1 AUTO entries: <b>{'YES' if _bool(ctl.get('auto_enabled')) else 'NO'}</b>",
        f"{_icon(r.get('signer_ready'))} EVM signer vault: <b>{'READY' if r.get('signer_ready') else 'NOT READY'}</b>",
        "",
        "<b>Platform / Base gates</b>",
        f"{_icon(p.get('global_live'))} Global Platform LIVE",
        f"{_icon(p.get('base_live'))} Base LIVE scope: <b>{base_live_label}</b>",
        f"{_icon(p.get('platform_live'))} Effective Base LIVE",
        f"{_icon(p.get('global_auto'))} Global Platform AUTO",
        f"{_icon(p.get('base_auto'))} Base AUTO scope: <b>{base_auto_label}</b>",
        f"{_icon(p.get('platform_auto'))} Effective Base AUTO",
        f"{_icon(p.get('user_live'))} User LIVE",
        f"{_icon(p.get('user_auto'))} User AUTO",
        f"{_icon(p.get('can_auto'))} Account AUTO permission",
        "",
        f"Fixed Base canary size: <b>{_bridge._fixed_entry_size(ctl)} ETH</b> (hard maximum {_bridge.MAX_ENTRY_NATIVE} ETH)",
        f"Maximum SiBot 1 LIVE positions: <b>{_bridge.MAX_OPEN_POSITIONS}</b>",
        f"Maximum signal age: <b>{_bridge.MAX_SIGNAL_AGE_SECONDS}s</b>",
        "",
        f"{'🟢' if r.get('entry_execution_active') else '🔴'} <b>Real-money SiBot 1 entry execution: {'READY/ACTIVE' if r.get('entry_execution_active') else 'OFF/BLOCKED'}</b>",
        "",
        "<b>Manual commands</b>",
        "<code>/platformlive on CONFIRM</code> — global emergency LIVE gate",
        "<code>/sibot1base on CONFIRM</code> — Base chain LIVE scope",
        "<code>/sibot1arm on CONFIRM</code>",
        "<code>/sibot1live on CONFIRM</code>",
        "<code>/sibot1auto on CONFIRM</code>",
        "<code>/sibot1stop</code> — stop new entries; LIVE exits remain available",
    ])


_PREV_COMMAND = _bridge._command


def _command(app, tid, text: str) -> bool:
    parts = str(text or "").strip().split()
    if not parts:
        return False
    cmd = parts[0].lower().split("@", 1)[0]
    if cmd != "/sibot1base":
        return _PREV_COMMAND(app, tid, text)

    if not is_master(app.csv_dir, tid):
        _ui._send(app, tid, "❌ MASTER account required.")
        return True
    if len(parts) < 2 or parts[1].lower() not in {"on", "off"}:
        _ui._send(app, tid, "Usage: <code>/sibot1base on CONFIRM</code> or <code>/sibot1base off</code>")
        return True
    enable = parts[1].lower() == "on"
    if enable and (len(parts) < 3 or parts[2].upper() != "CONFIRM"):
        _ui._send(app, tid, "❌ Add <code>CONFIRM</code> to enable the Base LIVE scope.")
        return True

    set_kv(
        Path(app.csv_dir) / "live_trading_settings.csv",
        "trading_enabled",
        "true" if enable else "false",
        "Base chain LIVE permission; global Platform LIVE remains an independent mandatory gate",
        chain_id=BASE_CHAIN_ID,
    )
    _ui._send(app, tid, status_text(app, tid), _bridge.live_keyboard())
    return True


# Protect all EVM LiveTrader use from a chain-specific TRUE bypassing the global
# emergency OFF. Existing side/user/manual settings remain independently enforced.
def _require_enabled(self, side: str):
    live = _scope_gate(Path(self.app.csv_dir) / "live_trading_settings.csv", "trading_enabled", int(self.chain.chain_id))
    if not live["global"]:
        raise _live.LiveTradingError("Global Platform LIVE trading gate is OFF. MASTER must enable it.")
    if not live["chain"]:
        raise _live.LiveTradingError(f"{self.chain.name} LIVE trading scope is OFF.")
    if self.telegram_id is not None:
        from .user_registry import user_bool as _user_bool
        if not _user_bool(self.app.csv_dir, self.telegram_id, self.chain.chain_id, "live_trading_enabled", False):
            raise _live.LiveTradingError("Your LIVE trading switch is OFF. Use /live on CONFIRM.")
    if side == "BUY" and not _bool(self.settings.get("manual_buy_enabled"), True):
        raise _live.LiveTradingError("Manual BUY is disabled in live_trading_settings.csv")
    if side == "SELL" and not _bool(self.settings.get("manual_sell_enabled"), True):
        raise _live.LiveTradingError("Manual SELL is disabled in live_trading_settings.csv")


def install():
    _bridge._platform_gates = _platform_gates
    _bridge.status_text = status_text
    _bridge._command = _command
    _live.LiveTrader._require_enabled = _require_enabled
    print("[sibot1-live-gates] global-off-authoritative=true base-scope-visible=true")


install()
