from __future__ import annotations

import csv
import html
import os
import threading
import time
from decimal import Decimal
from pathlib import Path

from . import sibot1_solana_live_bridge_patch as _bridge
from . import telegram_ui as _ui
from .solana_wallet_store import SolanaWalletStore
from .user_registry import is_master

# Gemini-only Solana control plane. It composes after the GPT-specific wrapper,
# so Gemini candidates use a separate control row while GPT and every other
# engine retain their existing controls. No risk, PoolCheck, quote, simulation,
# signer or execution threshold is weakened here.

_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_PROCESS_CANDIDATE = _bridge._process_candidate
_PREV_READINESS = _bridge.readiness
_PREV_START = _bridge._start

_TLS = threading.local()
_GEMINI_WORKER_STARTED = False
_GEMINI_WORKER_LOCK = threading.Lock()
_INSTALLED = False

CONTROL_HEADERS = ["telegram_id", "armed", "live_enabled", "auto_enabled", "updated_epoch"]
COMMANDS = {
    "/gemini_status",
    "/gemini_arm_live",
    "/gemini_auto",
    "/gemini_disarm",
    "/gemini_stop",
    "/geminisolstatus",
    "/geminisolarm",
    "/geminisollive",
    "/geminisolauto",
    "/geminisolstop",
}


def _bool(value, default=False) -> bool:
    if value in (None, ""):
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _control_path(app) -> Path:
    override = os.environ.get("GEMINI_SOLANA_CONTROL_FILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(app.csv_dir) / "sibot1" / "gemini_solana_live_control.csv"


def _rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    except Exception:
        return []


def _write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CONTROL_HEADERS)
        writer.writeheader()
        writer.writerows([{h: row.get(h, "") for h in CONTROL_HEADERS} for row in rows])
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def configured(app, tid) -> bool:
    wanted = str(tid)
    return any(str(row.get("telegram_id") or "") == wanted for row in _rows(_control_path(app)))


def control(app, tid) -> dict[str, str]:
    wanted = str(tid)
    for row in _rows(_control_path(app)):
        if str(row.get("telegram_id") or "") == wanted:
            return dict(row)
    return {
        "telegram_id": wanted,
        "armed": "false",
        "live_enabled": "false",
        "auto_enabled": "false",
        "updated_epoch": "0",
    }


def set_control(app, tid, **updates) -> dict[str, str]:
    wanted = str(tid)
    path = _control_path(app)
    rows = _rows(path)
    row = next((r for r in rows if str(r.get("telegram_id") or "") == wanted), None)
    if row is None:
        row = control(app, wanted)
        rows.append(row)
    for key, value in updates.items():
        if key in CONTROL_HEADERS and key != "telegram_id":
            row[key] = str(value)
    row["updated_epoch"] = str(int(time.time()))
    _write_rows(path, rows)
    return dict(row)


def _signer_vault_status(app, tid) -> tuple[bool, str, str]:
    try:
        store = SolanaWalletStore(app.csv_dir, app.data_dir)
        meta = store.get_meta(tid)
        wallet_id = meta.get("wallet_id")
        if not store.has_private_key(tid, wallet_id):
            return False, "encrypted Solana signer is not stored", ""
        address = str(meta.get("address") or "").strip()
        if not address:
            return False, "active Solana wallet address is missing", ""
        return True, "ready", address
    except Exception as exc:
        return False, f"{type(exc).__name__}", ""


def _balance_status(app, address: str) -> tuple[bool, Decimal, str]:
    if not str(address or "").strip():
        return False, Decimal(0), "wallet address unavailable"
    try:
        result = _bridge._sol._rpc(
            app,
            "getBalance",
            [str(address), {"commitment": "confirmed"}],
        ) or {}
        balance = Decimal(int(result.get("value") or 0)) / Decimal(1_000_000_000)
        return True, balance, "ready"
    except Exception as exc:
        detail = str(exc).replace("<", "").replace(">", "")[:180]
        return False, Decimal(0), f"{type(exc).__name__}: {detail}"


def readiness(app, tid) -> dict:
    ctl = control(app, tid)
    signer_ok, signer_detail, address = _signer_vault_status(app, tid)
    if signer_ok:
        rpc_ok, balance, rpc_detail = _balance_status(app, address)
    else:
        rpc_ok, balance, rpc_detail = False, Decimal(0), "signer not ready"
    account_ok, account_reason = _bridge._account_gate(app, tid)
    amount = _bridge._entry_size(_bridge.control(app, tid))
    funded = bool(rpc_ok and balance >= amount + _bridge.MIN_RESERVE_SOL)
    armed = _bool(ctl.get("armed"))
    live = _bool(ctl.get("live_enabled"))
    auto = _bool(ctl.get("auto_enabled"))
    return {
        "control": ctl,
        "signer_ready": signer_ok,
        "signer_detail": signer_detail,
        "rpc_ready": rpc_ok,
        "rpc_detail": rpc_detail,
        "balance_sol": balance,
        "funded": funded,
        "account_ready": account_ok,
        "account_reason": account_reason,
        "entry_size_sol": amount,
        "reserve_sol": _bridge.MIN_RESERVE_SOL,
        "entry_execution_active": bool(armed and live and auto and signer_ok and rpc_ok and funded and account_ok),
        "exit_execution_active": bool(armed and live and signer_ok and rpc_ok and account_ok),
        "chain": "solana",
        "engine_id": "gemini",
        "engine_isolated": True,
    }


def _readiness_engine_aware(app, tid):
    if str(getattr(_TLS, "engine_id", "") or "").lower() == "gemini" and configured(app, tid):
        return readiness(app, tid)
    return _PREV_READINESS(app, tid)


def _process_candidate_engine_aware(app, tid, candidate):
    engine = str(candidate.get("engine_id") or "").strip().lower()
    if engine != "gemini" or not configured(app, tid):
        return _PREV_PROCESS_CANDIDATE(app, tid, candidate)

    previous = getattr(_TLS, "engine_id", None)
    _TLS.engine_id = "gemini"
    try:
        return _PREV_PROCESS_CANDIDATE(app, tid, candidate)
    finally:
        if previous is None:
            try:
                delattr(_TLS, "engine_id")
            except AttributeError:
                pass
        else:
            _TLS.engine_id = previous


def _shared_live_worker_present(app) -> bool:
    try:
        return any(_bridge._bool(row.get("live_enabled")) for row in _bridge._rows(_bridge._control_path(app)))
    except Exception:
        return False


def _gemini_worker(app):
    time.sleep(20)
    while True:
        try:
            controls = [r for r in _rows(_control_path(app)) if _bool(r.get("live_enabled"))]
            if controls and not _shared_live_worker_present(app):
                candidates = _bridge._candidate_rows(app)
                for ctl in controls:
                    tid = str(ctl.get("telegram_id") or "")
                    if not tid:
                        continue
                    for candidate in candidates:
                        if str(candidate.get("engine_id") or "").lower() == "gemini":
                            _bridge._process_candidate(app, tid, candidate)
        except Exception as exc:
            print("[gemini-solana-control] worker", type(exc).__name__, str(exc)[:180])
        time.sleep(2)


def _start_with_gemini_control(app):
    global _GEMINI_WORKER_STARTED
    result = _PREV_START(app)
    with _GEMINI_WORKER_LOCK:
        if not _GEMINI_WORKER_STARTED:
            threading.Thread(
                target=_gemini_worker,
                args=(app,),
                daemon=True,
                name="sibot1-gemini-solana-control-worker",
            ).start()
            _GEMINI_WORKER_STARTED = True
            print("[gemini-solana-control] worker=true engine=gemini isolated=true")
    return result


def status_text(app, tid) -> str:
    r = readiness(app, tid)
    ctl = r["control"]
    icon = lambda value: "🟢" if value else "🔴"
    rpc_detail = ""
    if not r.get("rpc_ready"):
        rpc_detail = f"\nRPC detail: <code>{html.escape(str(r.get('rpc_detail') or '')[:220])}</code>"
    return "\n".join([
        "<b>🟢 GEMINI TRADING BOT — Solana Control</b>",
        "",
        "Scope: <b>Gemini candidates only</b>",
        "GPT/Grok/Claude controls: <b>UNCHANGED</b>",
        "",
        f"{icon(_bool(ctl.get('armed')))} Gemini ARMED: <b>{'YES' if _bool(ctl.get('armed')) else 'NO'}</b>",
        f"{icon(_bool(ctl.get('live_enabled')))} Gemini LIVE: <b>{'YES' if _bool(ctl.get('live_enabled')) else 'NO'}</b>",
        f"{icon(_bool(ctl.get('auto_enabled')))} Gemini AUTO: <b>{'YES' if _bool(ctl.get('auto_enabled')) else 'NO'}</b>",
        f"{icon(r.get('signer_ready'))} Signer vault: <b>{'READY' if r.get('signer_ready') else 'NOT READY'}</b>",
        f"{icon(r.get('rpc_ready'))} Solana RPC: <b>{'READY' if r.get('rpc_ready') else 'NOT READY'}</b>{rpc_detail}",
        f"{icon(r.get('account_ready'))} Account AUTO permission: <b>{'READY' if r.get('account_ready') else 'BLOCKED'}</b>",
        f"{icon(r.get('funded'))} Funding: <b>{r.get('balance_sol', Decimal(0)):.9f} SOL</b>",
        "",
        f"Current protected entry size: <b>{r.get('entry_size_sol')} SOL</b>",
        f"Untouched reserve: <b>{r.get('reserve_sol')} SOL</b>",
        "PoolCheck / reverse sellability / 3x stress / signed simulation: <b>UNCHANGED</b>",
        "",
        "<b>Gemini commands</b>",
        "<code>/gemini_status</code>",
        "<code>/gemini_arm_live CONFIRM</code>",
        "<code>/gemini_auto on CONFIRM</code>",
        "<code>/gemini_disarm</code>",
        "<code>/gemini_stop</code>",
    ])


def _notify(app, tid, text: str) -> None:
    try:
        _bridge._notify(app, tid, text)
    except Exception:
        pass


def _send_status(app, tid) -> None:
    _notify(app, tid, status_text(app, tid))


def _ready_for_live(app, tid) -> tuple[bool, dict]:
    r = readiness(app, tid)
    ok = bool(r.get("signer_ready") and r.get("rpc_ready") and r.get("funded") and r.get("account_ready"))
    return ok, r


def _command(app, tid, text: str) -> bool:
    parts = str(text or "").strip().split()
    if not parts:
        return False
    cmd = parts[0].lower().split("@", 1)[0]
    if cmd not in COMMANDS:
        return False
    if not is_master(app.csv_dir, tid):
        _notify(app, tid, "❌ <b>MASTER account required.</b>")
        return True

    if cmd in {"/gemini_status", "/geminisolstatus"}:
        _send_status(app, tid)
        return True

    if cmd in {"/gemini_disarm", "/gemini_stop", "/geminisolstop"}:
        set_control(app, tid, armed="false", live_enabled="false", auto_enabled="false")
        _notify(app, tid, "🛑 <b>Gemini Solana control is OFF.</b> Other engines are unchanged.")
        _send_status(app, tid)
        return True

    if cmd == "/gemini_arm_live":
        if len(parts) != 2 or parts[1].upper() != "CONFIRM":
            _notify(app, tid, "❌ Use exactly: <code>/gemini_arm_live CONFIRM</code>")
            return True
        ok, r = _ready_for_live(app, tid)
        if not ok:
            _notify(app, tid, "❌ <b>Gemini cannot ARM LIVE yet.</b> Signer, RPC, funding and account permission must all be ready.")
            _send_status(app, tid)
            return True
        set_control(app, tid, armed="true", live_enabled="true", auto_enabled="false")
        _notify(
            app,
            tid,
            "✅ <b>GEMINI ARMED + LIVE.</b>\n"
            "AUTO remains OFF. Existing PoolCheck, sellability, stress, simulation and execution gates remain mandatory.",
        )
        _send_status(app, tid)
        return True

    if cmd == "/gemini_auto":
        if len(parts) < 2 or parts[1].lower() not in {"on", "off"}:
            _notify(app, tid, "Usage: <code>/gemini_auto on CONFIRM</code> or <code>/gemini_auto off</code>")
            return True
        enable = parts[1].lower() == "on"
        if enable and (len(parts) != 3 or parts[2].upper() != "CONFIRM"):
            _notify(app, tid, "❌ Add <code>CONFIRM</code> to enable Gemini AUTO.")
            return True
        ctl = control(app, tid)
        if enable and not (_bool(ctl.get("armed")) and _bool(ctl.get("live_enabled"))):
            _notify(app, tid, "❌ Gemini must be ARMED + LIVE before AUTO can be enabled.")
            return True
        if enable:
            ok, _r = _ready_for_live(app, tid)
            if not ok:
                _notify(app, tid, "❌ Gemini signer, RPC, funding or account permission is not ready.")
                _send_status(app, tid)
                return True
        set_control(app, tid, auto_enabled="true" if enable else "false")
        _send_status(app, tid)
        return True

    # Advanced aliases retain the same three-stage control pattern as GPT.
    if len(parts) < 2 or parts[1].lower() not in {"on", "off"}:
        _notify(app, tid, f"Usage: <code>{cmd} on CONFIRM</code> or <code>{cmd} off</code>")
        return True
    enable = parts[1].lower() == "on"
    if enable and (len(parts) < 3 or parts[2].upper() != "CONFIRM"):
        _notify(app, tid, "❌ Add <code>CONFIRM</code> to enable a Gemini Solana control.")
        return True

    if cmd == "/geminisolarm":
        if enable:
            signer_ok, signer_detail, _address = _signer_vault_status(app, tid)
            account_ok, account_reason = _bridge._account_gate(app, tid)
            if not signer_ok:
                _notify(app, tid, "❌ <b>Gemini cannot arm:</b> signer vault is not ready.\n" f"<code>{html.escape(str(signer_detail)[:220])}</code>")
                return True
            if not account_ok:
                _notify(app, tid, "❌ <b>Gemini cannot arm:</b> account AUTO permission is blocked.\n" f"<code>{html.escape(str(account_reason)[:220])}</code>")
                return True
            set_control(app, tid, armed="true")
        else:
            set_control(app, tid, armed="false", live_enabled="false", auto_enabled="false")
    elif cmd == "/geminisollive":
        ctl = control(app, tid)
        if enable and not _bool(ctl.get("armed")):
            _notify(app, tid, "❌ Arm Gemini first.")
            return True
        if enable:
            ok, _r = _ready_for_live(app, tid)
            if not ok:
                _notify(app, tid, "❌ Gemini LIVE cannot enable yet.")
                _send_status(app, tid)
                return True
            set_control(app, tid, live_enabled="true")
        else:
            set_control(app, tid, live_enabled="false", auto_enabled="false")
    elif cmd == "/geminisolauto":
        ctl = control(app, tid)
        if enable and not (_bool(ctl.get("armed")) and _bool(ctl.get("live_enabled"))):
            _notify(app, tid, "❌ Gemini must be ARMED and LIVE before AUTO can be enabled.")
            return True
        if enable:
            ok, _r = _ready_for_live(app, tid)
            if not ok:
                _notify(app, tid, "❌ Gemini signer, RPC, funding or account permission is not ready.")
                _send_status(app, tid)
                return True
        set_control(app, tid, auto_enabled="true" if enable else "false")

    _send_status(app, tid)
    return True


def handle_update(app, update):
    message = update.get("message") or {}
    tid = (message.get("chat") or {}).get("id")
    text = str(message.get("text") or "").strip()
    if tid is not None and text and _command(app, tid, text):
        return
    return _PREV_HANDLE_UPDATE(app, update)


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _bridge.readiness = _readiness_engine_aware
    _bridge._process_candidate = _process_candidate_engine_aware
    _bridge._start = _start_with_gemini_control
    _ui.handle_update = handle_update
    _ui._sibot1_gemini_solana_control_installed = True
    _INSTALLED = True
    print(
        "[gemini-solana-control] installed=true engine=gemini isolated=true "
        "arm_live_rpc_required=true auto_separate=true other_engines=unchanged"
    )


install()
