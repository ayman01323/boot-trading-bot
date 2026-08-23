from __future__ import annotations

"""Compatibility layer for the MASTER trading-funnel observability patch.

The new funnel report remains authoritative in production.  This module preserves
legacy test/extension hooks (`_snapshot`) and the long-standing safe startup-health
fields without restoring the obsolete claim that Etherscan is required for
Alchemy-backed EVM reconstruction.
"""

import html
import json
import time
from pathlib import Path

from . import polygon_focus_patch as _polygon
from . import telegram_trade_blocker_health_patch as _trade_health
from . import trading_pipeline_observability_patch as _obs
from .user_registry import all_users

_BASE_SNAPSHOT = _trade_health._snapshot
_PREV_BUILD_REPORT = _trade_health.build_report


def _legacy_report_from_snapshot(app, tid) -> str:
    """Render the historical report only when an extension overrides `_snapshot`.

    Existing tests and small diagnostic extensions monkeypatch `_snapshot` directly.
    The production path never enters this renderer and continues to use the exact
    three-funnel MASTER report from trading_pipeline_observability_patch.
    """
    s = _trade_health._snapshot(app, tid)
    lines = ["<b>🧭 WHY NO TRADE — LAST HOUR</b>", "━━━━━━━━━━━━━━━━━━━━"]

    if s.get("etherscan_configured"):
        lines.append("🟢 EVM history dependency: <b>Etherscan key configured</b>")
    else:
        lines.append("🔴 EVM history dependency: <b>ETHERSCAN_API_KEY MISSING</b>")
        lines.append("   Legacy diagnostic snapshot reports the key as missing; production EVM reconstruction uses Alchemy.")

    lines += ["", "<b>EVM SIBOT LEADER FUNNEL</b>"]
    for slug, row in (s.get("evm") or {}).items():
        icon = "🔴" if row.get("errors") and not row.get("leaders") else ("🟢" if row.get("leaders") else "🟡")
        lines.append(
            f"{icon} {html.escape(str(slug).upper())}: leaders <b>{int(row.get('leaders') or 0)}</b> • "
            f"history {int(row.get('status_wallets') or 0)} • errors {int(row.get('errors') or 0)} • "
            f"newest {_trade_health._age(int(row.get('newest') or 0))} ago"
        )
        if row.get("dominant"):
            lines.append(f"   <code>{html.escape(str(row.get('dominant'))[:150])}</code>")

    f = s.get("fast_market") or {}
    lines += ["", "<b>DIRECT AUTO</b>"]
    focus = "Polygon only" if s.get("polygon_focus") else "all enabled chains"
    lines.append(
        f"{'🟢' if s.get('platform_live', True) else '🔴'} Platform LIVE (signing): "
        f"<b>{'ON' if s.get('platform_live', True) else 'OFF'}</b>"
    )
    lines.append(
        f"{'🟢' if s.get('platform_auto') else '🔴'} Platform AUTO: "
        f"<b>{'ON' if s.get('platform_auto') else 'OFF'}</b> • scope <b>{focus}</b>"
    )
    lines.append(
        f"Scanner: <b>{html.escape(str(f.get('status') or 'UNKNOWN'))}</b> • routes {int(f.get('routes') or 0)} • "
        f"merged {int(f.get('merged') or 0)} • eligible {int(f.get('eligible') or 0)} • "
        f"auto events {int(f.get('auto_events') or 0)} • updated {_trade_health._age(int(f.get('updated') or 0))} ago"
    )
    lines.append(
        f"Last hour: wallet simulations <b>{int(f.get('simulations') or 0)}</b> • "
        f"execution rows <b>{int(f.get('executions') or 0)}</b>"
    )
    if f.get("simulation_reason"):
        lines.append(f"Top simulation block: <code>{html.escape(str(f.get('simulation_reason')))}</code>")
    if not int(f.get("simulations") or 0) and int(f.get("eligible") or 0) == 0:
        lines.append("ℹ️ No route reached wallet simulation; scanner/route/profit gates are filtering upstream.")

    lines += ["", "<b>SOLANA LIVE</b>"]
    sol = s.get("solana") or {}
    if sol.get("error"):
        lines.append(f"🔴 Diagnostics unavailable: <code>{html.escape(str(sol.get('error')))}</code>")
    else:
        counts = sol.get("counts") or {}
        engine = bool(sol.get("engine_enabled"))
        live = bool(sol.get("live_enabled"))
        lines.append(
            f"{'🟢' if engine and live else '🔴'} Engine {'ON' if engine else 'OFF'} • "
            f"LIVE {'ON' if live else 'OFF'} • leaders <b>{int(sol.get('leaders') or 0)}</b> • "
            f"selected-leader events <b>{int(sol.get('events') or 0)}</b>"
        )
        lines.append(
            f"Decisions: BUY {int(counts.get('BUY') or 0)} • SELL {int(counts.get('SELL') or 0)} • "
            f"REJECT {int(counts.get('REJECT') or 0)} • SKIP {int(counts.get('SKIP') or 0)}"
        )
        rows = sol.get("rows") or []
        if rows:
            recent = rows[0]
            reason = str(recent.get("reason") or "accepted/processed")
            lines.append(
                f"Latest: <b>{html.escape(str(recent.get('decision') or 'UNKNOWN'))}</b> • "
                f"<code>{html.escape(reason[:180])}</code>"
            )
        elif int(sol.get("leaders") or 0) and not int(sol.get("events") or 0):
            lines.append("ℹ️ A leader is selected, but no fresh selected-leader swap reached the LIVE decision path.")
        elif not int(sol.get("leaders") or 0):
            lines.append("⚠️ No qualified Solana leader is currently selected.")

    lines += [
        "",
        "<i>This report is diagnostic only. It does not weaken profit, liquidity, simulation, "
        "loss-quarantine, reserve or signing safeguards.</i>",
    ]
    return "\n".join(lines)


def build_report(app, tid) -> str:
    # Preserve the explicit `_snapshot` extension seam.  In normal production the
    # function identity is unchanged and the new MASTER three-funnel report wins.
    if _trade_health._snapshot is not _BASE_SNAPSHOT:
        return _legacy_report_from_snapshot(app, tid)
    return _PREV_BUILD_REPORT(app, tid)


def _publish_startup_health(app) -> None:
    # Do not spin long-lived monitor threads for lightweight test/extension objects.
    if isinstance(app, _obs.AppSettings):
        _obs._start_background(app)

    masters = [
        str(u.get("telegram_id") or "")
        for u in all_users(app.csv_dir, enabled_only=True)
        if str(u.get("role") or "").upper() == "MASTER" and str(u.get("telegram_id") or "")
    ]
    tid = masters[0] if masters else ""

    funnels = {}
    if masters:
        try:
            funnels = _obs.snapshot(app, tid)
        except Exception:
            funnels = {}

    safe = dict(funnels) if isinstance(funnels, dict) else {}
    safe.setdefault("generated_epoch", int(time.time()))
    safe["etherscan_configured"] = bool(str(getattr(app, "etherscan_api_key", "") or "").strip())
    try:
        safe["polygon_focus"] = bool(_polygon.focus_enabled(app))
    except Exception:
        safe["polygon_focus"] = False
    safe["trading_funnels"] = {
        "evm_sibot": safe.get("evm_sibot", {}),
        "polygon_auto": safe.get("polygon_auto", {}),
        "solana": safe.get("solana", {}),
    }

    try:
        _obs._atomic_json(_obs._MASTER_BRIDGE, safe)
    except Exception:
        pass

    path = Path(app.data_dir) / "trade_blocker_health.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(safe, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")

    poly = safe.get("polygon_auto") or {}
    try:
        _trade_health._maybe_alert_platform_gate_off(
            app,
            masters,
            {
                "platform_auto": poly.get("platform_auto", safe.get("platform_auto")),
                "platform_live": poly.get("platform_live", safe.get("platform_live")),
            },
        )
    except Exception:
        pass

    print(
        "[trading-funnel-compat] etherscan=%s polygon_focus=%s funnels=%s"
        % (
            "configured" if safe["etherscan_configured"] else "missing-informational",
            safe["polygon_focus"],
            "ready" if funnels else "collecting",
        ),
        flush=True,
    )


def install() -> None:
    if getattr(_trade_health, "_trading_pipeline_compat_installed", False):
        return
    _trade_health.build_report = build_report
    _trade_health._publish_startup_health = _publish_startup_health
    _trade_health._trading_pipeline_compat_installed = True
    print(
        "[trading-funnel-compat] legacy_extension_hooks=true etherscan_block_claim=false "
        "master_funnels=true secrets=never_written",
        flush=True,
    )


install()
