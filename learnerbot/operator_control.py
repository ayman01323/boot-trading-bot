from __future__ import annotations

import csv
import os
import threading
import time
from pathlib import Path

_LOCK = threading.RLock()


def _rows(path: Path):
    if not path.exists():
        return []
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def _atomic_write(path: Path, rows: list[dict], fieldnames: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, '') for k in fieldnames})
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def set_kv(path: Path, key: str, value, description: str = '', chain_id: str | int | None = None):
    """Atomically set a key/value CSV row, optionally in a chain-scoped file."""
    with _LOCK:
        rows = _rows(path)
        scoped = chain_id is not None
        fieldnames = ['chain_id', 'setting', 'value', 'description'] if scoped else ['setting', 'value', 'description']
        if rows:
            existing = list(rows[0].keys())
            for name in fieldnames:
                if name not in existing:
                    existing.append(name)
            fieldnames = existing
        scope = str(chain_id) if scoped else None
        found = False
        for row in rows:
            same_key = (row.get('setting') or '').strip() == key
            same_scope = True if not scoped else (row.get('chain_id') or '*').strip() == scope
            if same_key and same_scope:
                row['value'] = str(value)
                if description and not (row.get('description') or '').strip():
                    row['description'] = description
                found = True
                break
        if not found:
            row = {k: '' for k in fieldnames}
            if scoped:
                row['chain_id'] = scope
            row['setting'] = key
            row['value'] = str(value)
            row['description'] = description
            rows.append(row)
        _atomic_write(path, rows, fieldnames)


def set_scoped_default(path: Path, key: str, value, description: str = ''):
    """Set the '*' row in a chain-scoped settings CSV."""
    return set_kv(path, key, value, description, chain_id='*')


def set_chain_enabled(path: Path, chain_id: int, enabled: bool):
    with _LOCK:
        rows = _rows(path)
        if not rows:
            raise ValueError('chains.csv is missing or empty')
        fieldnames = list(rows[0].keys())
        found = False
        for row in rows:
            if (row.get('chain_id') or '').strip() == str(chain_id):
                row['enabled'] = 'true' if enabled else 'false'
                found = True
                break
        if not found:
            raise ValueError(f'Unknown chain_id {chain_id}')
        _atomic_write(path, rows, fieldnames)


def set_allowed_behaviours(path: Path, behaviours: list[str]):
    clean = []
    for item in behaviours:
        b = str(item).strip().upper()
        if b and b not in clean:
            clean.append(b)
    set_scoped_default(path, 'allowed_behaviours', '|'.join(clean), 'Behaviours eligible for copy research')


def audit(csv_dir: Path, chat_id, action: str, setting: str = '', old_value: str = '', new_value: str = '', note: str = ''):
    path = Path(csv_dir) / 'auto' / 'telegram_operator_audit.csv'
    with _LOCK:
        rows = _rows(path)
        fieldnames = ['timestamp_epoch', 'chat_id', 'action', 'setting', 'old_value', 'new_value', 'note']
        rows.append({
            'timestamp_epoch': int(time.time()),
            'chat_id': str(chat_id),
            'action': action,
            'setting': setting,
            'old_value': old_value,
            'new_value': new_value,
            'note': note,
        })
        # Keep a bounded audit file so Telegram usage cannot grow it forever.
        _atomic_write(path, rows[-5000:], fieldnames)


def parse_float(value: str, *, minimum: float, maximum: float, name: str) -> float:
    try:
        n = float(str(value).strip())
    except Exception as exc:
        raise ValueError(f'{name} must be a number') from exc
    if not (minimum <= n <= maximum):
        raise ValueError(f'{name} must be between {minimum:g} and {maximum:g}')
    return n


def parse_int(value: str, *, minimum: int, maximum: int, name: str) -> int:
    try:
        n = int(str(value).strip())
    except Exception as exc:
        raise ValueError(f'{name} must be a whole number') from exc
    if not (minimum <= n <= maximum):
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return n
