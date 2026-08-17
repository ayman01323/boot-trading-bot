from __future__ import annotations

from pathlib import Path

from . import cli as _cli
from .config import load_chains, load_kv_scoped
from .multi_wallet_store import MultiWalletStore
from .solana_wallet_store import SolanaWalletStore
from .user_registry import all_users, user_bool, user_setting
from . import solana_sibot as _sol

_PREV_APP = _cli._app


def _b(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {'1', 'true', 'yes', 'on', 'y'}


def _report(app):
    users = [u for u in all_users(app.csv_dir, enabled_only=True) if str(u.get('status') or '').upper() == 'ACTIVE']
    masters = [u for u in users if str(u.get('role') or '').upper() == 'MASTER']
    targets = masters or users[:1]
    auto_platform = load_kv_scoped(Path(app.csv_dir) / 'auto_trading_settings.csv', 0)
    print(f"[live-status] platform_auto={str(_b(auto_platform.get('auto_trading_enabled'), False)).lower()}")
    evm_store = MultiWalletStore(app.data_dir, app.csv_dir)
    sol_store = SolanaWalletStore(app.csv_dir, app.data_dir)
    for u in targets:
        tid = str(u.get('telegram_id') or '')
        can_auto = _b(u.get('can_auto_trade'), True)
        try:
            meta = evm_store.get_meta(tid)
            evm_signing = bool(evm_store.private_key_hex(tid, str(meta.get('wallet_id') or '')))
        except Exception:
            evm_signing = False
        print(f"[live-status] evm_signing_ready={str(evm_signing).lower()} can_auto_trade={str(can_auto).lower()}")
        for c in load_chains(app, enabled_only=True):
            live_cfg = load_kv_scoped(Path(app.csv_dir) / 'live_trading_settings.csv', c.chain_id)
            p_live = _b(live_cfg.get('trading_enabled'), False)
            u_live = user_bool(app.csv_dir, tid, c.chain_id, 'live_trading_enabled', False)
            u_auto = user_bool(app.csv_dir, tid, c.chain_id, 'auto_trading_enabled', False)
            mode = str(user_setting(app.csv_dir, tid, c.chain_id, 'recommendation_mode', 'SHADOW')).upper()
            effective = p_live and _b(auto_platform.get('auto_trading_enabled'), False) and u_live and u_auto and mode == 'ARMED' and can_auto and evm_signing
            print(
                f"[live-status] chain={c.slug} platform_live={str(p_live).lower()} "
                f"user_live={str(u_live).lower()} user_auto={str(u_auto).lower()} mode={mode} "
                f"effective_auto_live={str(effective).lower()}"
            )
        try:
            sm = sol_store.get_meta(tid)
            ssign = sol_store.has_private_key(tid, str(sm.get('wallet_id') or ''))
            slive = user_bool(app.csv_dir, tid, _sol.SOLANA_CHAIN_ID, 'solana_live_enabled', False)
            senabled = _b(_sol.settings(app).get('enabled'), True)
            seffective = senabled and slive and ssign and can_auto
            print(
                f"[live-status] chain=solana enabled={str(senabled).lower()} user_live={str(slive).lower()} "
                f"signing_ready={str(ssign).lower()} effective_auto_live={str(seffective).lower()}"
            )
        except Exception as exc:
            print(f"[live-status] chain=solana effective_auto_live=false error={type(exc).__name__}")


def _app_with_probe():
    app = _PREV_APP()
    try:
        _report(app)
    except Exception as exc:
        print(f"[live-status] probe_error={type(exc).__name__}")
    return app


_cli._app = _app_with_probe
