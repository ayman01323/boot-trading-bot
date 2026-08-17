#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path

from learnerbot.config import AppSettings

ROOT = Path('/root/multichain-learning-bot-v2.2-fast-direct-market')
REMOTE = 'origin'
STATUS_BRANCH = 'server-status'
STATUS_FILE = 'server_status.json'
SAFE_SUCCESS = {'SUCCESS', 'SUCCESS_FEE_PENDING'}


def run(argv: list[str], *, timeout: int = 30, input_text: str | None = None, env: dict | None = None) -> tuple[int, str]:
    p = subprocess.run(
        argv,
        cwd=ROOT,
        text=True,
        input=input_text,
        capture_output=True,
        timeout=timeout,
        check=False,
        env=env,
    )
    out = ((p.stdout or '') + ('\n' + p.stderr if p.stderr else '')).strip()
    return p.returncode, out


def service_active(unit: str) -> bool:
    return run(['systemctl', 'is-active', '--quiet', unit], timeout=8)[0] == 0


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    except Exception:
        return {}


def rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with path.open('r', encoding='utf-8-sig', newline='') as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def epoch(row: dict) -> int:
    try:
        return int(float(row.get('timestamp_epoch') or 0))
    except Exception:
        return 0


def truthy(value) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on', 'y'}


def fnum(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def telemetry(app: AppSettings) -> dict:
    csv_dir = Path(app.csv_dir)
    now = int(time.time())
    since = now - 15 * 60

    challenge = read_json(csv_dir / 'auto' / 'profit_challenge_status.json')
    adaptive = read_json(csv_dir / 'auto' / 'adaptive_strategy_status.json')

    sims = [r for r in rows(csv_dir / 'auto' / 'auto_trade_simulations.csv') if epoch(r) >= since]
    execs = [r for r in rows(csv_dir / 'auto' / 'auto_trade_execution.csv') if epoch(r) >= since]
    rejects = Counter()
    for r in sims:
        if truthy(r.get('simulation_ok')):
            continue
        reason = str(r.get('reason') or 'unknown').strip()
        if reason:
            rejects[reason[:140]] += 1

    opps = rows(csv_dir / 'auto' / 'live_opportunities.csv') or rows(csv_dir / 'live_opportunities.csv') or rows(csv_dir / 'auto' / 'full_power_opportunities.csv')
    enabled = [r for r in opps if truthy(r.get('enabled'))]

    def expected_net(r: dict) -> float:
        for key in ('expected_net_base', 'net_profit_base', 'expected_user_net_base'):
            if r.get(key) not in (None, ''):
                return fnum(r.get(key))
        return (
            fnum(r.get('expected_gross_profit_base'))
            - max(fnum(r.get('gas_cost_base')), fnum(r.get('estimated_gas_cost_base')), fnum(r.get('gas_reserve_base')))
            - fnum(r.get('slippage_reserve_base'))
            - fnum(r.get('profit_fee_base'))
        )

    best = None
    if opps:
        pool = enabled or opps
        r = max(pool, key=expected_net)
        best = {
            'chain_slug': str(r.get('chain_slug') or ''),
            'route_kind': str(r.get('route_kind') or r.get('behaviour') or ''),
            'expected_net_base': expected_net(r),
            'enabled': truthy(r.get('enabled')),
            'source': 'DIRECT' if str(r.get('wallet') or '').upper() == 'DIRECT_MARKET' else 'LEARNED',
        }

    rc, head = run(['git', 'rev-parse', '--short', 'HEAD'], timeout=8)
    rc2, disk = run(['df', '-Pk', '/'], timeout=8)
    disk_free_kb = None
    if rc2 == 0:
        lines = disk.splitlines()
        if len(lines) >= 2:
            parts = lines[-1].split()
            if len(parts) >= 4:
                try:
                    disk_free_kb = int(parts[3])
                except Exception:
                    pass

    return {
        'published_epoch': now,
        'git_head': head.strip() if rc == 0 else '',
        'services': {
            'learnerbot': service_active('learnerbot'),
            'deploy_timer': service_active('boot-auto-deploy.timer'),
            'challenge': service_active('boot-profit-challenge.service'),
        },
        'challenge': {
            'status': str(challenge.get('status') or 'UNKNOWN'),
            'target_usd': fnum(challenge.get('target_usd'), 0.01),
            'realised_user_net_usd': fnum(challenge.get('realised_user_net_usd'), 0.0),
            'successful_trades': int(fnum(challenge.get('successful_trades'), 0)),
            'stage': str(challenge.get('stage') or '-'),
            'start_epoch': int(fnum(challenge.get('start_epoch'), 0)),
            'deadline_epoch': int(fnum(challenge.get('deadline_epoch') or challenge.get('end_epoch'), 0)),
        },
        'adaptive': {
            'profile': str(adaptive.get('profile') or 'unknown'),
            'updated_epoch': int(fnum(adaptive.get('updated_epoch') or adaptive.get('timestamp_epoch'), 0)),
        },
        'last_15m': {
            'simulations': len(sims),
            'simulation_passes': sum(truthy(r.get('simulation_ok')) for r in sims),
            'execution_records': len(execs),
            'confirmed_successes': sum(str(r.get('status') or '').upper() in SAFE_SUCCESS for r in execs),
            'top_rejects': [{'reason': reason, 'count': count} for reason, count in rejects.most_common(5)],
        },
        'opportunities': {
            'total': len(opps),
            'eligible': len(enabled),
            'best': best,
        },
        'disk_free_kb': disk_free_kb,
    }


def publish(payload: dict) -> tuple[bool, str]:
    text = json.dumps(payload, indent=2, sort_keys=True) + '\n'

    rc, blob = run(['git', 'hash-object', '-w', '--stdin'], input_text=text, timeout=20)
    if rc != 0:
        return False, f'hash-object failed: {blob[:500]}'
    blob = blob.strip()

    tree_input = f'100644 blob {blob}\t{STATUS_FILE}\n'
    rc, tree = run(['git', 'mktree'], input_text=tree_input, timeout=20)
    if rc != 0:
        return False, f'mktree failed: {tree[:500]}'
    tree = tree.strip()

    rc, remote = run(['git', 'ls-remote', '--heads', REMOTE, f'refs/heads/{STATUS_BRANCH}'], timeout=30)
    parent = ''
    if rc == 0 and remote.strip():
        parent = remote.split()[0].strip()

    env = os.environ.copy()
    env.update({
        'GIT_AUTHOR_NAME': 'BOOT Server Status',
        'GIT_AUTHOR_EMAIL': 'boot-status@localhost',
        'GIT_COMMITTER_NAME': 'BOOT Server Status',
        'GIT_COMMITTER_EMAIL': 'boot-status@localhost',
    })
    args = ['git', 'commit-tree', tree, '-m', f"BOOT server status {payload.get('published_epoch')}"]
    if parent:
        args.extend(['-p', parent])
    rc, commit = run(args, timeout=20, env=env)
    if rc != 0:
        return False, f'commit-tree failed: {commit[:500]}'
    commit = commit.strip()

    rc, out = run(['git', 'push', REMOTE, f'{commit}:refs/heads/{STATUS_BRANCH}'], timeout=90)
    if rc != 0:
        return False, f'push failed: {out[:1000]}'
    return True, f'published {commit[:12]} to {STATUS_BRANCH}'


def main() -> int:
    try:
        app = AppSettings.load()
        payload = telemetry(app)
        ok, detail = publish(payload)
        print(detail, flush=True)
        return 0 if ok else 2
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}', flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
