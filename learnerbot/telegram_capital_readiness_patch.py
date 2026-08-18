from __future__ import annotations

import html
from decimal import Decimal
from pathlib import Path

from . import capital_dashboard as _cap
from . import telegram_dashboard_patch as _dash
from . import telegram_multi_wallet_manager_patch as _wallets
from . import telegram_solana_everywhere_compat_patch as _solcompat
from . import telegram_ui as _ui
from . import polygon_focus_patch as _polygon

_PREV_WALLET_HUB = _wallets.wallet_hub_page


def _on(v) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def _asset_text(asset: dict) -> str:
    symbol = html.escape(str(asset.get("symbol") or "ASSET"))
    amount = _cap._fmt_amount(Decimal(str(asset.get("balance") or 0)))
    usd = asset.get("usd_value")
    if usd is None:
        return f"{symbol} <b>{amount}</b> <i>(USD unavailable)</i>"
    return f"{symbol} <b>{amount}</b> ≈ <b>${Decimal(str(usd)):,.2f}</b>"


def _chain_details(app, tid, user, snap: dict, chain) -> list[str]:
    state = str(snap.get("trading_state") or "OFF").upper()
    native = Decimal(str(snap.get("native_balance") or 0))
    live_cfg = _cap.load_kv_scoped(Path(app.csv_dir) / "live_trading_settings.csv", chain.chain_id)
    auto_cfg = _cap.load_kv_scoped(Path(app.csv_dir) / "auto_trading_settings.csv", chain.chain_id)
    reserve_raw = _cap.user_setting(
        app.csv_dir, tid, chain.chain_id, "min_native_gas_reserve",
        live_cfg.get("min_native_gas_reserve", "0"),
    )
    reserve = max(Decimal(0), _cap._dec(reserve_raw, "0"))
    usable = max(Decimal(0), native - reserve)
    native_price = snap.get("assets", [{}])[0].get("usd_price") if snap.get("assets") else None
    usable_usd = usable * Decimal(str(native_price)) if native_price is not None else None
    gas_ready = bool(snap.get("rpc_ok")) and native >= reserve and (native > 0 or reserve == 0)

    user_live = _cap.user_bool(app.csv_dir, tid, chain.chain_id, "live_trading_enabled", False)
    user_auto = _cap.user_bool(app.csv_dir, tid, chain.chain_id, "auto_trading_enabled", False)
    mode = str(_cap.user_setting(app.csv_dir, tid, chain.chain_id, "recommendation_mode", "SHADOW") or "SHADOW").upper()
    platform_live = _on(live_cfg.get("trading_enabled"))
    platform_auto = _on(auto_cfg.get("auto_trading_enabled"))

    icon = "🟣" if str(chain.slug).lower() == "polygon" else ("🟢" if state == "AUTO" else "🔵" if state == "LIVE" else "⚪")
    lines = [f"{icon} <b>{html.escape(str(chain.name or chain.slug).upper())}</b> — state <b>{html.escape(state)}</b>"]
    if snap.get("error"):
        lines += [f"   RPC/gas: <b>❌ NOT READY</b> — {html.escape(str(snap.get('error'))[:120])}"]
        return lines

    assets = [x for x in snap.get("assets", []) if Decimal(str(x.get("balance") or 0)) > 0]
    if assets:
        lines.append("   Assets: " + " | ".join(_asset_text(x) for x in assets[:6]))
    else:
        lines.append("   Assets: <b>0</b> — priced capital <b>$0.00</b>")

    usable_usd_text = f" ≈ <b>${usable_usd:,.2f}</b>" if usable_usd is not None else ""
    lines.append(
        f"   Native usable: <b>{_cap._fmt_amount(usable)} {html.escape(chain.native_symbol)}</b>{usable_usd_text} | "
        f"gas reserve <b>{_cap._fmt_amount(reserve)} {html.escape(chain.native_symbol)}</b> | "
        f"gas <b>{'✅ READY' if gas_ready else '❌ LOW/NOT READY'}</b>"
    )
    lines.append(
        "   Gates: "
        f"user LIVE <b>{'ON' if user_live else 'OFF'}</b> | AUTO <b>{'ON' if user_auto else 'OFF'}</b> | mode <b>{html.escape(mode)}</b> | "
        f"platform LIVE <b>{'ON' if platform_live else 'OFF'}</b> | AUTO <b>{'ON' if platform_auto else 'OFF'}</b>"
    )
    focus = " | Polygon-only focus <b>ON</b>" if str(chain.slug).lower() == "polygon" and _polygon.focus_enabled(app) else ""
    lines.append(f"   Chain priced capital: <b>${Decimal(str(snap.get('capital_usd') or 0)):,.2f}</b>{focus}")
    return lines


def user_dashboard_text(app, telegram_id) -> str:
    d = _cap.user_dashboard_data(app, telegram_id)
    u = d["user"]
    tid = str(telegram_id)
    chain_map = {int(c.chain_id): c for c in _cap.load_chains(app, enabled_only=True)}
    L = [
        "<b>📊 MY CAPITAL &amp; P&amp;L — ALL CHAINS</b>",
        "━━━━━━━━━━━━",
        f"Account: <b>{html.escape((u.get('status') or '').upper())}</b> | plan <b>{html.escape(u.get('fee_plan_id') or '-')}</b>",
        "<i>Every enabled EVM chain is shown, even when its current balance is zero.</i>",
        "",
    ]
    if not d["wallets"]:
        L += ["No EVM wallet configured.", "Use <code>/wallets</code> to create or import one."]
    else:
        any_unpriced = False
        for wallet in d["wallets"]:
            active = _on(wallet.get("active"))
            L += [
                f"{'✅' if active else '▫️'} <b>{html.escape(wallet.get('label') or wallet.get('wallet_id') or 'Wallet')}</b>",
                f"<code>{html.escape(wallet.get('address') or '')}</code>",
            ]
            for snap in wallet.get("chains", []):
                chain = chain_map.get(int(snap.get("chain_id") or 0))
                if chain is None:
                    continue
                L.extend(_chain_details(app, tid, u, snap, chain))
                any_unpriced = any_unpriced or bool(snap.get("unpriced_assets"))
            L += [f"Wallet priced capital: <b>${Decimal(str(wallet.get('capital_usd') or 0)):,.2f}</b>", ""]

        perf = d["performance"]
        L += [
            f"<b>Total EVM priced capital: ${Decimal(str(d.get('capital_usd') or 0)):,.2f}</b>",
            f"Successful AUTO trades: <b>{perf.get('trades', 0)}</b>",
            f"Trading net after profit-share: <b>${Decimal(str(perf.get('net_usd') or 0)):,.2f}</b>",
            f"Platform fees: <b>${Decimal(str(perf.get('fees_usd') or 0)):,.2f}</b>",
        ]
        if any_unpriced:
            L.append("<i>Totals exclude non-zero assets for which a reliable USD price is unavailable.</i>")

    try:
        L += ["", *_solcompat._sol_user_section(app, telegram_id)]
    except Exception as exc:
        L += ["", "<b>🟣 SOLANA CAPITAL &amp; P&amp;L</b>", f"⚠️ unavailable: <code>{html.escape(type(exc).__name__)}</code>"]
    return "\n".join(L)


def wallet_hub_page(app, tid):
    text = _PREV_WALLET_HUB(app, tid)
    stale = (
        "⚠️ An imported Solana signing key is stored for future LIVE capability, but Solana SiBot remains "
        "SHADOW-only until its transaction signing/broadcast engine is separately enabled."
    )
    current = (
        "🟣 Solana LIVE capability is installed. LIVE trading still depends on the separate Solana LIVE switch, "
        "signing readiness, sufficient balance/reserve, and all trade-safety checks."
    )
    return text.replace(stale, current)


def install():
    _dash.user_dashboard_text = user_dashboard_text
    _wallets.wallet_hub_page = wallet_hub_page
    _ui.wallet_page = wallet_hub_page


install()
