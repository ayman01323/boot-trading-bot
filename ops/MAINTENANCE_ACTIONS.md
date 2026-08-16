# BOOT GitHub Maintenance Actions

The GitHub maintenance controller is intentionally **not a remote shell**. Each request selects one fixed action implemented in `scripts/github_ops_runner.py`. No command text from GitHub is passed to a shell.

Use `ops/request.json` with a new unique `id` and one of the actions below.

## Health and diagnostics

- `health` — learnerbot, auto-deploy service/timer, challenge state, Git HEAD and disk summary.
- `disk_status` — filesystem space and BOOT folder size.
- `root_boot_listing` — list BOOT/multichain-related paths under `/root`.
- `git_status` — local HEAD and tracked changes.
- `git_fetch_challenge` — fetch fixed `origin/challenge-auto`.
- `git_compare_local_remote` — compare local HEAD with `origin/challenge-auto`.
- `journal_learnerbot_50` — last 50 learnerbot journal lines.
- `journal_deploy_50` — last 50 auto-deploy journal lines.
- `tail_deploy_log_60` — last 60 lines of `/root/boot-auto-deploy.log`.

## Git/deploy repairs used during BOOT recovery

- `git_set_filemode_false` — set local `core.fileMode=false`.
- `repair_deploy_executable` — restore execute permission on `scripts/auto_deploy_challenge.sh`.
- `compile_check` — compile `learnerbot` and `scripts` with the project venv.
- `targeted_tests` — run the v2.3 regression test set used by guarded deployment.
- `repair_and_trigger_deploy` — apply file-mode/executable repairs and trigger the guarded deploy service.
- `systemd_daemon_reload` — fixed `systemctl daemon-reload`.

## learnerbot service

- `learnerbot_status`
- `start_learnerbot`
- `stop_learnerbot`
- `restart_learnerbot`

## GitHub auto-deploy service

- `deploy_service_status`
- `start_deploy_service`
- `restart_deploy_service`

## GitHub auto-deploy timer

- `deploy_timer_status`
- `start_deploy_timer`
- `stop_deploy_timer`
- `restart_deploy_timer`

## Profit challenge

- `challenge_status`
- `challenge_start_5h_001` — fixed maximum five hours, $0.01 realised-user-net target, 15-minute reporting.
- `challenge_stop`

## ZIP / backup / extraction

- `zip_boot_code_backup` — creates a ZIP in `/root/boot-code-backups` containing Git-tracked BOOT code only. Runtime `data/`, `CSVbot/`, `.venv/` and `.git/` are deliberately excluded.
- `list_boot_code_backups` — list recent BOOT code ZIPs.
- `unzip_latest_code_backup_to_staging` — safely extracts the latest BOOT code ZIP into `/root/boot-code-staging/<timestamp>`; it never overwrites the live installation.
- `clear_code_staging` — remove only the dedicated code-staging directory.

## Known old-installation cleanup

- `remove_known_old_v20_multiuser` — remove only `/root/multichain-learning-bot-v2.0-multiuser`. It refuses symlinks and can never target the current live ROOT.

## Request format

```json
{
  "id": "unique-request-id",
  "action": "health"
}
```

A request ID is processed once and recorded in `/root/boot-github-ops.last`; results are appended to `/root/boot-github-ops.log` and sent to configured/ACTIVE Telegram recipients when possible.

## Safety boundary

The controller does not support arbitrary commands, `bash -c`, `sh -c`, `eval`, arbitrary paths, arbitrary service names, arbitrary `rm`, or arbitrary ZIP extraction destinations. New operational abilities must be added as explicit reviewed actions.