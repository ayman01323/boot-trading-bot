from __future__ import annotations

import os
from pathlib import Path

from . import cli as _cli
from .config import load_chains, load_kv_scoped
from .multi_wallet_store import MultiWalletStore
from .solana_wallet_store import SolanaWalletStore
from .user_registry import all_users, user_bool, user_setting
from . import solana_sibot as _sol

_PREV_APP = _cli._app
_OUT = Path('/tmp/learnerbot_live_status_probe.txt')


def _b(v, default=False):
    if v is None:
        return default
    return str(v).strip().lower() in {'1','true','yes','on','y'}


def _write(app):
    users = [u for u in all_users(app.csv_dir, enabled_only=True) if str(u.get('status') or '').upper() == 'ACTIVE']
    masters = [u for u in users if str(u.get('role') or '').upper() == 'MASTER']
    targets = masters or users[:1]
    lines = []
    auto_platform = load_kv_scoped(Path(app.csv_dir) / 'auto_trading_settings.csv', 0)
    lines.append(f"platform_auto_trading_enabled={str(_b(auto_platform.get('auto_trading_enabled'), False)).lower()}")
    lines.append(f"active_users={len(users)} master_users={len(masters)}")
    evm_store = MultiWalletStore(app.data_dir, app.csv_dir)
    sol_store = SolanaWalletStore(app.csv_dir, app.data_dir)
    for u in targets:
        tid = str(u.get('telegram_id') or '')
        lines.append(f"user={tid} role={str(u.get('role') or '')} can_auto_trade={str(_b(u.get('can_auto_trade'), True)).lower()}")
        try:
            meta = evm_store.get_meta(tid)
            wid = str(meta.get('wallet_id') or '')
            addr = str(meta.get('address') or '')
            signing = bool(evm_store.private_key_hex(tid, wid))
            lines.append(f"evm_wallet_active=true evm_signing_ready={str(signing).lower()} evm_address={addr}")
        except Exception as exc:
            lines.append(f"evm_wallet_active=false evm_signing_ready=false evm_wallet_error={type(exc).__name__}")
        for c in load_chains(app, enabled_only=True):
            live_cfg = load_kv_scoped(Path(app.csv_dir) / 'live_trading_settings.csv', c.chain_id)
            p_live = _b(live_cfg.get('trading_enabled'), False)
            u_live = user_bool(app.csv_dir, tid, c.chain_id, 'live_trading_enabled', False)
            u_auto = user_bool(app.csv_dir, tid, c.chain_id, 'auto_trading_enabled', False)
            mode = str(user_setting(app.csv_dir, tid, c.chain_id, 'recommendation_mode', 'SHADOW')).upper()
            effective = p_live and _b(auto_platform.get('auto_trading_enabled'), False) and u_live and u_auto and mode == 'ARMED' and _b(u.get('can_auto_trade'), True)
            lines.append(f"chain={c.slug} chain_id={c.chain_id} enabled=true platform_live={str(p_live).lower()} user_live={str(u_live).lower()} user_auto={str(u_auto).lower()} mode={mode} effective_auto_live={str(effective).lower()}")
        try:
            sm = sol_store.get_meta(tid)
            swid = str(sm.get('wallet_id') or '')
            saddr = str(sm.get('address') or '')
            ssign = sol_store.has_private_key(tid, swid)
            slive = user_bool(app.csv_dir, tid, _sol.SOLANA_CHAIN_ID, 'solana_live_enabled', False)
            scfg = _sol.settings(app)
            senabled = _b(scfg.get('enabled'), True)
            seffective = senabled and slive and ssign and _b(u.get('can_auto_trade'), True)
            lines.append(f"chain=solana chain_id={_sol.SOLANA_CHAIN_ID} enabled={str(senabled).lower()} user_live={str(slive).lower()} signing_ready={str(ssign).lower()} effective_auto_live={str(seffective).lower()} sol_address={saddr}")
        except Exception as exc:
            lines.append(f"chain=solana enabled=false user_live=false signing_ready=false effective_auto_live=false error={type(exc).__name__}")
    _OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    os.chmod(_OUT, 0o644)


def _app_with_probe():
    app = _PREV_APP()
    try:
        _write(app)
    except Exception as exc:
        try:
            _OUT.write_text(f"probe_error={type(exc).__name__}:{str(exc)[:300]}\n", encoding='utf-8')
            os.chmod(_OUT, 0o644)
        except Exception:
            pass
    return app

_cli._app = _app_with_probe
