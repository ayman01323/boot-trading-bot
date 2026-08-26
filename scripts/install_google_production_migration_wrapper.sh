#!/usr/bin/env bash
set -Eeuo pipefail

STAGING_DIR="/home/ayman01323/NamecheapMigration/multichain-learning-bot-v2.2-fast-direct-market"
FINAL_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
RUNNER_USER="ayman01323"
SERVICE="learnerbot.service"
INSPECT="/usr/local/sbin/inspect-google-production-bot"
PREPARE="/usr/local/sbin/prepare-google-production-bot"
REFRESH="/usr/local/sbin/refresh-google-production-bot"
VERIFY="/usr/local/sbin/verify-google-production-bot"
SUDOERS="/etc/sudoers.d/google-production-migration"
LOCK="/var/lock/google-production-migration.lock"
STATE_DIR="/var/lib/google-production-migration"
MARKER="$STATE_DIR/prepared"
BACKUP_RECORD="$STATE_DIR/preexisting_backup_path"
OLD_MARKER="/var/tmp/google-production-migration.prepared"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash scripts/install_google_production_migration_wrapper.sh" >&2
  exit 2
fi
if [[ "$(hostname)" != "botgoogle" ]]; then
  echo "Refusing install: expected hostname botgoogle, got $(hostname)" >&2
  exit 3
fi
if ! id "$RUNNER_USER" >/dev/null 2>&1; then
  echo "Refusing install: user $RUNNER_USER does not exist" >&2
  exit 4
fi
if [[ ! -d "$STAGING_DIR/.git" || ! -f "$STAGING_DIR/requirements.txt" ]]; then
  echo "Refusing install: verified migration staging directory is missing" >&2
  exit 5
fi
command -v rsync >/dev/null 2>&1 || {
  apt-get update -y >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -y rsync python3-venv >/dev/null
}
python3 -m venv --help >/dev/null 2>&1 || {
  apt-get update -y >/dev/null
  DEBIAN_FRONTEND=noninteractive apt-get install -y python3-venv >/dev/null
}
install -d -o root -g root -m 0700 "$STATE_DIR"
rm -f "$OLD_MARKER"

cat >"$INSPECT" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
STAGING_DIR="/home/ayman01323/NamecheapMigration/multichain-learning-bot-v2.2-fast-direct-market"
FINAL_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
STATE_DIR="/var/lib/google-production-migration"
MARKER="$STATE_DIR/prepared"
BACKUP_RECORD="$STATE_DIR/preexisting_backup_path"
[[ "$(hostname)" == "botgoogle" ]] || exit 41
echo '=== GOOGLE FINAL PATH INSPECTION ==='
echo "final_exists=$([[ -e "$FINAL_DIR" ]] && echo true || echo false)"
if [[ -e "$FINAL_DIR" ]]; then
  stat -c 'final_owner=%U final_group=%G final_mode=%a' "$FINAL_DIR"
  echo "final_size=$(du -sh "$FINAL_DIR" 2>/dev/null | awk '{print $1}')"
  echo "final_files=$(find "$FINAL_DIR" -xdev -type f 2>/dev/null | wc -l)"
  echo "final_git=$([[ -d "$FINAL_DIR/.git" ]] && echo true || echo false)"
  if [[ -d "$FINAL_DIR/.git" ]]; then
    echo "final_sha=$(git -C "$FINAL_DIR" rev-parse HEAD 2>/dev/null || echo unknown)"
    echo "final_dirty_files=$(git -C "$FINAL_DIR" status --porcelain 2>/dev/null | wc -l)"
  fi
  echo "final_venv=$([[ -x "$FINAL_DIR/.venv/bin/python" ]] && echo true || echo false)"
  echo "final_learnerbot=$([[ -d "$FINAL_DIR/learnerbot" ]] && echo true || echo false)"
  echo "final_requirements=$([[ -f "$FINAL_DIR/requirements.txt" ]] && echo true || echo false)"
  if [[ -d "$FINAL_DIR/.git" ]]; then
    echo "staging_vs_final_dryrun_changes=$(rsync -aHni --delete --exclude='.venv/' --exclude='.venv-preflight-google/' --exclude='.google-preflight-venv/' "$STAGING_DIR/" "$FINAL_DIR/" 2>/dev/null | wc -l)"
  fi
fi
echo "migration_marker=$([[ -f "$MARKER" ]] && echo true || echo false)"
echo "preexisting_backup_record=$([[ -f "$BACKUP_RECORD" ]] && cat "$BACKUP_RECORD" || echo none)"
echo "service_active=$(systemctl is-active "$SERVICE" 2>/dev/null || true)"
echo "service_enabled=$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"
echo "trading_started=false"
EOF

cat >"$PREPARE" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
STAGING_DIR="/home/ayman01323/NamecheapMigration/multichain-learning-bot-v2.2-fast-direct-market"
FINAL_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
LOCK="/var/lock/google-production-migration.lock"
STATE_DIR="/var/lib/google-production-migration"
MARKER="$STATE_DIR/prepared"
BACKUP_RECORD="$STATE_DIR/preexisting_backup_path"
exec 9>"$LOCK"
flock -w 60 9 || { echo 'Migration lock busy' >&2; exit 10; }
[[ "$(hostname)" == "botgoogle" ]] || exit 11
[[ -d "$STAGING_DIR/.git" ]] || { echo 'Staging repo missing' >&2; exit 12; }
if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
  echo 'Refusing: learnerbot.service is already active on Google' >&2
  exit 13
fi
if pgrep -af "$STAGING_DIR" | grep -v -E 'grep|Actions.Runner|Runner.Worker' >/dev/null 2>&1; then
  echo 'Refusing: a process is running from the staging directory' >&2
  exit 14
fi
install -d -o root -g root -m 0700 "$STATE_DIR"
if [[ -e "$FINAL_DIR" && ! -f "$MARKER" ]]; then
  TS="$(date -u +'%Y%m%dT%H%M%SZ')"
  BACKUP="${FINAL_DIR}.pre-migration.${TS}"
  [[ ! -e "$BACKUP" ]] || { echo "Refusing: backup path already exists: $BACKUP" >&2; exit 15; }
  mv "$FINAL_DIR" "$BACKUP"
  printf '%s\n' "$BACKUP" > "$BACKUP_RECORD"
  chmod 0600 "$BACKUP_RECORD"
  echo "preserved_existing_final=$BACKUP"
fi
mkdir -p "$FINAL_DIR"
rsync -aH --delete --exclude='.venv/' --exclude='.venv-preflight-google/' --exclude='.google-preflight-venv/' "$STAGING_DIR/" "$FINAL_DIR/"
chown -R root:root "$FINAL_DIR"
rm -rf "$FINAL_DIR/.venv"
python3 -m venv "$FINAL_DIR/.venv"
"$FINAL_DIR/.venv/bin/python" -m pip install -q --upgrade pip
"$FINAL_DIR/.venv/bin/python" -m pip install -q -r "$FINAL_DIR/requirements.txt"
"$FINAL_DIR/.venv/bin/python" -m compileall -q "$FINAL_DIR/learnerbot" "$FINAL_DIR/scripts"
PYTHONPATH="$FINAL_DIR" "$FINAL_DIR/.venv/bin/python" - <<'PY'
import learnerbot
print('learnerbot_import=PASS')
print('learnerbot_version=' + str(getattr(learnerbot, '__version__', 'unknown')))
PY
install -o root -g root -m 0644 "$FINAL_DIR/systemd/learnerbot.service" /etc/systemd/system/learnerbot.service
systemctl daemon-reload
systemctl disable learnerbot.service >/dev/null 2>&1 || true
SHA="$(git -C "$FINAL_DIR" rev-parse HEAD)"
printf 'prepared_at=%s\nsha=%s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$SHA" > "$MARKER"
chmod 0600 "$MARKER"
echo "GOOGLE_PRODUCTION_PATH_PREPARED=true"
echo "final_dir=$FINAL_DIR"
echo "sha=$SHA"
echo "size=$(du -sh "$FINAL_DIR" | awk '{print $1}')"
echo "files=$(find "$FINAL_DIR" -xdev -type f 2>/dev/null | wc -l)"
echo "service_active=$(systemctl is-active learnerbot.service 2>/dev/null || true)"
echo "service_enabled=$(systemctl is-enabled learnerbot.service 2>/dev/null || true)"
echo "trading_started=false"
EOF

cat >"$REFRESH" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
STAGING_DIR="/home/ayman01323/NamecheapMigration/multichain-learning-bot-v2.2-fast-direct-market"
FINAL_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
LOCK="/var/lock/google-production-migration.lock"
MARKER="/var/lib/google-production-migration/prepared"
exec 9>"$LOCK"
flock -w 60 9 || { echo 'Migration lock busy' >&2; exit 20; }
[[ "$(hostname)" == "botgoogle" ]] || exit 21
[[ -f "$MARKER" && -d "$FINAL_DIR/.venv" ]] || { echo 'Production path has not been prepared' >&2; exit 22; }
if systemctl is-active --quiet "$SERVICE" 2>/dev/null; then
  echo 'Refusing refresh while learnerbot.service is active on Google' >&2
  exit 23
fi
rsync -aH --delete --exclude='.venv/' --exclude='.venv-preflight-google/' --exclude='.google-preflight-venv/' "$STAGING_DIR/" "$FINAL_DIR/"
chown -R root:root "$FINAL_DIR"
"$FINAL_DIR/.venv/bin/python" -m pip install -q -r "$FINAL_DIR/requirements.txt"
"$FINAL_DIR/.venv/bin/python" -m compileall -q "$FINAL_DIR/learnerbot" "$FINAL_DIR/scripts"
PYTHONPATH="$FINAL_DIR" "$FINAL_DIR/.venv/bin/python" -c "import learnerbot; print('learnerbot_import=PASS')"
SHA="$(git -C "$FINAL_DIR" rev-parse HEAD)"
printf 'prepared_at=%s\nsha=%s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$SHA" > "$MARKER"
chmod 0600 "$MARKER"
echo "GOOGLE_PRODUCTION_PATH_REFRESHED=true"
echo "sha=$SHA"
echo "size=$(du -sh "$FINAL_DIR" | awk '{print $1}')"
echo "files=$(find "$FINAL_DIR" -xdev -type f 2>/dev/null | wc -l)"
echo "service_active=$(systemctl is-active learnerbot.service 2>/dev/null || true)"
echo "trading_started=false"
EOF

cat >"$VERIFY" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
FINAL_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
MARKER="/var/lib/google-production-migration/prepared"
BACKUP_RECORD="/var/lib/google-production-migration/preexisting_backup_path"
[[ "$(hostname)" == "botgoogle" ]] || exit 31
[[ -f "$MARKER" && -d "$FINAL_DIR/.git" && -x "$FINAL_DIR/.venv/bin/python" ]] || exit 32
echo '=== GOOGLE PRODUCTION MIGRATION STATUS ==='
echo "final_dir=$FINAL_DIR"
echo "sha=$(git -C "$FINAL_DIR" rev-parse HEAD)"
echo "size=$(du -sh "$FINAL_DIR" | awk '{print $1}')"
echo "files=$(find "$FINAL_DIR" -xdev -type f 2>/dev/null | wc -l)"
echo "python=$($FINAL_DIR/.venv/bin/python --version 2>&1)"
PYTHONPATH="$FINAL_DIR" "$FINAL_DIR/.venv/bin/python" -c "import learnerbot; print('learnerbot_import=PASS')"
echo "service_active=$(systemctl is-active learnerbot.service 2>/dev/null || true)"
echo "service_enabled=$(systemctl is-enabled learnerbot.service 2>/dev/null || true)"
echo "preexisting_backup=$([[ -f "$BACKUP_RECORD" ]] && cat "$BACKUP_RECORD" || echo none)"
echo "claude_root_present=$([[ -d /home/ayman01323/ClaudeServer ]] && echo true || echo false)"
echo "trading_started=false"
EOF

chmod 0755 "$INSPECT" "$PREPARE" "$REFRESH" "$VERIFY"
chown root:root "$INSPECT" "$PREPARE" "$REFRESH" "$VERIFY"
cat >"$SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $INSPECT
$RUNNER_USER ALL=(root) NOPASSWD: $PREPARE
$RUNNER_USER ALL=(root) NOPASSWD: $REFRESH
$RUNNER_USER ALL=(root) NOPASSWD: $VERIFY
EOF
chmod 0440 "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

cat <<EOF
Installed hardened Google production migration bridge:
  $INSPECT
  $PREPARE
  $REFRESH
  $VERIFY
Final production path fixed to: $FINAL_DIR
Staging path fixed to: $STAGING_DIR
Migration state: $STATE_DIR (root-only)
Any pre-existing final directory will be preserved by rename before preparation.
Trading/service start permission: NOT GRANTED
Runner may inspect, prepare, refresh and verify only.
EOF
