from __future__ import annotations

import html
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from decimal import Decimal
from pathlib import Path

from . import sibot as _sibot
from . import telegram_sibot_patch as _tg
from .config import load_chains


_original_ensure_settings = _sibot.ensure_settings


def _is_top20_candidate(a: dict) -> bool:
    """Top-20 means profit-first: measured gains exceed losses and net is positive."""
    return a.get("net", Decimal(0)) > 0 and a.get("profit", Decimal(0)) > a.get("loss", Decimal(0))


def _is_leader_candidate(a: dict, min_closed: int, min_win_rate: float) -> bool:
    """Leader-copy safety is separate from being visible in Top-20."""
    if not _is_top20_candidate(a):
        return False
    return int(a.get("closed", 0)) >= int(min_closed) and float(a.get("win_rate", 0.0)) >= float(min_win_rate)


def _migrate_reasonable_defaults(app, path: Path) -> None:
    """Relax only the original over-strict defaults; preserve intentional user changes."""
    rows = _sibot._rows(path)
    replacements = {
        "history_candidate_wallets": ("40", "100"),
        "require_complete_history": ("true", "false"),
        "min_closed_trades": ("50", "5"),
        "min_win_rate_pct": ("55", "50"),
    }
    changed = False
    for row in rows:
        key = str(row.get("setting") or "").strip()
        current = str(row.get("value") or "").strip().lower()
        if key in replacements:
            old, new = replacements[key]
            if current == old.lower():
                row["value"] = new
                changed = True
    if changed:
        _sibot._atomic_csv(path, rows, ["chain_id", "setting", "value", "description"])


def ensure_settings(app) -> Path:
    path = _original_ensure_settings(app)
    _migrate_reasonable_defaults(app, path)
    return path


def _new_bucket():
    return {"profit": Decimal(0), "loss": Decimal(0), "net": Decimal(0), "wins": 0, "losses": 0, "closed": 0}


def _add_result(a: dict, net) -> None:
    n = _sibot._dec(net)
    a["net"] += n
    a["closed"] += 1
    if n > 0:
        a["profit"] += n
        a["wins"] += 1
    elif n < 0:
        a["loss"] += -n
        a["losses"] += 1


def _core_proven_profit(app, chain, cutoff: int) -> dict[str, dict]:
    """Use the learning bot's existing timestamped proven P&L evidence for broad coverage."""
    path = Path(app.data_dir) / f"{chain.slug}.sqlite3"
    if not path.exists():
        return {}
    out = defaultdict(_new_bucket)
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT lower(wallet) wallet, profit_base
               FROM trade_behaviour_evidence
               WHERE block_timestamp>=?
                 AND profit_base IS NOT NULL
                 AND proof_quality='PROVEN_WRAPPED_BASE'""",
            (int(cutoff),),
        ).fetchall()
        for r in rows:
            wallet = str(r["wallet"] or "").lower()
            if wallet:
                _add_result(out[wallet], r["profit_base"])
    except sqlite3.Error:
        return {}
    finally:
        conn.close()
    return dict(out)


def _spot_profit(app, chain, cutoff: int) -> tuple[dict[str, dict], dict[str, bool]]:
    out = defaultdict(_new_bucket)
    with closing(_sibot.connect(app)) as conn:
        rows = conn.execute(
            "SELECT wallet,net_native FROM wallet_trades WHERE chain_id=? AND sell_ts>=?",
            (chain.chain_id, int(cutoff)),
        ).fetchall()
        complete = {
            str(r["wallet"]).lower(): bool(r["history_complete"])
            for r in conn.execute(
                "SELECT wallet,history_complete FROM wallet_history_status WHERE chain_id=?",
                (chain.chain_id,),
            ).fetchall()
        }
    for r in rows:
        wallet = str(r["wallet"] or "").lower()
        if wallet:
            _add_result(out[wallet], r["net_native"])
    return dict(out), complete


def refresh_rankings(app, telegram_id, chain) -> list[dict]:
    """Rank Top-20 by profit only; apply trade-count/win-rate only to SiBot leaders."""
    cfg = _sibot.user_settings(app, telegram_id, chain.chain_id)
    lookback = max(1, min(365, _sibot._int(cfg.get("lookback_days"), 60)))
    topn = max(1, min(100, _sibot._int(cfg.get("top_wallets"), 20)))
    min_closed = max(1, _sibot._int(cfg.get("min_closed_trades"), 5))
    min_win_rate = max(0.0, min(100.0, _sibot._float(cfg.get("min_win_rate_pct"), 50)))
    cutoff = int(time.time()) - lookback * 86400
    now = int(time.time())

    # Prefer the bot's broad, already-proven timestamped P&L evidence. The direct
    # spot-history reconstruction is a fallback for wallets not represented there.
    agg = _core_proven_profit(app, chain, cutoff)
    spot, complete_map = _spot_profit(app, chain, cutoff)
    for wallet, a in spot.items():
        if wallet not in agg:
            agg[wallet] = a

    ranked = []
    for wallet, a in agg.items():
        a = dict(a)
        a["wallet"] = wallet
        a["win_rate"] = (a["wins"] / a["closed"] * 100.0) if a["closed"] else 0.0
        a["history_complete"] = bool(complete_map.get(wallet, False))
        if _is_top20_candidate(a):
            ranked.append(a)

    ranked.sort(key=lambda x: (x["net"], x["profit"], x["closed"], x["win_rate"]), reverse=True)
    top = ranked[:topn]

    with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
        conn.execute("DELETE FROM rankings WHERE telegram_id=? AND chain_id=?", (str(telegram_id), chain.chain_id))
        for i, a in enumerate(top, 1):
            conn.execute(
                """INSERT INTO rankings(telegram_id,chain_id,chain_slug,lookback_days,rank,wallet,
                                          gross_profit_native,gross_loss_native,net_profit_native,wins,losses,
                                          closed_trades,win_rate,history_complete,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(telegram_id), chain.chain_id, chain.slug, lookback, i, a["wallet"],
                    str(a["profit"]), str(a["loss"]), str(a["net"]), a["wins"], a["losses"],
                    a["closed"], a["win_rate"], 1 if a["history_complete"] else 0, now,
                ),
            )

        old = {
            str(r["wallet"]).lower(): int(r["selected_at"] or now)
            for r in conn.execute(
                "SELECT wallet,selected_at FROM leaders WHERE telegram_id=? AND chain_id=?",
                (str(telegram_id), chain.chain_id),
            ).fetchall()
        }
        conn.execute("DELETE FROM leaders WHERE telegram_id=? AND chain_id=?", (str(telegram_id), chain.chain_id))
        nleaders = max(1, min(10, _sibot._int(cfg.get("leaders_per_chain"), 2)))
        safe = [a for a in top if _is_leader_candidate(a, min_closed, min_win_rate)]
        for i, a in enumerate(safe[:nleaders], 1):
            conn.execute(
                """INSERT INTO leaders(telegram_id,chain_id,chain_slug,rank,wallet,net_profit_native,
                                         win_rate,closed_trades,selected_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(telegram_id), chain.chain_id, chain.slug, i, a["wallet"], str(a["net"]),
                    a["win_rate"], a["closed"], old.get(a["wallet"], now), now,
                ),
            )
        conn.commit()

    _sibot.export_rankings(app)
    return _sibot.ranking_rows(app, telegram_id, chain.chain_id)


def top20_summary_page(app, tid):
    rows = _sibot.ranking_rows(app, tid)
    counts = {}
    for r in rows:
        counts[int(r["chain_id"])] = counts.get(int(r["chain_id"]), 0) + 1
    lines = ["<b>📈 SiBot Top 20</b>", _tg.DIV, "<b>60-day rule:</b> net profit &gt; 0 and gains &gt; losses.", ""]
    for c in load_chains(app, enabled_only=True):
        n = counts.get(c.chain_id, 0)
        lines.append(f"🌐 <b>{html.escape(c.name)}</b>  •  <b>{n}</b> profitable wallet{'s' if n != 1 else ''}")
    lines += ["", "<i>No 50-trade or 55% win-rate gate is used to hide wallets from Top 20. Those checks are only for automatic leader selection.</i>"]
    return "\n".join(lines)


def top20_page(app, tid, chain):
    target = _tg._chain(app, chain)
    if not target:
        return "<b>📈 SiBot Top 20</b>\nUnknown chain."
    rows = _sibot.ranking_rows(app, tid, target.chain_id)
    lines = [f"<b>📈 SiBot Top 20 — {html.escape(target.name)}</b>", _tg.DIV]
    if not rows:
        return "\n".join(lines + ["", "⏳ No positive-net wallet is in the stored 60-day evidence yet.", "This now means a data/coverage issue — not failure of strict quality filters."])
    for r in rows[:20]:
        net = Decimal(str(r.get("net_profit_native") or 0))
        gain = Decimal(str(r.get("gross_profit_native") or 0))
        loss = Decimal(str(r.get("gross_loss_native") or 0))
        icon = "🥇" if int(r["rank"]) == 1 else "🥈" if int(r["rank"]) == 2 else "🥉" if int(r["rank"]) == 3 else "▫️"
        lines.append(f"{icon} <b>#{r['rank']}</b> <code>{html.escape(_tg._short(r['wallet']))}</code>")
        lines.append(f"   💰 Net <b>{net:+.6f} {html.escape(target.native_symbol)}</b>  •  ✅ {gain:.6f}  •  🔻 {loss:.6f}")
        lines.append(f"   🔁 {int(r.get('closed_trades') or 0)} proven results  •  🎯 {float(r.get('win_rate') or 0):.1f}% positive")
    return "\n".join(lines)


def leaders_page(app, tid, chain=None):
    target = _tg._chain(app, chain) if chain else None
    rows = _sibot.leader_rows(app, tid, target.chain_id if target else None)
    title = f"🏆 SiBot Leaders — {html.escape(target.name)}" if target else "🏆 SiBot Leaders"
    lines = [f"<b>{title}</b>", _tg.DIV]
    if not rows:
        return "\n".join(lines + ["", "⏳ Top 20 may contain profitable wallets, but none yet passes the current leader-copy safety settings.", "Default leader filter is now only <b>5 proven results + 50% positive</b>."])
    last_chain = None
    for r in rows:
        c = _tg._chain(app, r["chain_id"])
        if r["chain_id"] != last_chain:
            lines += ["", f"<b>🌐 {html.escape(c.name if c else str(r['chain_id']))}</b>"]
            last_chain = r["chain_id"]
        medal = "🥇" if int(r["rank"]) == 1 else "🥈" if int(r["rank"]) == 2 else "🏅"
        sym = c.native_symbol if c else "native"
        lines.append(f"{medal} <code>{html.escape(_tg._short(r['wallet']))}</code>")
        lines.append(f"   💰 {Decimal(str(r['net_profit_native'])):+.6f} {html.escape(sym)}  •  🎯 {float(r['win_rate']):.1f}%  •  🔁 {r['closed_trades']}")
    return "\n".join(lines)


def install():
    if getattr(_sibot, "_reasonable_top20_patch_installed", False):
        return
    _sibot.ensure_settings = ensure_settings
    _sibot.refresh_rankings = refresh_rankings
    _tg.top20_summary_page = top20_summary_page
    _tg.top20_page = top20_page
    _tg.leaders_page = leaders_page
    _sibot._reasonable_top20_patch_installed = True


install()
