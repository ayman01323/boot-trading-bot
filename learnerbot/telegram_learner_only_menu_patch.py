from __future__ import annotations

import html
import os
from decimal import Decimal
from pathlib import Path

from . import solana_sibot as _sol
from . import telegram as _tg
from . import telegram_solana_live_patch as _sol_live_ui
from . import telegram_solana_wallet_patch as _sol_wallet_ui
from . import telegram_ui as _ui
from .solana_wallet_store import SolanaWalletStore


# This presentation layer is intentionally scoped to the isolated Google learner.
# If this branch is ever merged into another deployment, it stays dormant unless
# explicitly enabled with LEARNER_ONLY_TELEGRAM=true.
_TARGET = Path("/home/ayman01323/BOOT/testingbots/learn")
_PREV_HANDLE_UPDATE = _ui.handle_update


def _enabled() -> bool:
    env = str(os.getenv("LEARNER_ONLY_TELEGRAM", "")).strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    try:
        return Path(__file__).resolve().parents[1] == _TARGET
    except Exception:
        return False


def _d(value, default="0") -> Decimal:
    try:
        return Decimal(str(value if value not in (None, "") else default))
    except Exception:
        return Decimal(str(default))


def _short(value: str) -> str:
    value = str(value or "")
    return value if len(value) <= 18 else f"{value[:8]}…{value[-6:]}"


def learner_menu_keyboard(app=None, chat_id=None):
    return {"inline_keyboard": [
        [{"text": "🔐 Add Private Key", "callback_data": "solwallet:import"}],
        [
            {"text": "💰 My Wallet", "callback_data": "learner:wallet"},
            {"text": "📈 My Trading", "callback_data": "learner:trading"},
        ],
        [
            {"text": "🧠 Strategy", "callback_data": "learner:strategy"},
            {"text": "⚙️ My Settings", "callback_data": "learner:settings"},
        ],
        [
            {"text": "📊 My Results", "callback_data": "learner:results"},
            {"text": "🛡 Risk", "callback_data": "learner:risk"},
        ],
        [{"text": "ℹ️ Status", "callback_data": "learner:status"}],
    ]}


def home_text():
    return "\n".join([
        "<b>🧠 LEARNER BOT</b>",
        "━━━━━━━━━━━━",
        "",
        "Solana leader-copy learner using the restored 17-August strategy behaviour.",
        "",
        "Each Telegram user has an independent wallet, settings, positions and results.",
        "Private signing keys are encrypted server-side and are never written to CSV.",
        "",
        "Tap <b>🔐 Add Private Key</b> to import your learner wallet securely in this private chat.",
        "Choose an option below.",
    ])


def _back():
    return {"inline_keyboard": [[{"text": "⬅️ Learner Menu", "callback_data": "menu:home"}]]}


def _strategy_page(app, tid):
    cfg = _sol.settings(app)
    leaders = _sol.leader_rows(app, tid)
    rankings = _sol.ranking_rows(app, tid)
    return "\n".join([
        "<b>🧠 LEARNER STRATEGY</b>",
        "━━━━━━━━━━━━",
        "",
        "Mode: <b>Solana leader-copy learner</b>",
        f"History window: <b>{html.escape(str(cfg.get('lookback_days', '60')))} days</b>",
        f"Leaders per user: <b>{html.escape(str(cfg.get('leaders_per_user', '2')))}</b>",
        f"Minimum closed trades: <b>{html.escape(str(cfg.get('min_closed_trades', '5')))}</b>",
        f"Minimum leader win rate: <b>{html.escape(str(cfg.get('min_win_rate_pct', '50')))}%</b>",
        f"Maximum BUY signal age: <b>{html.escape(str(cfg.get('max_signal_age_seconds', '30')))}s</b>",
        f"Leader polling: <b>{html.escape(str(cfg.get('leader_poll_seconds', '5')))}s</b>",
        f"Mirror partial sells: <b>{'YES' if str(cfg.get('mirror_partial_sells', 'true')).lower() in {'1','true','yes','on'} else 'NO'}</b>",
        "",
        f"Current Top-20 rows: <b>{len(rankings)}</b>",
        f"Current selected leaders: <b>{len(leaders)}</b>",
        "",
        "<i>The strategy is shared; each user's capital and execution remain isolated.</i>",
    ])


def _settings_page(app, tid):
    cfg = _sol.settings(app)
    trade, reserve = _sol_live_ui.live_limits(app, tid, cfg)
    live = _sol_live_ui.live_enabled(app, tid)
    try:
        store = SolanaWalletStore(app.csv_dir, app.data_dir)
        meta = store.get_meta(tid)
        wallet = _short(meta.get("address") or "")
        signing = store.has_private_key(tid, meta.get("wallet_id"))
    except Exception:
        wallet = "not configured"
        signing = False
    return "\n".join([
        "<b>⚙️ MY LEARNER SETTINGS</b>",
        "━━━━━━━━━━━━",
        "",
        f"Active wallet: <code>{html.escape(wallet)}</code>",
        f"Signing: <b>{'🔐 READY' if signing else '👁 NOT READY'}</b>",
        f"LIVE: <b>{'🟢 ARMED' if live else '🔴 OFF'}</b>",
        "",
        f"Trade size: <b>{trade} SOL</b>",
        f"Untouched reserve: <b>{reserve} SOL</b>",
        f"Max LIVE positions: <b>{html.escape(str(cfg.get('live_max_positions', '1')))}</b>",
        f"Position check: <b>{html.escape(str(cfg.get('position_poll_seconds', '15')))}s</b>",
        f"Maximum hold: <b>{html.escape(str(cfg.get('max_hold_hours', '24')))}h</b>",
        "",
        "Use <b>My Wallet</b> to import/select a wallet and <b>My Trading</b> to review LIVE state.",
        "We will expose additional editable tuning controls after the first successful canary round trip.",
    ])


def _results_page(app, tid):
    rows = list(_sol.position_rows(app, tid, open_only=False) or [])
    closed = []
    open_rows = []
    for row in rows:
        status = str(row.get("status") or "").upper()
        if status == "OPEN":
            open_rows.append(row)
        elif status:
            closed.append(row)
    wins = losses = flat = 0
    realised = Decimal("0")
    for row in closed:
        pnl = _d(row.get("realised_net_sol"), "0")
        realised += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        else:
            flat += 1
    decided = wins + losses
    win_rate = (Decimal(wins) * Decimal(100) / Decimal(decided)) if decided else Decimal(0)
    return "\n".join([
        "<b>📊 MY LEARNER RESULTS</b>",
        "━━━━━━━━━━━━",
        "",
        f"Completed positions: <b>{len(closed)}</b>",
        f"Wins: <b>{wins}</b>",
        f"Losses: <b>{losses}</b>",
        f"Break-even/unpriced: <b>{flat}</b>",
        f"Win rate: <b>{win_rate:.1f}%</b>",
        f"Realised net: <b>{realised:+.9f} SOL</b>",
        f"Open positions: <b>{len(open_rows)}</b>",
        "",
        "<i>Results are isolated to this Telegram user.</i>",
    ])


def _risk_page(app, tid):
    cfg = _sol.settings(app)
    return "\n".join([
        "<b>🛡 LEARNER RISK</b>",
        "━━━━━━━━━━━━",
        "",
        f"Stop-loss trigger: <b>-{html.escape(str(cfg.get('stop_loss_pct', '10')))}%</b>",
        f"Take-profit trigger: <b>+{html.escape(str(cfg.get('take_profit_pct', '25')))}%</b>",
        f"Leader-exit loss cap: <b>{html.escape(str(cfg.get('leader_exit_loss_cap_pct', '2.5')))}%</b>",
        f"Break-even activation: <b>+{html.escape(str(cfg.get('break_even_trigger_pct', '5')))}%</b>",
        f"Trailing activation/gap: <b>+{html.escape(str(cfg.get('trailing_trigger_pct', '10')))}% / {html.escape(str(cfg.get('trailing_gap_pct', '5')))}%</b>",
        f"Max immediate round-trip loss: <b>{html.escape(str(cfg.get('max_roundtrip_loss_pct', '3')))}%</b>",
        f"Max entry deterioration: <b>{html.escape(str(cfg.get('max_entry_deterioration_pct', '2')))}%</b>",
        f"Signed simulation required: <b>{'YES' if str(cfg.get('live_require_simulation', 'true')).lower() in {'1','true','yes','on'} else 'NO'}</b>",
        "",
        "🧯 <b>Stuck-liquidity behaviour:</b> the affected mint remains blocked/retried safely, but it does not stop unrelated mints or other users from continuing.",
        "",
        "<i>A stop trigger is not a guaranteed execution price when liquidity disappears.</i>",
    ])


def _status_page(app, tid):
    status = dict(_sol.status(app) or {})
    leaders = list(_sol.leader_rows(app, tid) or [])
    positions = list(_sol.position_rows(app, tid, open_only=True) or [])
    try:
        store = SolanaWalletStore(app.csv_dir, app.data_dir)
        meta = store.get_meta(tid)
        wallet_line = f"<code>{html.escape(_short(meta.get('address') or ''))}</code>"
        signer = store.has_private_key(tid, meta.get("wallet_id"))
    except Exception:
        wallet_line = "<b>not configured</b>"
        signer = False
    return "\n".join([
        "<b>ℹ️ LEARNER STATUS</b>",
        "━━━━━━━━━━━━",
        "",
        f"Wallet: {wallet_line}",
        f"Signing: <b>{'🔐 READY' if signer else '👁 NOT READY'}</b>",
        f"Candidates: <b>{html.escape(str(status.get('candidates', 0)))}</b>",
        f"Selected leaders: <b>{len(leaders)}</b>",
        f"Open positions: <b>{len(positions)}</b>",
        f"LIVE: <b>{'🟢 ARMED' if _sol_live_ui.live_enabled(app, tid) else '🔴 OFF'}</b>",
        "",
        "Google learner path:",
        "<code>/home/ayman01323/BOOT/testingbots/learn</code>",
    ])


def _answer(app, qid, text=""):
    if not qid:
        return
    try:
        _tg.answer_callback_query(app.telegram_bot_token, qid, text)
    except Exception:
        pass


def _send(app, tid, text, keyboard=None):
    _ui._send(app, tid, text, keyboard or _back())


def _handle_learner_callback(app, update) -> bool:
    q = update.get("callback_query") or {}
    data = str(q.get("data") or "")
    if not data.startswith("learner:"):
        return False
    tid = ((q.get("message") or {}).get("chat") or {}).get("id")
    qid = q.get("id")
    if tid is None:
        return True
    if not _ui._auth(app, tid):
        _answer(app, qid, "Not authorised")
        return True
    _answer(app, qid)
    try:
        if data == "learner:wallet":
            _send(app, tid, _sol_wallet_ui.solwallet_page(app, tid), _sol_wallet_ui.solwallet_keyboard(app, tid))
        elif data == "learner:trading":
            _send(app, tid, _sol_live_ui.solana_page(app, tid), _sol_live_ui.solana_keyboard(app, tid))
        elif data == "learner:strategy":
            _send(app, tid, _strategy_page(app, tid))
        elif data == "learner:settings":
            _send(app, tid, _settings_page(app, tid))
        elif data == "learner:results":
            _send(app, tid, _results_page(app, tid))
        elif data == "learner:risk":
            _send(app, tid, _risk_page(app, tid))
        elif data == "learner:status":
            _send(app, tid, _status_page(app, tid))
        else:
            _send(app, tid, home_text(), learner_menu_keyboard(app, tid))
    except Exception as exc:
        _send(app, tid, f"❌ <b>Learner menu error</b>\n<code>{html.escape(str(exc)[:500])}</code>")
    return True


def handle_update(app, update):
    if _handle_learner_callback(app, update):
        return True
    m = update.get("message") or {}
    tid = (m.get("chat") or {}).get("id")
    text = str(m.get("text") or "").strip()
    cmd = text.split(maxsplit=1)[0].split("@", 1)[0].lower() if text.startswith("/") else ""
    if tid is not None and cmd in {"/learner", "/start"}:
        if _ui._auth(app, tid):
            _send(app, tid, home_text(), learner_menu_keyboard(app, tid))
            return True
    return _PREV_HANDLE_UPDATE(app, update)


def learner_set_commands(token: str):
    commands = [
        {"command": "menu", "description": "Open Learner Bot menu"},
        {"command": "join", "description": "Register this Telegram account"},
        {"command": "activate", "description": "Activate account with a code"},
        {"command": "solwallet", "description": "My Solana wallets"},
        {"command": "solwalletimport", "description": "Import encrypted Solana signing key"},
        {"command": "learner", "description": "Open Learner Bot dashboard"},
    ]
    _tg._json("setMyCommands", token, payload={"commands": commands}, timeout=15)


def install():
    if not _enabled():
        return
    if getattr(_ui, "_learner_only_menu_installed", False):
        return
    _ui.menu_keyboard = learner_menu_keyboard
    _ui.home_text = home_text
    _ui.handle_update = handle_update
    _ui.set_commands = learner_set_commands
    _ui._learner_only_menu_installed = True


install()
