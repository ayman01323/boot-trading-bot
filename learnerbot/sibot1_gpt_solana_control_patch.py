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

# GPT-only Solana control plane.
#
# This deliberately does not replace the legacy/shared Solana control file. A GPT
# candidate uses this control only after the MASTER user explicitly creates a GPT
# control row with one of the /gptsol* commands. Until then, legacy behaviour is
# unchanged. Once configured, GPT is isolated from the shared ARMED/LIVE/AUTO row;
# Gemini/Grok/other engines continue to use their existing controls.
#
# ARM proves only that the encrypted signer vault exists and the account has AUTO
# permission. RPC reachability and funding are reported separately and remain
# mandatory for LIVE/AUTO. This prevents a provider HTTP 401 from being falsely
# presented as "signer vault not ready" while keeping execution fail-closed.

_PREV_HANDLE_UPDATE = _ui.handle_update
_PREV_PROCESS_CANDIDATE = _bridge._process_candidate
_PREV_READINESS = _bridge.readiness
_PREV_START = _bridge._start

_TLS = threading.local()
_GPT_WORKER_STARTED = False
_GPT_WORKER_LOCK = threading.Lock()
_INSTALLED = False

CONTROL_HEADERS = [
    "telegram_id",
    "armed",
    "live_enabled",
    "auto_enabled",
    "updated_epoch",
]
COMMANDS = {
    "/gptsolarm",
    "/gptsollive",
    "/gptsolauto",
    "/gptsolstop",
    "/gptsolstatus",
}


def _bool(value, default=False) -> bool:
    if value in (None, ""):
        return bool(default)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _control_path(app) -> Path:
    return Path(app.csv_dir) / "sibot1" / "gpt_solana_live_control.csv"


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
    """Check encrypted signer presence only; never make an RPC request here."""
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

    # Sizing remains owned by the existing protected bridge. This patch changes
    # engine control/isolation only; it does not change trade size.
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
        "entry_execution_active": bool(
            armed and live and auto and signer_ok and rpc_ok and funded and account_ok
        ),
        "exit_execution_active": bool(
            armed and live and signer_ok and rpc_ok and account_ok
        ),
        "chain": "solana",
        "engine_id": "gpt",
        "engine_isolated": True,
    }


def _readiness_engine_aware(app, tid):
    if str(getattr(_TLS, "engine_id", "") or "").lower() == "gpt" and configured(app, tid):
        return readiness(app, tid)
    return _PREV_READINESS(app, tid)


def _process_candidate_engine_aware(app, tid, candidate):
    engine = str(candidate.get("engine_id") or "").strip().lower()
    if engine != "gpt" or not configured(app, tid):
        return _PREV_PROCESS_CANDIDATE(app, tid, candidate)

    previous = getattr(_TLS, "engine_id", None)
    _TLS.engine_id = "gpt"
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
        return any(
            _bridge._bool(row.get("live_enabled"))
            for row in _bridge._rows(_bridge._control_path(app))
        )
    except Exception:
        return False


def _gpt_worker(app):
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
                        if str(candidate.get("engine_id") or "").lower() == "gpt":
                            _bridge._process_candidate(app, tid, candidate)
        except Exception as exc:
            print("[gpt-solana-control] worker", type(exc).__name__, str(exc)[:180])
        time.sleep(2)


def _start_with_gpt_control(app):
    global _GPT_WORKER_STARTED
    result = _PREV_START(app)
    with _GPT_WORKER_LOCK:
        if not _GPT_WORKER_STARTED:
            threading.Thread(
                target=_gpt_worker,
                args=(app,),
                daemon=True,
                name="sibot1-gpt-solana-control-worker",
            ).start()
            _GPT_WORKER_STARTED = True
            print("[gpt-solana-control] worker=true engine=gpt isolated=true")
    return result


def status_text(app, tid) -> str:
    r = readiness(app, tid)
    ctl = r["control"]
    icon = lambda value: "🟢" if value else "🔴"
    rpc_detail = ""
    if not r.get("rpc_ready"):
        rpc_detail = f"\nRPC detail: <code>{html.escape(str(r.get('rpc_detail') or '')[:220])}</code>"
    return "\n".join([
        "<b>🟣 GPT BOT — Solana Control</b>",
        "",
        "Scope: <b>GPT candidates only</b>",
        "Gemini/Grok/Claude controls: <b>UNCHANGED</b>",
        "",
        f"{icon(_bool(ctl.get('armed')))} GPT ARMED: <b>{'YES' if _bool(ctl.get('armed')) else 'NO'}</b>",
        f"{icon(_bool(ctl.get('live_enabled')))} GPT LIVE: <b>{'YES' if _bool(ctl.get('live_enabled')) else 'NO'}</b>",
        f"{icon(_bool(ctl.get('auto_enabled')))} GPT AUTO: <b>{'YES' if _bool(ctl.get('auto_enabled')) else 'NO'}</b>",
        f"{icon(r.get('signer_ready'))} Signer vault: <b>{'READY' if r.get('signer_ready') else 'NOT READY'}</b>",
        f"{icon(r.get('rpc_ready'))} Solana RPC: <b>{'READY' if r.get('rpc_ready') else 'NOT READY'}</b>{rpc_detail}",
        f"{icon(r.get('account_ready'))} Account AUTO permission: <b>{'READY' if r.get('account_ready') else 'BLOCKED'}</b>",
        f"{icon(r.get('funded'))} Funding: <b>{r.get('balance_sol', Decimal(0)):.9f} SOL</b>",
        "",
        f"Current protected entry size: <b>{r.get('entry_size_sol')} SOL</b>",
        f"Untouched reserve: <b>{r.get('reserve_sol')} SOL</b>",
        "PoolCheck / reverse sellability / 3x stress / signed simulation: <b>UNCHANGED</b>",
        "",
        "<b>GPT-only commands</b>",
        "<code>/gptsolarm on CONFIRM</code>",
        "<code>/gptsollive on CONFIRM</code>",
        "<code>/gptsolauto on CONFIRM</code>",
        "<code>/gptsolstop</code>",
        "<code>/gptsolstatus</code>",
    ])


def _notify(app, tid, text: str) -> None:
    try:
        _bridge._notify(app, tid, text)
    except Exception:
        pass


def _send_status(app, tid) -> None:
    _notify(app, tid, status_text(app, tid))


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

    if cmd == "/gptsolstatus":
        _send_status(app, tid)
        return True
    if cmd == "/gptsolstop":
        set_control(app, tid, armed="false", live_enabled="false", auto_enabled="false")
        _notify(app, tid, "🛑 <b>GPT Solana control stopped.</b> Other engines are unchanged.")
        _send_status(app, tid)
        return True

    if len(parts) < 2 or parts[1].lower() not in {"on", "off"}:
        _notify(app, tid, f"Usage: <code>{cmd} on CONFIRM</code> or <code>{cmd} off</code>")
        return True
    enable = parts[1].lower() == "on"
    if enable and (len(parts) < 3 or parts[2].upper() != "CONFIRM"):
        _notify(app, tid, "❌ Add <code>CONFIRM</code> to enable a GPT Solana control.")
        return True

    if cmd == "/gptsolarm":
        if enable:
            signer_ok, signer_detail, _address = _signer_vault_status(app, tid)
            account_ok, account_reason = _bridge._account_gate(app, tid)
            if not signer_ok:
                _notify(
                    app,
                    tid,
                    "❌ <b>GPT cannot arm:</b> encrypted Solana signer vault is not ready.\n"
                    f"<code>{html.escape(str(signer_detail)[:220])}</code>",
                )
                return True
            if not account_ok:
                _notify(
                    app,
                    tid,
                    "❌ <b>GPT cannot arm:</b> account AUTO permission is blocked.\n"
                    f"<code>{html.escape(str(account_reason)[:220])}</code>",
                )
                return True
            set_control(app, tid, armed="true")
            _notify(
                app,
                tid,
                "✅ <b>GPT Solana ARMED.</b>\n"
                "This arms GPT only. RPC/funding are checked separately before LIVE execution.",
            )
        else:
            set_control(app, tid, armed="false", live_enabled="false", auto_enabled="false")

    elif cmd == "/gptsollive":
        ctl = control(app, tid)
        if enable and not _bool(ctl.get("armed")):
            _notify(app, tid, "❌ Arm GPT first with <code>/gptsolarm on CONFIRM</code>.")
            return True
        if enable:
            r = readiness(app, tid)
            if not (r.get("signer_ready") and r.get("rpc_ready") and r.get("funded") and r.get("account_ready")):
                _notify(
                    app,
                    tid,
                    "❌ <b>GPT LIVE cannot enable yet.</b> Signer, RPC, funding and account permission must all be ready.",
                )
                _send_status(app, tid)
                return True
            set_control(app, tid, live_enabled="true")
        else:
            set_control(app, tid, live_enabled="false", auto_enabled="false")

    elif cmd == "/gptsolauto":
        ctl = control(app, tid)
        if enable and not (_bool(ctl.get("armed")) and _bool(ctl.get("live_enabled"))):
            _notify(app, tid, "❌ GPT must be ARMED and LIVE before AUTO can be enabled.")
            return True
        if enable:
            r = readiness(app, tid)
            if not (r.get("signer_ready") and r.get("rpc_ready") and r.get("funded") and r.get("account_ready")):
                _notify(app, tid, "❌ GPT signer, RPC, funding or account permission is not ready.")
                _send_status(app, tid)
                return True
            set_control(app, tid, auto_enabled="true")
        else:
            set_control(app, tid, auto_enabled="false")

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
    _bridge._start = _start_with_gpt_control
    _ui.handle_update = handle_update
    _ui._sibot1_gpt_solana_control_installed = True
    _INSTALLED = True
    print(
        "[gpt-solana-control] installed=true engine=gpt isolated=true "
        "arm_rpc_independent=true live_rpc_required=true other_engines=unchanged"
    )


install()
