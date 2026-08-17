from __future__ import annotations

import html
import threading
import time
from contextlib import closing
from decimal import Decimal

from . import hourly_capital_alert_patch as _hourly
from . import sibot as _sibot
from .config import load_chains
from .live_executor import LiveTrader

_ORIGINAL_REFRESH_RANKINGS = _sibot.refresh_rankings
_ORIGINAL_PROCESS_LEADER_EVENT = _sibot.process_leader_event
_ORIGINAL_HOURLY = _hourly.build_hourly_capital_alert
_LOCK = threading.Lock()
_SENT: dict[str, float] = {}


def _d(v, default="0") -> Decimal:
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal(str(default))


def _short(v: str) -> str:
    v = str(v or "")
    return v if len(v) <= 18 else f"{v[:8]}…{v[-6:]}"


def _reserve(trader: LiveTrader) -> Decimal:
    return max(Decimal(0), _d(trader.settings.get("min_native_gas_reserve"), "0.005"))


def _gas_need(trader: LiveTrader, cfg: dict, side: str) -> Decimal:
    reserve = _reserve(trader)
    try:
        estimated = max(Decimal(0), _sibot._estimated_gas_native(trader, cfg, side))
    except Exception:
        estimated = Decimal(0)
    return max(reserve, estimated)


def _buy_readiness_issues(native: Decimal, gas_need: Decimal, min_trade: Decimal, planned_amount: Decimal) -> list[str]:
    issues = []
    if native < gas_need:
        issues.append(f"LOW_GAS:{gas_need - native}")
    usable = max(Decimal(0), native - gas_need)
    if usable < min_trade:
        issues.append(f"MISSING_NATIVE:{min_trade - usable}")
    return issues


def _exit_readiness_issues(native: Decimal, gas_need: Decimal, token_raw: int, required_raw: int) -> list[str]:
    issues = []
    if native < gas_need:
        issues.append(f"LOW_GAS:{gas_need - native}")
    if int(token_raw) < int(required_raw):
        issues.append(f"MISSING_TOKEN:{int(required_raw) - int(token_raw)}")
    return issues


def _notify_once(app, tid, key: str, text: str, ttl=3600):
    now = time.time()
    cache_key = f"{tid}|{key}"
    with _LOCK:
        old = _SENT.get(cache_key, 0)
        if now - old < ttl:
            return False
        _SENT[cache_key] = now
        if len(_SENT) > 5000:
            cutoff = now - 86400
            for k in list(_SENT):
                if _SENT.get(k, 0) < cutoff:
                    _SENT.pop(k, None)
    _sibot._notify(app, tid, text)
    return True


def _leader_warning(app, tid, chain, leader: dict):
    cfg = _sibot.user_settings(app, tid, chain.chain_id)
    if not _sibot._bool(cfg.get("enabled"), False):
        return
    try:
        trader = LiveTrader(app, chain.slug, telegram_id=tid)
    except Exception as exc:
        _notify_once(app, tid, f"leader-wallet:{chain.chain_id}:{leader['wallet']}", "\n".join([
            "🚨 <b>SiBot Leader — Wallet Not Ready</b>", "━━━━━━━━━━━━━━━━━━━━",
            f"🌐 {html.escape(chain.name)}",
            f"🏆 Leader #{int(leader.get('rank') or 0)} <code>{html.escape(_short(leader.get('wallet')))}</code>",
            "❌ No usable trading wallet/RPC execution context for this chain.",
            f"<code>{html.escape(str(exc)[:180])}</code>",
            "⚠️ SiBot will study the leader but cannot execute a LIVE trade until wallet readiness is fixed.",
        ]), ttl=6 * 3600)
        return
    native = trader.native_balance()
    gas_need = _gas_need(trader, cfg, "BUY")
    min_trade = max(Decimal(0), _d(cfg.get("min_trade_native"), "0.0001"))
    amount = _sibot._position_size(app, tid, trader, cfg)
    issues = _buy_readiness_issues(native, gas_need, min_trade, amount)
    if not issues:
        return
    usable = max(Decimal(0), native - gas_need)
    _notify_once(app, tid, f"leader-assets:{chain.chain_id}:{leader['wallet']}", "\n".join([
        "🚨 <b>SiBot Leader — Funding Warning</b>", "━━━━━━━━━━━━━━━━━━━━",
        f"🌐 {html.escape(chain.name)}",
        f"🏆 Leader #{int(leader.get('rank') or 0)} <code>{html.escape(_short(leader.get('wallet')))}</code>",
        f"💰 Native balance: <b>{native:f} {html.escape(chain.native_symbol)}</b>",
        f"⛽ Required gas allocation: <b>{gas_need:f} {html.escape(chain.native_symbol)}</b>",
        f"📤 Usable after gas: <b>{usable:f} {html.escape(chain.native_symbol)}</b>",
        f"🧾 Minimum SiBot entry: <b>{min_trade:f} {html.escape(chain.native_symbol)}</b>",
        "❌ This leader can be monitored, but current assets may not fund the next SiBot entry.",
    ]), ttl=6 * 3600)


def refresh_rankings(app, telegram_id, chain):
    before = {str(r.get("wallet") or "").lower() for r in _sibot.leader_rows(app, telegram_id, chain.chain_id)}
    result = _ORIGINAL_REFRESH_RANKINGS(app, telegram_id, chain)
    after = _sibot.leader_rows(app, telegram_id, chain.chain_id)
    for leader in after:
        if str(leader.get("wallet") or "").lower() not in before:
            try:
                _leader_warning(app, str(telegram_id), chain, leader)
            except Exception:
                pass
    return result


def _preflight_buy(app, event: dict, chain):
    for user in _sibot.all_users(app.csv_dir, enabled_only=True):
        tid = str(user.get("telegram_id") or "")
        if not tid:
            continue
        rank = _sibot._user_leader_rank(app, tid, chain.chain_id, event["leader_wallet"])
        if rank is None:
            continue
        cfg = _sibot.user_settings(app, tid, chain.chain_id)
        if not _sibot._bool(cfg.get("enabled"), False):
            continue
        try:
            trader = LiveTrader(app, chain.slug, telegram_id=tid)
            native = trader.native_balance()
            gas_need = _gas_need(trader, cfg, "BUY")
            min_trade = max(Decimal(0), _d(cfg.get("min_trade_native"), "0.0001"))
            amount = _sibot._position_size(app, tid, trader, cfg)
            issues = _buy_readiness_issues(native, gas_need, min_trade, amount)
            if not issues:
                continue
            usable = max(Decimal(0), native - gas_need)
            missing = max(Decimal(0), min_trade - usable)
            _notify_once(app, tid, f"buy:{event.get('tx_hash')}:{chain.chain_id}", "\n".join([
                "🚨 <b>SiBot Trade Cannot Execute — Asset Warning</b>", "━━━━━━━━━━━━━━━━━━━━",
                f"🌐 {html.escape(chain.name)}  •  🏆 Leader #{rank}",
                f"🔎 <code>{html.escape(_short(event.get('leader_wallet')))}</code>",
                f"🟢 Leader action: <b>BUY {html.escape(str(event.get('symbol') or _short(event.get('token'))))}</b>",
                f"💰 Native balance: <b>{native:f} {html.escape(chain.native_symbol)}</b>",
                f"⛽ Gas allocation needed: <b>{gas_need:f} {html.escape(chain.native_symbol)}</b>",
                f"📤 Usable after gas: <b>{usable:f} {html.escape(chain.native_symbol)}</b>",
                f"🧾 Minimum entry: <b>{min_trade:f} {html.escape(chain.native_symbol)}</b>",
                (f"🔴 Missing at least <b>{missing:f} {html.escape(chain.native_symbol)}</b> for a minimum entry after gas." if missing > 0 else "🔴 Gas allocation is below the required safety amount."),
                "⚠️ SiBot did not rely on this leader BUY as a funded LIVE entry.",
            ]), ttl=24 * 3600)
        except Exception as exc:
            _notify_once(app, tid, f"buy-wallet:{event.get('tx_hash')}:{chain.chain_id}", "\n".join([
                "🚨 <b>SiBot Leader BUY — Wallet Not Ready</b>", "━━━━━━━━━━━━━━━━━━━━",
                f"🌐 {html.escape(chain.name)}  •  🏆 Leader #{rank}",
                f"🔎 <code>{html.escape(_short(event.get('leader_wallet')))}</code>",
                f"❌ {html.escape(str(exc)[:220])}",
                "⚠️ Trade was not treated as executable LIVE capital.",
            ]), ttl=24 * 3600)


def _matching_positions(app, tid, chain_id, event):
    with closing(_sibot.connect(app)) as conn:
        rows = conn.execute("""SELECT p.* FROM positions p JOIN position_leaders l ON l.position_id=p.position_id
               WHERE p.telegram_id=? AND p.chain_id=? AND lower(p.token)=? AND p.status='OPEN'
                 AND l.leader_wallet=? AND l.active=1""",
            (str(tid), int(chain_id), str(event.get("token") or "").lower(), str(event.get("leader_wallet") or "").lower())).fetchall()
        return [dict(r) for r in rows]


def _preflight_sell(app, event: dict, chain):
    for user in _sibot.all_users(app.csv_dir, enabled_only=True):
        tid = str(user.get("telegram_id") or "")
        if not tid:
            continue
        rank = _sibot._user_leader_rank(app, tid, chain.chain_id, event["leader_wallet"])
        if rank is None:
            continue
        cfg = _sibot.user_settings(app, tid, chain.chain_id)
        positions = _matching_positions(app, tid, chain.chain_id, event)
        for p in positions:
            if str(p.get("mode") or "").upper() != "LIVE":
                continue
            full = _sibot._float(event.get("sell_pct"), 100.0) >= 99.0
            if not full and not _sibot._bool(cfg.get("mirror_partial_sells"), True):
                continue
            fraction = Decimal(1) if full else max(Decimal("0.0001"), min(Decimal(1), _d(event.get("sell_pct"), 100) / Decimal(100)))
            required_raw = max(1, int(Decimal(_sibot._int(p.get("token_amount_raw"), 0)) * fraction))
            try:
                trader = LiveTrader(app, chain.slug, telegram_id=tid, wallet_id=p.get("wallet_id") or None)
                native = trader.native_balance()
                gas_need = _gas_need(trader, cfg, "SELL")
                _, _, decimals, symbol, token_raw, token_human = trader.token_balance(p["token"])
                issues = _exit_readiness_issues(native, gas_need, token_raw, required_raw)
                if not issues:
                    continue
                missing_raw = max(0, required_raw - int(token_raw))
                missing_human = Decimal(missing_raw) / (Decimal(10) ** int(decimals))
                required_human = Decimal(required_raw) / (Decimal(10) ** int(decimals))
                _notify_once(app, tid, f"sell:{event.get('tx_hash')}:{p.get('position_id')}", "\n".join([
                    "🚨 <b>SiBot EXIT RISK — Assets Missing</b>", "━━━━━━━━━━━━━━━━━━━━",
                    f"🌐 {html.escape(chain.name)}  •  🏆 Leader #{rank} is selling",
                    f"🪙 Position: <b>{html.escape(symbol)}</b>",
                    f"Required token for this exit: <b>{required_human:f} {html.escape(symbol)}</b>",
                    f"Wallet token balance: <b>{token_human:f} {html.escape(symbol)}</b>",
                    f"⛽ Native gas balance: <b>{native:f} {html.escape(chain.native_symbol)}</b>",
                    f"⛽ Gas allocation needed: <b>{gas_need:f} {html.escape(chain.native_symbol)}</b>",
                    (f"🔴 Missing token: <b>{missing_human:f} {html.escape(symbol)}</b>" if missing_raw else "🔴 Native gas allocation is too low for a protected exit."),
                    "⚠️ SiBot may be unable to follow this leader exit until the missing asset is restored.",
                ]), ttl=24 * 3600)
            except Exception as exc:
                _notify_once(app, tid, f"sell-wallet:{event.get('tx_hash')}:{p.get('position_id')}", f"🚨 <b>SiBot EXIT RISK</b>\n{html.escape(chain.name)} • position <code>{html.escape(_short(p.get('token')))}</code>\n❌ Exit-readiness check failed: <code>{html.escape(str(exc)[:220])}</code>", ttl=24 * 3600)


def process_leader_event(app, event: dict):
    chain = _sibot._chain(app, chain_id=event.get("chain_id"))
    if chain:
        try:
            if str(event.get("action") or "").upper() == "BUY":
                _preflight_buy(app, event, chain)
            elif str(event.get("action") or "").upper() == "SELL":
                _preflight_sell(app, event, chain)
        except Exception:
            pass
    return _ORIGINAL_PROCESS_LEADER_EVENT(app, event)


def _hourly_exit_readiness(app, tid) -> str:
    chain_map = {int(c.chain_id): c for c in load_chains(app, enabled_only=True)}
    with closing(_sibot.connect(app)) as conn:
        positions = [dict(r) for r in conn.execute("SELECT * FROM positions WHERE telegram_id=? AND status='OPEN' AND mode='LIVE' ORDER BY chain_id,entry_ts", (str(tid),)).fetchall()]
    if not positions:
        return ""
    issues = []
    ready = 0
    for p in positions:
        chain = chain_map.get(int(p.get("chain_id") or 0))
        if not chain:
            continue
        cfg = _sibot.user_settings(app, tid, chain.chain_id)
        try:
            trader = LiveTrader(app, chain.slug, telegram_id=tid, wallet_id=p.get("wallet_id") or None)
            native = trader.native_balance()
            gas_need = _gas_need(trader, cfg, "SELL")
            _, _, decimals, symbol, token_raw, _ = trader.token_balance(p["token"])
            required_raw = max(0, _sibot._int(p.get("token_amount_raw"), 0))
            miss = _exit_readiness_issues(native, gas_need, token_raw, required_raw)
            if not miss:
                ready += 1
                continue
            missing_raw = max(0, required_raw - int(token_raw))
            missing_human = Decimal(missing_raw) / (Decimal(10) ** int(decimals))
            parts = []
            if native < gas_need:
                parts.append(f"gas short {(gas_need-native):f} {chain.native_symbol}")
            if missing_raw:
                parts.append(f"token short {missing_human:f} {symbol}")
            issues.append(f"▫️ {html.escape(chain.name)} • {html.escape(symbol)}: " + html.escape("; ".join(parts)))
        except Exception as exc:
            issues.append(f"▫️ {html.escape(chain.name)} • <code>{html.escape(_short(p.get('token')))}</code>: readiness check failed ({html.escape(str(exc)[:100])})")
    lines = ["", "<b>🛡 OPEN POSITION EXIT READINESS</b>"]
    if ready:
        lines.append(f"✅ LIVE positions with required token + gas ready: <b>{ready}</b>")
    if issues:
        lines.append("🚨 Positions needing attention:")
        lines.extend(issues[:12])
    return "\n".join(lines)


def build_hourly_capital_alert(app, tid) -> str:
    base = _ORIGINAL_HOURLY(app, tid)
    try:
        return base + _hourly_exit_readiness(app, tid)
    except Exception:
        return base


def install():
    if getattr(_sibot, "_readiness_alert_patch_installed", False):
        return
    _sibot.refresh_rankings = refresh_rankings
    _sibot.process_leader_event = process_leader_event
    _hourly.build_hourly_capital_alert = build_hourly_capital_alert
    _sibot._readiness_alert_patch_installed = True


install()
