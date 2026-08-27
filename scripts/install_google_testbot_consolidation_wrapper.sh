#!/usr/bin/env bash
set -Eeuo pipefail

# One-time root installer for a narrowly scoped Google test-bot folder consolidation.
# Installs two no-argument commands only:
#   /usr/local/sbin/consolidate-google-test-bots
#   /usr/local/sbin/backup-google-test-bots
# They are fixed to botgoogle and fixed paths; no arbitrary command or path input is accepted.

if [[ $EUID -ne 0 ]]; then
  echo 'Run this installer with sudo/root.' >&2
  exit 2
fi

RUNNER_USER=ayman01323
BASE=/home/ayman01323/ClaudeServer/TestBots
PROD_OLD=/root/multichain-learning-bot-v2.2-fast-direct-market
PROD_NEW=$BASE/boot-trading-bot
SIRISKY_OLD=/root/SiRisky
SIRISKY_NEW=$BASE/SiRisky-runtime
WORK_OLD=/home/ayman01323/ClaudeServer/SiRisky
WORK_NEW=$BASE/SiRisky-workspace
BACKUP_DEST=/home/ayman01323/GoogleServerBuckup
CONSOLIDATE=/usr/local/sbin/consolidate-google-test-bots
BACKUP=/usr/local/sbin/backup-google-test-bots
SUDOERS=/etc/sudoers.d/ayman-google-testbot-maintenance

[[ "$(hostname)" == botgoogle ]] || { echo 'Refusing: this installer is only for botgoogle.' >&2; exit 3; }
id "$RUNNER_USER" >/dev/null 2>&1 || { echo 'Expected user ayman01323 is missing.' >&2; exit 4; }

TMP_C="$(mktemp)"
TMP_B="$(mktemp)"
TMP_S="$(mktemp)"
trap 'rm -f "$TMP_C" "$TMP_B" "$TMP_S"' EXIT

cat >"$TMP_C" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -eq 0 ]] || { echo 'This command accepts no arguments.' >&2; exit 2; }
[[ "$(hostname)" == botgoogle ]] || { echo 'Wrong host.' >&2; exit 3; }

BASE=/home/ayman01323/ClaudeServer/TestBots
PROD_OLD=/root/multichain-learning-bot-v2.2-fast-direct-market
PROD_NEW=$BASE/boot-trading-bot
SIRISKY_OLD=/root/SiRisky
SIRISKY_NEW=$BASE/SiRisky-runtime
WORK_OLD=/home/ayman01323/ClaudeServer/SiRisky
WORK_NEW=$BASE/SiRisky-workspace

install -d -m 0755 -o ayman01323 -g ayman01323 "$BASE"

learner_was_active=false
sirisky_was_active=false
systemctl is-active --quiet learnerbot.service 2>/dev/null && learner_was_active=true || true
systemctl is-active --quiet sirisky.service 2>/dev/null && sirisky_was_active=true || true

rollback() {
  set +e
  echo 'ROLLBACK: restoring original paths.'
  systemctl stop learnerbot.service 2>/dev/null || true
  systemctl stop sirisky.service 2>/dev/null || true
  [[ -L "$PROD_OLD" ]] && rm -f "$PROD_OLD"
  [[ -d "$PROD_NEW" && ! -e "$PROD_OLD" ]] && mv "$PROD_NEW" "$PROD_OLD"
  [[ -L "$SIRISKY_OLD" ]] && rm -f "$SIRISKY_OLD"
  [[ -d "$SIRISKY_NEW" && ! -e "$SIRISKY_OLD" ]] && mv "$SIRISKY_NEW" "$SIRISKY_OLD"
  [[ -L "$WORK_OLD" ]] && rm -f "$WORK_OLD"
  [[ -d "$WORK_NEW" && ! -e "$WORK_OLD" ]] && mv "$WORK_NEW" "$WORK_OLD"
  [[ "$learner_was_active" == true ]] && systemctl start learnerbot.service || true
  [[ "$sirisky_was_active" == true ]] && systemctl start sirisky.service || true
}
trap rollback ERR

[[ "$learner_was_active" == true ]] && systemctl stop learnerbot.service || true
[[ "$sirisky_was_active" == true ]] && systemctl stop sirisky.service || true

if [[ -L "$PROD_OLD" ]]; then
  [[ "$(readlink -f "$PROD_OLD")" == "$PROD_NEW" ]]
else
  [[ -d "$PROD_OLD" ]]
  [[ ! -e "$PROD_NEW" ]]
  mv "$PROD_OLD" "$PROD_NEW"
  ln -s "$PROD_NEW" "$PROD_OLD"
fi

if [[ -L "$SIRISKY_OLD" ]]; then
  [[ "$(readlink -f "$SIRISKY_OLD")" == "$SIRISKY_NEW" ]]
else
  [[ -d "$SIRISKY_OLD" ]]
  [[ ! -e "$SIRISKY_NEW" ]]
  mv "$SIRISKY_OLD" "$SIRISKY_NEW"
  ln -s "$SIRISKY_NEW" "$SIRISKY_OLD"
fi

if [[ -L "$WORK_OLD" ]]; then
  [[ "$(readlink -f "$WORK_OLD")" == "$WORK_NEW" ]]
elif [[ -d "$WORK_OLD" ]]; then
  [[ ! -e "$WORK_NEW" ]]
  mv "$WORK_OLD" "$WORK_NEW"
  ln -s "$WORK_NEW" "$WORK_OLD"
fi

[[ -d "$PROD_NEW" && -d "$SIRISKY_NEW" ]]
[[ -L "$PROD_OLD" && -L "$SIRISKY_OLD" ]]
[[ "$(readlink -f "$PROD_OLD")" == "$PROD_NEW" ]]
[[ "$(readlink -f "$SIRISKY_OLD")" == "$SIRISKY_NEW" ]]
[[ -x "$PROD_OLD/.venv/bin/python" ]]
[[ -x "$SIRISKY_OLD/.venv/bin/python" ]]
git -C "$PROD_OLD" rev-parse --is-inside-work-tree >/dev/null

[[ "$learner_was_active" == true ]] && { systemctl start learnerbot.service; sleep 3; systemctl is-active --quiet learnerbot.service; } || true
[[ "$sirisky_was_active" == true ]] && { systemctl start sirisky.service; sleep 3; systemctl is-active --quiet sirisky.service; } || true

trap - ERR

echo 'CONSOLIDATION_OK=true'
echo "base=$BASE"
echo "production=$PROD_NEW"
echo "sirisky_runtime=$SIRISKY_NEW"
echo "sirisky_workspace=$WORK_NEW"
echo "production_compat=$PROD_OLD -> $(readlink "$PROD_OLD")"
echo "sirisky_compat=$SIRISKY_OLD -> $(readlink "$SIRISKY_OLD")"
echo "workspace_compat=$WORK_OLD -> $(readlink "$WORK_OLD" 2>/dev/null || echo not-present)"
echo "learnerbot=$(systemctl is-active learnerbot.service 2>/dev/null || true)"
echo "sirisky=$(systemctl is-active sirisky.service 2>/dev/null || true)"
du -sh "$PROD_NEW" "$SIRISKY_NEW" "$WORK_NEW" 2>/dev/null || true
EOF

cat >"$TMP_B" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -eq 0 ]] || { echo 'This command accepts no arguments.' >&2; exit 2; }
[[ "$(hostname)" == botgoogle ]] || { echo 'Wrong host.' >&2; exit 3; }
SOURCE=/home/ayman01323/ClaudeServer
DEST=/home/ayman01323/GoogleServerBuckup
RETENTION_DAYS=14
[[ -d "$SOURCE" ]] || { echo 'ClaudeServer source missing.' >&2; exit 4; }
install -d -m 0700 -o ayman01323 -g ayman01323 "$DEST"
STAMP="$(date -u +%Y-%m-%d_%H-%M-%SZ)"
OUT="$DEST/claude-server-all-${STAMP}.tar.gz"
tar -C /home/ayman01323 -czf "$OUT" ClaudeServer
chown ayman01323:ayman01323 "$OUT"
chmod 0600 "$OUT"
tar -tzf "$OUT" >/dev/null
sha256sum "$OUT" > "$OUT.sha256"
chown ayman01323:ayman01323 "$OUT.sha256"
chmod 0600 "$OUT.sha256"
find "$DEST" -maxdepth 1 -type f \( -name 'claude-server-all-*.tar.gz' -o -name 'claude-server-all-*.tar.gz.sha256' \) -mtime +"$RETENTION_DAYS" -delete
printf 'BACKUP_OK=true\narchive=%s\n' "$OUT"
ls -lh "$OUT" "$OUT.sha256"
EOF

cat >"$TMP_S" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $CONSOLIDATE
$RUNNER_USER ALL=(root) NOPASSWD: $BACKUP
EOF

chmod 0755 "$TMP_C" "$TMP_B"
chmod 0440 "$TMP_S"
visudo -cf "$TMP_S" >/dev/null
install -o root -g root -m 0755 "$TMP_C" "$CONSOLIDATE"
install -o root -g root -m 0755 "$TMP_B" "$BACKUP"
install -o root -g root -m 0440 "$TMP_S" "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

echo 'Installed restricted Google test-bot maintenance bridge.'
echo "consolidate=$CONSOLIDATE"
echo "backup=$BACKUP"
echo "sudoers=$SUDOERS"
