from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import threading
import time
from collections import Counter
from pathlib import Path

from . import cli as _cli

_PREV_APP = _cli._app
_STARTED = False
_LOCK = threading.Lock()
OUT = Path('/var/tmp/sibot1-runtime-diag.json')


def _bool(value) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on', 'y'}


def _redact_text(value) -> str:
    text = str(value or '')[:1400]
    text = re.sub(r'0x[0-9a-fA-F]{64}', '0x[REDACTED_SECRET]', text)
    text = re.sub(r'(?i)(private[_ -]?key|seed phrase|mnemonic|authorization|bearer|password|secret)\s*[:=]\s*\S+', r'\1=[REDACTED]', text)
    return text


def _status(app) -> dict:
    out = {}
    path = Path(app.data_dir) / 'sibot1' / 'status.json'
    if not path.exists():
        return out
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        return {'status_error': f'{type(exc).__name__}: {_redact_text(exc)}'}
    if not isinstance(raw, dict):
        return {'status_error': 'status.json is not an object'}
    keep_top = ('controller_state', 'mode', 'live_enabled', 'signer_attached', 'broadcast_enabled', 'wallet_private_key_access')
    out['sibot1_status'] = {k: raw.get(k) for k in keep_top if k in raw}
    out['workers'] = [
        {k: row.get(k) for k in ('engine_id', 'chain', 'state', 'health', 'alive', 'pid', 'last_heartbeat_epoch') if k in row}
        for row in (raw.get('workers') or []) if isinstance(row, dict)
    ]
    out['scoreboard'] = [
        {k: row.get(k) for k in (
            'engine_id', 'chain', 'signals', 'poolcheck_shadow', 'poolcheck_blocks', 'paper_entries', 'paper_exits',
            'paper_wins', 'paper_losses', 'realised_pnl_quote', 'errors', 'last_event_epoch'
        ) if k in row}
        for row in (raw.get('scoreboard') or []) if isinstance(row, dict)
    ]
    return out


def _candidates(app) -> dict:
    path = Path(app.data_dir) / 'sibot1' / 'live_candidates.jsonl'
    rows = []
    if path.exists():
        try:
            lines = path.read_text(encoding='utf-8', errors='replace').splitlines()[-500:]
        except Exception:
            lines = []
        for line in lines:
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return {
        'live_candidates_total_tail': len(rows),
        'live_candidate_kinds': dict(Counter(str(r.get('kind') or '') for r in rows)),
        'live_candidate_engines': dict(Counter(str(r.get('engine_id') or '') for r in rows)),
        'last_live_candidates': [
            {k: r.get(k) for k in (
                'candidate_id', 'kind', 'engine_id', 'engine_version', 'strategy_id', 'chain',
                'poolcheck_verdict', 'shadow_poolcheck_verdict', 'live_revalidation_required',
                'route_id', 'route_kind', 'execution_mode', 'intent_created_at_ms', 'net_edge_bps'
            ) if k in r}
            for r in rows[-10:]
        ],
    }


def _attempts(app) -> dict:
    path = Path(app.data_dir) / 'sibot1_live_bridge.sqlite3'
    if not path.exists():
        return {'attempt_status_counts_tail': {}, 'last_attempts': [], 'live_position_counts': []}
    try:
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT candidate_id,kind,chain,status,tx_hash,error,created_at,updated_at '
            'FROM attempts ORDER BY updated_at DESC LIMIT 50'
        ).fetchall()
        positions = conn.execute("SELECT chain,status,COUNT(*) n FROM positions GROUP BY chain,status").fetchall()
        conn.close()
    except Exception as exc:
        return {'attempts_error': f'{type(exc).__name__}: {_redact_text(exc)}'}
    safe = []
    for row in rows[:15]:
        item = dict(row)
        item['error'] = _redact_text(item.get('error'))
        item['tx_hash'] = str(item.get('tx_hash') or '')[:80]
        safe.append(item)
    return {
        'attempt_status_counts_tail': dict(Counter(str(r['status']) for r in rows)),
        'last_attempts': safe,
        'live_position_counts': [dict(r) for r in positions],
    }


def _controls(app) -> dict:
    path = Path(app.csv_dir) / 'sibot1' / 'live_control.csv'
    if not path.exists():
        return {'live_control': {'configured_accounts': 0, 'any_armed': False, 'any_live': False, 'any_auto': False}}
    try:
        with path.open('r', encoding='utf-8-sig', newline='') as handle:
            rows = list(csv.DictReader(handle))
    except Exception as exc:
        return {'live_control_error': f'{type(exc).__name__}: {_redact_text(exc)}'}
    return {'live_control': {
        'configured_accounts': len(rows),
        'any_armed': any(_bool(r.get('armed')) for r in rows),
        'any_live': any(_bool(r.get('live_enabled')) for r in rows),
        'any_auto': any(_bool(r.get('auto_enabled')) for r in rows),
    }}


def _server_sha(app) -> str:
    try:
        head = Path(app.root) / '.git' / 'HEAD'
        raw = head.read_text(encoding='utf-8').strip()
        if raw.startswith('ref: '):
            ref = Path(app.root) / '.git' / raw[5:]
            return ref.read_text(encoding='utf-8').strip()[:40]
        return raw[:40]
    except Exception:
        return ''


def snapshot(app) -> dict:
    out = {
        'utc_epoch': int(time.time()),
        'server_sha': _server_sha(app),
        'service_process_alive': True,
        'schema_version': 1,
        'redacted': True,
    }
    out.update(_status(app))
    out.update(_candidates(app))
    out.update(_attempts(app))
    out.update(_controls(app))
    return out


def _write(app) -> None:
    data = json.dumps(snapshot(app), indent=2, sort_keys=True)
    tmp = OUT.with_suffix('.json.tmp')
    tmp.write_text(data + '\n', encoding='utf-8')
    os.chmod(tmp, 0o644)
    os.replace(tmp, OUT)
    os.chmod(OUT, 0o644)


def _worker(app) -> None:
    while True:
        try:
            _write(app)
        except Exception as exc:
            print('[sibot1-runtime-diag]', type(exc).__name__, _redact_text(exc)[:240])
        time.sleep(10)


def _start(app) -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _write(app)
        threading.Thread(target=_worker, args=(app,), daemon=True, name='sibot1-redacted-runtime-diag').start()
        _STARTED = True
        print(f'[sibot1-runtime-diag] redacted-export={OUT} interval=10s')


def _app_with_diag():
    app = _PREV_APP()
    _start(app)
    return app


def install() -> None:
    _cli._app = _app_with_diag


install()
