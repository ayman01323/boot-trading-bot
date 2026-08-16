from __future__ import annotations

import csv
import hashlib
import os
import time
from pathlib import Path


def _rows(path: Path):
    if not path.exists():
        return []
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def _write(path: Path, rows: list[dict], fieldnames: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows([{k: row.get(k, '') for k in fieldnames} for row in rows])
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def queue_armed_recommendations(app, recommendations: list[dict]) -> dict:
    """Queue eligible ARMED recommendations for a separate local executor.

    Security boundary: this function NEVER signs, approves, broadcasts, withdraws,
    or reads a private key. It only creates a local queue row.
    """
    op = app.operator_settings()
    queue_enabled = str(op.get('execution_queue_enabled', 'true')).strip().lower() in {'1','true','yes','on'}
    path = Path(app.csv_dir) / 'auto' / 'execution_queue.csv'
    fieldnames = [
        'queue_id','recommendation_id','chain_id','chain_slug','wallet','behaviour','route_id',
        'recommended_input_base','minimum_expected_profit_base','status','created_at','updated_at','note'
    ]
    rows = _rows(path)
    existing = {r.get('recommendation_id') for r in rows}
    added = 0
    if queue_enabled:
        now = int(time.time())
        for rec in recommendations:
            if (rec.get('recommendation_mode') or '').upper() != 'ARMED':
                continue
            if rec.get('action') != 'IN':
                continue
            rid = str(rec.get('recommendation_id') or '')
            if not rid or rid in existing:
                continue
            qid = hashlib.sha256(f"{rid}|{now}".encode()).hexdigest()[:24]
            rows.append({
                'queue_id': qid,
                'recommendation_id': rid,
                'chain_id': rec.get('chain_id',''),
                'chain_slug': rec.get('chain_slug',''),
                'wallet': rec.get('wallet',''),
                'behaviour': rec.get('behaviour',''),
                'route_id': rec.get('route_id',''),
                'recommended_input_base': rec.get('recommended_input_base',''),
                'minimum_expected_profit_base': rec.get('conservative_net_profit_base',''),
                'status': 'PENDING_LOCAL_EXECUTOR',
                'created_at': now,
                'updated_at': now,
                'note': 'Historical queue rows are never auto-signed. v2.2 auto execution uses only fresh scanner routes that are re-quoted and re-simulated immediately before signing.',
            })
            existing.add(rid); added += 1
    if rows or not path.exists():
        _write(path, rows[-5000:], fieldnames)
    return {'added': added, 'total': len(rows), 'path': str(path), 'enabled': queue_enabled}


def queue_summary(csv_dir: Path) -> dict:
    path = Path(csv_dir) / 'auto' / 'execution_queue.csv'
    rows = _rows(path)
    pending = [r for r in rows if (r.get('status') or '') == 'PENDING_LOCAL_EXECUTOR']
    return {'path': path, 'total': len(rows), 'pending': len(pending), 'recent': rows[-10:]}
