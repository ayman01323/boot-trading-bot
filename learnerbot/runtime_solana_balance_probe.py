from __future__ import annotations

import csv
import os
from decimal import Decimal
from pathlib import Path

import requests

from . import cli as _cli
from . import solana_sibot as _sol

_PREV_APP = _cli._app
_OUT = Path('/tmp/learnerbot_solana_balance_probe.txt')


def _enabled(value, default=True):
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on', 'y'}


def _write_probe(app):
    wallet_path = Path(app.csv_dir) / 'auto' / 'solana_user_wallets.csv'
    rows = []
    if wallet_path.exists():
        with wallet_path.open('r', encoding='utf-8-sig', newline='') as f:
            rows = list(csv.DictReader(f))
    active = [
        r for r in rows
        if _enabled(r.get('enabled'), True)
        and str(r.get('active') or '').strip().lower() == 'true'
        and str(r.get('address') or '').strip()
    ]
    cfg = _sol.settings(app)
    rpc = str(cfg.get('rpc_url') or _sol.DEFAULT_RPC).strip()
    lines = [f'active_wallet_count={len(active)}']
    for row in active:
        address = str(row.get('address') or '').strip()
        wallet_id = str(row.get('wallet_id') or '').strip()
        label = str(row.get('label') or '').strip().replace('\n', ' ')[:80]
        payload = {
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'getBalance',
            'params': [address, {'commitment': 'confirmed'}],
        }
        try:
            resp = requests.post(rpc, json=payload, timeout=12)
            resp.raise_for_status()
            data = resp.json()
            if data.get('error'):
                raise RuntimeError(str(data['error']))
            lamports = int(((data.get('result') or {}).get('value')) or 0)
            sol = Decimal(lamports) / Decimal(1_000_000_000)
            lines.append(f'wallet_id={wallet_id} label={label} address={address} lamports={lamports} sol={sol:.9f}')
        except Exception as exc:
            lines.append(f'wallet_id={wallet_id} label={label} address={address} error={type(exc).__name__}:{str(exc)[:160]}')
    _OUT.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    os.chmod(_OUT, 0o644)


def _app_with_probe():
    app = _PREV_APP()
    try:
        _write_probe(app)
    except Exception as exc:
        try:
            _OUT.write_text(f'probe_error={type(exc).__name__}:{str(exc)[:200]}\n', encoding='utf-8')
            os.chmod(_OUT, 0o644)
        except Exception:
            pass
    return app


_cli._app = _app_with_probe
