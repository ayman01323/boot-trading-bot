from __future__ import annotations

import html
from contextlib import closing
from decimal import Decimal

from . import solana_live_patch as _live
from . import solana_position_wallet_binding_patch as _binding
from . import solana_positive_edge_entry_gate_patch as _edge
from . import solana_sibot as _sol
from . import telegram_trade_blocker_health_patch as _trade
from .solana_wallet_store import SolanaWalletStore

_PREV_BUILD_REPORT = _trade.build_report


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
        if position.get("verified"):
            verified = html.escape(position.get("verified_balance_raw") or "0")
            balance_text = f"verified wallet raw <b>{verified}</b> across {int(position.get('wallets_checked') or 0)} wallet(s)"
        else:
            balance_text = "verified wallet raw <b>UNKNOWN</b> (RPC/wallet proof incomplete)"
        lines.append(f"🔴 Open LIVE position: <code>{pid}</code>")
        lines.append(f"   Mint: <code>{mint}</code>")
        lines.append(f"   Recorded raw <b>{recorded}</b> • {balance_text}")

    leaders = truth.get("leaders") or []
    if not leaders:
        lines.append("🔴 Leader edge gate: <b>NO SELECTED LEADER</b>")
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
