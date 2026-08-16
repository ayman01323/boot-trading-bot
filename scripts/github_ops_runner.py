#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import os
import shutil
import stat
import subprocess
import time
import zipfile
from pathlib import Path

from learnerbot.config import AppSettings
from learnerbot.telegram import send_to_chats

ROOT = Path('/root/multichain-learning-bot-v2.2-fast-direct-market')
REQUEST = ROOT / 'ops' / 'request.json'
LAST = Path('/root/boot-github-ops.last')
AUDIT = Path('/root/boot-github-ops.log')
DEPLOY_LOG = Path('/root/boot-auto-deploy.log')
BACKUP_DIR = Path('/root/boot-code-backups')
STAGING_DIR = Path('/root/boot-code-staging')
CHALLENGE_UNIT = 'boot-profit-challenge.service'
LEARNER_UNIT = 'learnerbot'
DEPLOY_SERVICE = 'boot-auto-deploy.service'
DEPLOY_TIMER = 'boot-auto-deploy.timer'
BRANCH = 'challenge-auto'
REMOTE = 'origin'

# Fixed operational actions used to install, inspect, repair and operate BOOT.
# This is deliberately NOT an arbitrary remote shell. No command string supplied
# through GitHub is evaluated, and every subprocess uses a fixed argv list.
ALLOWED_ACTIONS = {
    'noop',
    'health',
    'disk_status',
    'root_boot_listing',
    'git_status',
    'git_fetch_challenge',
    'git_compare_local_remote',
    'git_set_filemode_false',
    'repair_deploy_executable',
    'compile_check',
    'targeted_tests',
    'learnerbot_status',
    'start_learnerbot',
    'stop_learnerbot',
    'restart_learnerbot',
    'deploy_service_status',
    'start_deploy_service',
    'restart_deploy_service',
    'deploy_timer_status',
    'start_deploy_timer',
    'stop_deploy_timer',
    'restart_deploy_timer',
    'systemd_daemon_reload',
    'journal_learnerbot_50',
    'journal_deploy_50',
    'tail_deploy_log_60',
    'repair_and_trigger_deploy',
    'challenge_status',
    'challenge_start_5h_001',
    'challenge_stop',
    'zip_boot_code_backup',
    'list_boot_code_backups',
    'unzip_latest_code_backup_to_staging',
    'clear_code_staging',
    'remove_known_old_v20_multiuser',
}


def run(argv: list[str], timeout: int = 30) -> tuple[int, str]:
    p = subprocess.run(argv, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False)
    out = ((p.stdout or '') + ('\n' + p.stderr if p.stderr else '')).strip()
    return p.returncode, out[:5000]


def active(unit: str) -> bool:
    return run(['systemctl', 'is-active', '--quiet', unit], 10)[0] == 0


def service_action(unit: str, verb: str, wait: int = 2) -> tuple[bool, str]:
    if verb not in {'start', 'stop', 'restart'}:
        return False, 'Unsupported service verb.'
    rc, out = run(['systemctl', verb, unit], 45)
    time.sleep(wait)
    is_on = active(unit)
    if verb == 'stop':
        ok = rc == 0 and not is_on
    else:
        ok = rc == 0 and is_on
    return ok, f"{unit}={'ACTIVE' if is_on else 'INACTIVE'}\n{out}".strip()


def service_status(unit: str) -> tuple[bool, str]:
    rc, out = run(['systemctl', 'status', unit, '--no-pager', '-n', '30'], 20)
    # systemctl status returns non-zero for inactive services; returning the text is still useful.
    return True, out or f'{unit}: no status output (rc={rc})'


def chats(app: AppSettings) -> list[str]:
    ids = [str(x).strip() for x in (app.telegram_chat_ids or []) if str(x).strip()]
    users = Path(app.csv_dir) / 'users.csv'
    if users.exists():
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


def file_tail(path: Path, lines: int) -> str:
    if not path.exists():
        return f'Not found: {path}'
    try:
        data = path.read_text(encoding='utf-8', errors='replace').splitlines()
        return '\n'.join(data[-max(1, lines):])[-5000:]
    except Exception as exc:
        return f'{type(exc).__name__}: {exc}'


def root_boot_listing() -> str:
    rows = []
    for p in sorted(Path('/root').iterdir(), key=lambda x: x.name.lower()):
        n = p.name.lower()
        if 'boot' not in n and 'multichain-learning-bot' not in n:
            continue
        try:
            st = p.stat()
            kind = 'dir' if p.is_dir() else 'file'
            rows.append(f'{kind:4} {st.st_size:>12} {p.name}')
        except Exception:
            rows.append(f'?    ?            {p.name}')
    return '\n'.join(rows) or 'No BOOT-related paths found under /root.'


def zip_tracked_code() -> tuple[bool, str]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    rc, tracked = run(['git', 'ls-files'], 30)
    if rc != 0:
        return False, tracked
    files = [Path(x.strip()) for x in tracked.splitlines() if x.strip()]
    if not files:
        return False, 'git ls-files returned no tracked files.'
    stamp = time.strftime('%Y%m%d-%H%M%S')
    out = BACKUP_DIR / f'boot-code-{stamp}.zip'
    count = 0
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for rel in files:
            src = ROOT / rel
            if not src.is_file():
                continue
            # Never archive local runtime/config/secrets even if accidentally tracked later.
            top = rel.parts[0] if rel.parts else ''
            if top in {'data', 'CSVbot', '.venv', '.git'}:
                continue
            info = zipfile.ZipInfo(str(rel))
            st = src.stat()
            info.date_time = time.localtime(st.st_mtime)[:6]
            info.external_attr = (stat.S_IMODE(st.st_mode) & 0xFFFF) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, src.read_bytes())
            count += 1
    return True, f'Created {out}\nTracked code files archived: {count}\nRuntime data/CSVbot/.venv/.git excluded.'


def list_backups() -> str:
    if not BACKUP_DIR.exists():
        return 'No code-backup directory yet.'
    rows = []
    for p in sorted(BACKUP_DIR.glob('boot-code-*.zip'), key=lambda x: x.stat().st_mtime, reverse=True)[:20]:
        rows.append(f'{p.name} size={p.stat().st_size} mtime={int(p.stat().st_mtime)}')
    return '\n'.join(rows) or 'No BOOT code ZIP backups found.'


def safe_extract_latest() -> tuple[bool, str]:
    archives = sorted(BACKUP_DIR.glob('boot-code-*.zip'), key=lambda x: x.stat().st_mtime, reverse=True) if BACKUP_DIR.exists() else []
    if not archives:
        return False, 'No BOOT code backup ZIP found.'
    src = archives[0]
    stamp = time.strftime('%Y%m%d-%H%M%S')
    dest = STAGING_DIR / stamp
    dest.mkdir(parents=True, exist_ok=False)
    base = dest.resolve()
    count = 0
    with zipfile.ZipFile(src, 'r') as zf:
        for info in zf.infolist():
            target = (dest / info.filename).resolve()
            if target != base and base not in target.parents:
                raise RuntimeError(f'Unsafe ZIP path refused: {info.filename}')
            zf.extract(info, dest)
            mode = (info.external_attr >> 16) & 0o7777
            if mode and target.exists():
                os.chmod(target, mode)
            count += 1
    return True, f'Extracted {src.name} to staging only: {dest}\nEntries: {count}\nLive BOOT folder was not overwritten.'


def clear_staging() -> tuple[bool, str]:
    if not STAGING_DIR.exists():
        return True, 'Staging directory does not exist.'
    shutil.rmtree(STAGING_DIR)
    return True, f'Removed staging directory {STAGING_DIR}'


def remove_known_old_v20() -> tuple[bool, str]:
    old = Path('/root/multichain-learning-bot-v2.0-multiuser')
    if old.resolve() == ROOT.resolve():
        return False, 'Safety refusal: old path resolves to live ROOT.'
    if not old.exists():
        return True, f'Already absent: {old}'
    if old.is_symlink():
        return False, 'Safety refusal: old installation path is a symlink.'
    shutil.rmtree(old)
    return True, f'Removed known old installation: {old}'


def action_result(action: str) -> tuple[bool, str]:
    if action == 'noop':
        return True, 'No action requested.'

    if action == 'health':
        _, head = run(['git', 'rev-parse', '--short', 'HEAD'])
        _, disk = run(['df', '-h', '/'])
        st = challenge_state()
        msg = (
            f"learnerbot={'ACTIVE' if active(LEARNER_UNIT) else 'INACTIVE'}\n"
            f"deploy_service={'ACTIVE' if active(DEPLOY_SERVICE) else 'INACTIVE'}\n"
            f"deploy_timer={'ACTIVE' if active(DEPLOY_TIMER) else 'INACTIVE'}\n"
            f"challenge_service={'ACTIVE' if active(CHALLENGE_UNIT) else 'INACTIVE'}\n"
            f"challenge_state={str(st.get('status') or 'UNKNOWN')}\n"
            f"git={head.strip()}\n{disk}"
        )
        return True, msg

    if action == 'disk_status':
        rc1, out1 = run(['df', '-h', '/'])
        rc2, out2 = run(['du', '-sh', str(ROOT)], 40)
        return rc1 == 0, f'{out1}\n\nBOOT folder:\n{out2 if rc2 == 0 else out2}'

    if action == 'root_boot_listing':
        return True, root_boot_listing()

    if action == 'git_status':
        rc1, head = run(['git', 'rev-parse', '--short', 'HEAD'])
        rc2, status = run(['git', 'status', '--short', '--untracked-files=no'])
        return rc1 == 0 and rc2 == 0, f"HEAD={head.strip()}\ntracked_changes={status or 'none'}"

    if action == 'git_fetch_challenge':
        rc, out = run(['git', 'fetch', REMOTE, BRANCH], 90)
        return rc == 0, out or f'Fetched {REMOTE}/{BRANCH}'

    if action == 'git_compare_local_remote':
        rc1, local = run(['git', 'rev-parse', '--short', 'HEAD'])
        rc2, remote = run(['git', 'rev-parse', '--short', f'{REMOTE}/{BRANCH}'])
        return rc1 == 0 and rc2 == 0, f'LOCAL={local.strip()}\nREMOTE={remote.strip()}\nMATCH={local.strip() == remote.strip()}'

    if action == 'git_set_filemode_false':
        rc, out = run(['git', 'config', 'core.fileMode', 'false'])
        return rc == 0, out or 'git core.fileMode=false'

    if action == 'repair_deploy_executable':
        p = ROOT / 'scripts' / 'auto_deploy_challenge.sh'
        if not p.exists():
            return False, f'Not found: {p}'
        os.chmod(p, p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return os.access(p, os.X_OK), f'executable={os.access(p, os.X_OK)} path={p}'

    if action == 'compile_check':
        rc, out = run([str(ROOT / '.venv' / 'bin' / 'python'), '-m', 'compileall', '-q', 'learnerbot', 'scripts'], 120)
        return rc == 0, out or 'Python compile check passed.'

    if action == 'targeted_tests':
        tests = [
            x for x in (
                'tests/test_v231_v3_router_deadline.py',
                'tests/test_v23_full_power.py',
                'tests/test_v233_dynamic_products.py',
            ) if (ROOT / x).exists()
        ]
        if not tests:
            return False, 'No targeted regression tests found.'
        rc, out = run([str(ROOT / '.venv' / 'bin' / 'python'), '-m', 'pytest', '-q', *tests], 240)
        return rc == 0, out or 'Targeted tests passed.'

    if action == 'learnerbot_status':
        return service_status(LEARNER_UNIT)
    if action == 'start_learnerbot':
        return service_action(LEARNER_UNIT, 'start', 4)
    if action == 'stop_learnerbot':
        return service_action(LEARNER_UNIT, 'stop', 2)
    if action == 'restart_learnerbot':
        return service_action(LEARNER_UNIT, 'restart', 4)

    if action == 'deploy_service_status':
        return service_status(DEPLOY_SERVICE)
    if action == 'start_deploy_service':
        # oneshot services normally become inactive after success; use command rc as primary result.
        rc, out = run(['systemctl', 'start', DEPLOY_SERVICE], 180)
        return rc == 0, out or 'boot-auto-deploy.service start completed.'
    if action == 'restart_deploy_service':
        rc, out = run(['systemctl', 'restart', DEPLOY_SERVICE], 180)
        return rc == 0, out or 'boot-auto-deploy.service restart completed.'

    if action == 'deploy_timer_status':
        return service_status(DEPLOY_TIMER)
    if action == 'start_deploy_timer':
        return service_action(DEPLOY_TIMER, 'start', 2)
    if action == 'stop_deploy_timer':
        return service_action(DEPLOY_TIMER, 'stop', 2)
    if action == 'restart_deploy_timer':
        run(['systemctl', 'daemon-reload'], 20)
        return service_action(DEPLOY_TIMER, 'restart', 2)

    if action == 'systemd_daemon_reload':
        rc, out = run(['systemctl', 'daemon-reload'], 30)
        return rc == 0, out or 'systemd daemon-reload completed.'

    if action == 'journal_learnerbot_50':
        rc, out = run(['journalctl', '-u', LEARNER_UNIT, '--no-pager', '-n', '50'], 30)
        return rc == 0, out

    if action == 'journal_deploy_50':
        rc, out = run(['journalctl', '-u', DEPLOY_SERVICE, '--no-pager', '-n', '50'], 30)
        return rc == 0, out

    if action == 'tail_deploy_log_60':
        return True, file_tail(DEPLOY_LOG, 60)

    if action == 'repair_and_trigger_deploy':
        ok1, d1 = action_result('git_set_filemode_false')
        ok2, d2 = action_result('repair_deploy_executable')
        if not (ok1 and ok2):
            return False, f'{d1}\n{d2}'
        rc, out = run(['systemctl', 'start', DEPLOY_SERVICE], 180)
        return rc == 0, f'{d1}\n{d2}\n{out or "deploy trigger completed"}'

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
        return service_action(CHALLENGE_UNIT, 'stop', 2)

    if action == 'zip_boot_code_backup':
        return zip_tracked_code()

    if action == 'list_boot_code_backups':
        return True, list_backups()

    if action == 'unzip_latest_code_backup_to_staging':
        return safe_extract_latest()

    if action == 'clear_code_staging':
        return clear_staging()

    if action == 'remove_known_old_v20_multiuser':
        return remove_known_old_v20()

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
        f.write(line[:10000])

    if action != 'noop':
        icon = '✅' if ok else '❌'
        notify(f"{icon} BOOT GITHUB MAINTENANCE\nRequest: {request_id}\nAction: {action}\n\n{detail}")
    return 0 if ok else 3


if __name__ == '__main__':
    raise SystemExit(main())
