from __future__ import annotations

import html
import json
import time
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from . import solana_emergency_liquidity_unwind_patch as _emergency
from . import solana_live_patch as _live
from . import solana_position_wallet_binding_patch as _binding
from . import solana_positive_edge_entry_gate_patch as _edge
from . import solana_sibot as _sol
from . import telegram_trade_blocker_health_patch as _trade
from .solana_wallet_store import SolanaWalletStore

_PREV_BUILD_REPORT = _trade.build_report
_SELECTOR_BRIDGE = Path("/var/tmp/boot/solana_leader_selector.json")


def _selector_truth(max_age_seconds: int = 300) -> dict:
    """Read the tiny sanitised selector bridge; never read the large history DB here."""
    try:
        payload = json.loads(_SELECTOR_BRIDGE.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return {}
        generated = int(payload.get("generated_epoch") or 0)
        if generated <= 0 or int(time.time()) - generated > max(30, int(max_age_seconds)):
            return {}
        failures = payload.get("first_failure_counts") or {}
        if not isinstance(failures, dict):
            failures = {}
        return {
            "generated_epoch": generated,
            "pool": max(0, int(payload.get("pool") or 0)),
            "qualified": max(0, int(payload.get("qualified") or 0)),
            "selected": max(0, int(payload.get("selected") or 0)),
            "failures": {
                str(reason)[:100]: max(0, int(count or 0))
                for reason, count in failures.items()
                if str(reason or "").strip()
            },
            "thresholds_unchanged": bool(payload.get("thresholds_unchanged", False)),
        }
    except Exception:
        return {}


def _wallet_truth(app, tid: str, cfg: dict) -> dict:
    trade, reserve = _live.live_limits(app, tid, cfg)
    result = {
        "signing_ready": False,
        "balance_sol": None,
        "trade_sol": trade,
        "reserve_sol": reserve,
        "minimum_sol": trade + reserve,
        "reason": "active wallet unavailable",
    }
    try:
        store = SolanaWalletStore(app.csv_dir, app.data_dir)
        meta = store.get_meta(tid)
        wallet_id = meta.get("wallet_id")
        address = str(meta.get("address") or "")
        result["signing_ready"] = bool(store.has_private_key(tid, wallet_id))
        if not result["signing_ready"]:
            result["reason"] = "active Solana wallet is PUBLIC ONLY"
        rpc = _sol._rpc(app, "getBalance", [address, {"commitment": "confirmed"}]) or {}
        result["balance_sol"] = Decimal(int(rpc.get("value") or 0)) / Decimal(1_000_000_000)
        if result["balance_sol"] < result["minimum_sol"]:
            result["reason"] = (
                f"wallet balance {result['balance_sol']:.9f} SOL is below "
                f"trade+reserve minimum {result['minimum_sol']:.9f} SOL"
            )
        elif result["signing_ready"]:
            result["reason"] = "wallet signing and minimum funding are ready"
    except Exception as exc:
        result["reason"] = f"{type(exc).__name__}: {str(exc)[:160]}"
    return result


def _liquidity_state(app, position_id: str) -> dict:
    """Observational state only; the DB row deliberately remains OPEN."""
    try:
        state = _emergency._load_backoff(app, str(position_id)) or {}
    except Exception:
        state = {}
    attempts = max(0, _sol._int(state.get("attempts"), 0))
    next_retry = max(0, _sol._int(state.get("next_retry"), 0) - int(time.time()))
    first_blocked = max(0, _sol._int(state.get("first_blocked_epoch"), 0))
    return {
        "label": "LIQUIDITY_STUCK" if attempts > 0 else "OPEN",
        "attempts": attempts,
        "retry_after_seconds": next_retry,
        "first_blocked_epoch": first_blocked,
    }


def _open_live_truth(app, tid: str) -> list[dict]:
    """Read-only proof of the LIVE rows that can block recovery exclusivity."""
    try:
        with closing(_sol.connect(app)) as conn:
            rows = [dict(row) for row in conn.execute(
                """SELECT position_id,mint,token_amount_raw,entry_ts,updated_at,exit_reason
                   FROM positions
                   WHERE telegram_id=? AND status='OPEN' AND mode='LIVE'
                   ORDER BY entry_ts""",
                (str(tid),),
            ).fetchall()]
    except Exception:
        return []
    if not rows:
        return []

    addresses: list[str] = []
    try:
        store = SolanaWalletStore(app.csv_dir, app.data_dir)
        seen = set()
        for wallet in store.list_wallets(tid, enabled_only=False):
            address = str(wallet.get("address") or "").strip()
            if address and address not in seen:
                seen.add(address)
                addresses.append(address)
    except Exception:
        addresses = []

    cfg = _sol.settings(app)
    slice_pcts = [
        str((fraction * Decimal(100)).normalize())
        for fraction in list(getattr(_emergency, "_SLICE_FRACTIONS", ()))
    ]
    emergency_limit = _emergency._emergency_limit(cfg)

    out = []
    for row in rows:
        mint = str(row.get("mint") or "").strip()
        verified = bool(addresses and mint)
        total_raw = 0
        checked = 0
        if verified:
            for address in addresses:
                try:
                    total_raw += int(_binding._token_balance_for_address(app, address, mint))
                    checked += 1
                except Exception:
                    verified = False
                    break
        liquidity = _liquidity_state(app, str(row.get("position_id") or ""))
        out.append({
            "position_id": str(row.get("position_id") or ""),
            "mint": mint,
            "recorded_raw": str(row.get("token_amount_raw") or "0"),
            "verified": verified,
            "verified_balance_raw": str(total_raw) if verified else "",
            "wallets_checked": checked if verified else 0,
            "entry_ts": int(row.get("entry_ts") or 0),
            "updated_at": int(row.get("updated_at") or 0),
            "exit_reason": str(row.get("exit_reason") or ""),
            "liquidity_state": liquidity["label"],
            "liquidity_attempts": liquidity["attempts"],
            "liquidity_retry_after_seconds": liquidity["retry_after_seconds"],
            "liquidity_first_blocked_epoch": liquidity["first_blocked_epoch"],
            "safe_slice_percentages": slice_pcts,
            "emergency_limit_bps": str(emergency_limit),
        })
    return out


def gate_snapshot(app, tid: str) -> dict:
    cfg = _sol.settings(app)
    wallet = _wallet_truth(app, str(tid), cfg)

    try:
        platform_ok, platform_reason, platform_metrics, recovery = _edge._platform_amount_gate(app, cfg)
    except Exception as exc:
        platform_ok, platform_reason, platform_metrics, recovery = (
            False,
            f"{type(exc).__name__}: {str(exc)[:180]}",
            {},
            False,
        )

    leaders = []
    try:
        for row in _sol.leader_rows(app, str(tid)):
            wallet_address = str(row.get("wallet") or "")
            try:
                ok, reason, metrics = _edge._edge_ok(app, wallet_address, cfg)
            except Exception as exc:
                ok, reason, metrics = False, f"{type(exc).__name__}: {str(exc)[:160]}", {}
            leaders.append(
                {
                    "rank": int(row.get("rank") or 0),
                    "wallet": wallet_address,
                    "ok": bool(ok),
                    "reason": str(reason or ""),
                    "median_return_pct": str(metrics.get("median_return_pct") or ""),
                    "recent_median_return_pct": str(metrics.get("recent_median_return_pct") or ""),
                }
            )
    except Exception:
        leaders = []

    return {
        "wallet": wallet,
        "platform_ok": bool(platform_ok),
        "platform_reason": str(platform_reason or ""),
        "platform_metrics": platform_metrics or {},
        "recovery_canary": bool(recovery),
        "open_live_positions": _open_live_truth(app, str(tid)),
        "leaders": leaders,
        "selector_truth": _selector_truth(),
    }


def _short_wallet(value: str) -> str:
    value = str(value or "")
    if len(value) <= 18:
        return value
    return f"{value[:8]}…{value[-6:]}"


def build_report_with_gate_truth(app, tid) -> str:
    base = _PREV_BUILD_REPORT(app, tid).rstrip()
    try:
        truth = gate_snapshot(app, str(tid))
    except Exception as exc:
        return base + f"\n\n<b>🔎 SOLANA PREFLIGHT TRUTH</b>\n🔴 unavailable: <code>{html.escape(type(exc).__name__)}</code>"

    wallet = truth["wallet"]
    bal = wallet.get("balance_sol")
    bal_text = "unavailable" if bal is None else f"{bal:.9f} SOL"
    wallet_ok = bool(wallet.get("signing_ready")) and bal is not None and bal >= wallet.get("minimum_sol", Decimal(0))

    lines = ["", "<b>🔎 SOLANA PREFLIGHT TRUTH</b>"]
    lines.append(
        f"{'🟢' if wallet_ok else '🔴'} Wallet: "
        f"{'SIGNING READY' if wallet.get('signing_ready') else 'NOT SIGNING READY'} • "
        f"balance <b>{html.escape(bal_text)}</b> • minimum <b>{wallet.get('minimum_sol')} SOL</b>"
    )
    if not wallet_ok:
        lines.append(f"   <code>{html.escape(str(wallet.get('reason') or '')[:180])}</code>")

    platform_metrics = truth.get("platform_metrics") or {}
    pf = platform_metrics.get("profit_factor")
    metric = f" • realised PF <b>{html.escape(str(pf))}</b>" if pf not in {None, ""} else ""
    lines.append(
        f"{'🟢' if truth['platform_ok'] else '🔴'} Platform amount-profit gate: "
        f"<b>{'PASS' if truth['platform_ok'] else 'BLOCK'}</b>{metric}"
    )
    lines.append(f"   <code>{html.escape(truth['platform_reason'][:200])}</code>")
    if truth.get("recovery_canary"):
        lines.append("   🟡 Recovery mode: one canary BUY may proceed if every other safety check passes.")

    for position in (truth.get("open_live_positions") or [])[:3]:
        pid = html.escape(position.get("position_id") or "")
        mint = html.escape(position.get("mint") or "")
        recorded = html.escape(position.get("recorded_raw") or "0")
        liquidity_state = str(position.get("liquidity_state") or "OPEN")
        if position.get("verified"):
            verified = html.escape(position.get("verified_balance_raw") or "0")
            balance_text = f"verified wallet raw <b>{verified}</b> across {int(position.get('wallets_checked') or 0)} wallet(s)"
        else:
            balance_text = "verified wallet raw <b>UNKNOWN</b> (RPC/wallet proof incomplete)"
        if liquidity_state == "LIQUIDITY_STUCK":
            lines.append(f"🔴 LIVE position — <b>LIQUIDITY_STUCK</b>: <code>{pid}</code>")
        else:
            lines.append(f"🔴 Open LIVE position: <code>{pid}</code>")
        lines.append(f"   Mint: <code>{mint}</code>")
        lines.append(f"   Recorded raw <b>{recorded}</b> • {balance_text}")
        if liquidity_state == "LIQUIDITY_STUCK":
            slices = "/".join(position.get("safe_slice_percentages") or []) or "100/75/50/25"
            limit_pct = Decimal(str(position.get("emergency_limit_bps") or "500")) / Decimal(100)
            attempts = int(position.get("liquidity_attempts") or 0)
            retry = int(position.get("liquidity_retry_after_seconds") or 0)
            lines.append(
                f"   Liquidity: <b>LIQUIDITY_STUCK</b> • attempts <b>{attempts}</b> • "
                f"safe slices <b>{html.escape(slices)}%</b> • hard ceiling <b>{limit_pct:.2f}%</b>"
            )
            lines.append(
                f"   Remains <b>OPEN</b> for recovery/risk/exposure accounting"
                + (f" • next automatic retry in <b>{retry}s</b>" if retry > 0 else "")
            )

    leaders = truth.get("leaders") or []
    if not leaders:
        lines.append("🔴 Leader edge gate: <b>NO SELECTED LEADER</b>")
        selector = truth.get("selector_truth") or {}
        if selector:
            lines.append(
                f"   Selector: pool <b>{selector.get('pool', 0)}</b> • "
                f"qualified <b>{selector.get('qualified', 0)}</b> • selected <b>{selector.get('selected', 0)}</b>"
            )
            failures = selector.get("failures") or {}
            for reason, count in sorted(failures.items(), key=lambda item: (-int(item[1]), item[0]))[:3]:
                lines.append(f"   ↳ <code>{html.escape(str(reason))}</code>: <b>{int(count)}</b>")
    else:
        for leader in leaders[:3]:
            lines.append(
                f"{'🟢' if leader['ok'] else '🔴'} Leader #{leader['rank']} "
                f"<code>{html.escape(_short_wallet(leader['wallet']))}</code>: "
                f"<b>{'PASS' if leader['ok'] else 'BLOCK'}</b>"
            )
            lines.append(f"   <code>{html.escape(leader['reason'][:180])}</code>")

    lines.append("<i>These checks are read-only and do not bypass or modify execution safeguards.</i>")
    return base + "\n" + "\n".join(lines)


def install():
    if getattr(_trade, "_solana_trade_gate_truth_installed", False):
        return
    _trade.build_report = build_report_with_gate_truth
    _trade._solana_trade_gate_truth_installed = True


install()
