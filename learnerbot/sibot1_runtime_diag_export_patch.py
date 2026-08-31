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
_ACTIVE_OUT = OUT


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

    keep_top = ('state', 'controller_state', 'mode', 'live_enabled', 'signer_attached', 'broadcast_enabled', 'wallet_private_key_access', 'updated_epoch')
    out['sibot1_status'] = {k: raw.get(k) for k in keep_top if k in raw}

    workers_raw = raw.get('workers') or {}
    workers = []
    if isinstance(workers_raw, dict):
        worker_items = workers_raw.items()
    elif isinstance(workers_raw, list):
        worker_items = ((str((row or {}).get('engine_id') or ''), row) for row in workers_raw if isinstance(row, dict))
    else:
        worker_items = ()
    for engine_id, row in worker_items:
        if not isinstance(row, dict):
            continue
        item = {'engine_id': str(engine_id or row.get('engine_id') or '')}
        for key in ('state', 'alive', 'pid', 'version', 'events', 'signals', 'spread_signals', 'cycle_signals', 'updated_epoch', 'error'):
            if key in row:
                item[key] = _redact_text(row.get(key)) if key == 'error' else row.get(key)
        workers.append(item)
    out['workers'] = sorted(workers, key=lambda row: row.get('engine_id', ''))

    out['scoreboard'] = [
        {k: row.get(k) for k in (
            'engine_id', 'chain', 'signals', 'poolcheck_shadow', 'poolcheck_blocks', 'paper_entries', 'paper_exits',
            'paper_wins', 'paper_losses', 'realised_pnl_quote', 'errors', 'last_event_epoch'
        ) if k in row}
        for row in (raw.get('scoreboard') or []) if isinstance(row, dict)
    ]
    return out


def _audit(app) -> dict:
    path = Path(app.data_dir) / 'sibot1' / 'audit.ndjson'
    if not path.exists():
        return {'audit_event_counts_tail': {}, 'poolcheck_reason_counts_tail': {}, 'last_engine_audit': []}
    try:
        lines = path.read_text(encoding='utf-8', errors='replace').splitlines()[-1000:]
    except Exception as exc:
        return {'audit_error': f'{type(exc).__name__}: {_redact_text(exc)}'}
    rows = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    counts = Counter(str(row.get('event_type') or '') for row in rows)
    reason_counts = Counter()
    for row in rows:
        if str(row.get('event_type') or '').upper() != 'POOLCHECK':
            continue
        for reason in row.get('reasons') or []:
            reason_counts[_redact_text(reason)] += 1
    safe = []
    for row in rows[-60:]:
        et = str(row.get('event_type') or '').upper()
        if et not in {'SIGNAL', 'POOLCHECK', 'ERROR', 'PAPER_ENTRY', 'PAPER_EXIT'}:
            continue
        item = {k: row.get(k) for k in ('epoch_ms', 'event_type', 'engine_id', 'chain', 'verdict', 'intent_id', 'lot_id') if k in row}
        if 'reasons' in row:
            item['reasons'] = [_redact_text(x) for x in (row.get('reasons') or [])][:8]
        if 'detail' in row:
            item['detail'] = _redact_text(row.get('detail'))
        safe.append(item)
    return {
        'audit_event_counts_tail': dict(counts),
        'poolcheck_reason_counts_tail': dict(reason_counts.most_common(30)),
        'last_engine_audit': safe[-30:],
    }


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
        'schema_version': 2,
        'redacted': True,
    }
    out.update(_status(app))
    out.update(_audit(app))
    out.update(_candidates(app))
    out.update(_attempts(app))
    out.update(_controls(app))
    return out


def _fallback_out(app) -> Path:
    return Path(app.data_dir) / 'sibot1' / 'runtime_diag.json'


def _atomic_write(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    try:
        tmp.write_text(data + '\n', encoding='utf-8')
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        os.chmod(path, 0o644)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


def _write(app) -> Path:
    global _ACTIVE_OUT
    data = json.dumps(snapshot(app), indent=2, sort_keys=True)
    target = _ACTIVE_OUT
    try:
        _atomic_write(target, data)
        return target
    except PermissionError as exc:
        fallback = _fallback_out(app)
        if target == fallback:
            raise
        print(
            '[sibot1-runtime-diag] primary-output-unwritable',
            type(exc).__name__,
            _redact_text(exc)[:180],
            f'fallback={fallback}',
        )
        _atomic_write(fallback, data)
        _ACTIVE_OUT = fallback
        return fallback


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
        active_out = _ACTIVE_OUT
        try:
            active_out = _write(app)
        except Exception as exc:
            # Runtime diagnostics are observability only. A diagnostic export
            # failure must never terminate learnerbot/Claude or create a
            # systemd restart loop. The background worker will keep retrying.
            print('[sibot1-runtime-diag] startup-export-degraded', type(exc).__name__, _redact_text(exc)[:240])
        threading.Thread(target=_worker, args=(app,), daemon=True, name='sibot1-redacted-runtime-diag').start()
        _STARTED = True
        print(f'[sibot1-runtime-diag] redacted-export={active_out} interval=10s startup-nonfatal=true')


def _app_with_diag():
    app = _PREV_APP()
    _start(app)
    return app


def install() -> None:
    _cli._app = _app_with_diag


install()
