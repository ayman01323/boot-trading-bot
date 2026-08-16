#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from learnerbot.config import AppSettings
from learnerbot.telegram import send_to_chats

ROOT = Path('/root/multichain-learning-bot-v2.2-fast-direct-market')
REQUEST = ROOT / 'ops' / 'request.json'
LAST = Path('/root/boot-github-ops.last')
AUDIT = Path('/root/boot-github-ops.log')
CHALLENGE_UNIT = 'boot-profit-challenge.service'

# Intentionally fixed and narrow. This is NOT a remote shell.
ALLOWED_ACTIONS = {
    'noop',
    'health',
    'disk_status',
    'git_status',
    'restart_learnerbot',
    'restart_deploy_timer',
    'challenge_status',
    'challenge_start_5h_001',
    'challenge_stop',
}


def run(argv: list[str], timeout: int = 30) -> tuple[int, str]:
    p = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
    out = ((p.stdout or '') + ('\n' + p.stderr if p.stderr else '')).strip()
    return p.returncode, out[:3500]


def active(unit: str) -> bool:
    return run(['systemctl', 'is-active', '--quiet', unit], 10)[0] == 0


def chats(app: AppSettings) -> list[str]:
    ids = [str(x).strip() for x in (app.telegram_chat_ids or []) if str(x).strip()]
    users = Path(app.csv_dir) / 'users.csv'
    if users.exists():
        import csv
        try:
            with users.open('r', encoding='utf-8-sig', newline='') as f:
                for row in csv.DictReader(f):
                    if str(row.get('status') or '').upper() == 'ACTIVE':
                        tid = str(row.get('telegram_id') or '').strip()
                        if tid:
                            ids.append(tid)
        except Exception:
            pass
    return list(dict.fromkeys(ids))[:5]


def notify(text: str) -> None:
    try:
        app = AppSettings.load()
        ids = chats(app)
        if app.telegram_bot_token and ids:
            send_to_chats(app.telegram_bot_token, ids, text)
    except Exception:
        pass


def challenge_state() -> dict:
    try:
        app = AppSettings.load()
        p = Path(app.csv_dir) / 'auto' / 'profit_challenge_status.json'
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
    except Exception:
        return {}


def action_result(action: str) -> tuple[bool, str]:
    if action == 'noop':
        return True, 'No action requested.'

    if action == 'health':
        _, head = run(['git', 'rev-parse', '--short', 'HEAD'])
        _, disk = run(['df', '-h', '/'])
        st = challenge_state()
        msg = (
            f"learnerbot={'ACTIVE' if active('learnerbot') else 'INACTIVE'}\n"
            f"deploy_timer={'ACTIVE' if active('boot-auto-deploy.timer') else 'INACTIVE'}\n"
            f"challenge_service={'ACTIVE' if active(CHALLENGE_UNIT) else 'INACTIVE'}\n"
            f"challenge_state={str(st.get('status') or 'UNKNOWN')}\n"
            f"git={head.strip()}\n{disk}"
        )
        return True, msg

    if action == 'disk_status':
        rc, out = run(['df', '-h', '/'])
        return rc == 0, out

    if action == 'git_status':
        rc1, head = run(['git', 'rev-parse', '--short', 'HEAD'])
        rc2, status = run(['git', 'status', '--short', '--untracked-files=no'])
        return rc1 == 0 and rc2 == 0, f"HEAD={head.strip()}\ntracked_changes={status or 'none'}"

    if action == 'restart_learnerbot':
        rc, out = run(['systemctl', 'restart', 'learnerbot'], 40)
        time.sleep(4)
        ok = rc == 0 and active('learnerbot')
        return ok, f"learnerbot={'ACTIVE' if ok else 'FAILED'}\n{out}"

    if action == 'restart_deploy_timer':
        run(['systemctl', 'daemon-reload'], 20)
        rc, out = run(['systemctl', 'restart', 'boot-auto-deploy.timer'], 30)
        time.sleep(2)
        ok = rc == 0 and active('boot-auto-deploy.timer')
        return ok, f"boot-auto-deploy.timer={'ACTIVE' if ok else 'FAILED'}\n{out}"

    if action == 'challenge_status':
        st = challenge_state()
        return True, (
            f"service={'ACTIVE' if active(CHALLENGE_UNIT) else 'INACTIVE'}\n"
            f"status={st.get('status', 'UNKNOWN')}\n"
            f"realised_user_net_usd={st.get('realised_user_net_usd', '0')}\n"
            f"target_usd={st.get('target_usd', '0.01')}\n"
            f"successful_trades={st.get('successful_trades', '0')}\n"
            f"stage={st.get('stage', '-') }"
        )

    if action == 'challenge_start_5h_001':
        if active(CHALLENGE_UNIT):
            return True, 'Challenge already ACTIVE.'
        run(['systemctl', 'reset-failed', CHALLENGE_UNIT], 10)
        argv = [
            'systemd-run', '--quiet', '--unit=boot-profit-challenge',
            '--property=Type=simple', f'--property=WorkingDirectory={ROOT}',
            str(ROOT / '.venv' / 'bin' / 'python'), str(ROOT / 'scripts' / 'profit_challenge.py'),
            '--hours', '5', '--target-usd', '0.01', '--report-minutes', '15',
        ]
        rc, out = run(argv, 30)
        time.sleep(3)
        ok = rc == 0 and active(CHALLENGE_UNIT)
        return ok, f"challenge={'ACTIVE' if ok else 'FAILED'} target=$0.01 max=5h\n{out}"

    if action == 'challenge_stop':
        rc, out = run(['systemctl', 'stop', CHALLENGE_UNIT], 30)
        time.sleep(2)
        ok = rc == 0 and not active(CHALLENGE_UNIT)
        return ok, f"challenge={'STOPPED' if ok else 'STOP_FAILED'}\n{out}"

    return False, 'Unsupported action.'


def main() -> int:
    if not REQUEST.exists():
        return 0
    try:
        req = json.loads(REQUEST.read_text(encoding='utf-8'))
    except Exception as exc:
        notify(f"❌ BOOT GitHub maintenance request invalid: {type(exc).__name__}")
        return 2

    request_id = str(req.get('id') or '').strip()
    action = str(req.get('action') or '').strip()
    if not request_id or not action:
        return 2

    if LAST.exists() and LAST.read_text(encoding='utf-8').strip() == request_id:
        return 0

    if action not in ALLOWED_ACTIONS:
        ok, detail = False, f"Refused unsupported action: {action}"
    else:
        try:
            ok, detail = action_result(action)
        except Exception as exc:
            ok, detail = False, f"{type(exc).__name__}: {exc}"

    LAST.write_text(request_id, encoding='utf-8')
    line = f"{int(time.time())} id={request_id} action={action} ok={ok} detail={detail.replace(chr(10), ' | ')}\n"
    with AUDIT.open('a', encoding='utf-8') as f:
        f.write(line[:8000])

    if action != 'noop':
        icon = '✅' if ok else '❌'
        notify(f"{icon} BOOT GITHUB MAINTENANCE\nRequest: {request_id}\nAction: {action}\n\n{detail}")
    return 0 if ok else 3


if __name__ == '__main__':
    raise SystemExit(main())
