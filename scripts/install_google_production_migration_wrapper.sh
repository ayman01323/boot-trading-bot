#!/usr/bin/env bash
set -Eeuo pipefail

STAGING_DIR="/home/ayman01323/NamecheapMigration/multichain-learning-bot-v2.2-fast-direct-market"
FINAL_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
RUNNER_USER="ayman01323"
SERVICE="learnerbot.service"
PREPARE="/usr/local/sbin/prepare-google-production-bot"
REFRESH="/usr/local/sbin/refresh-google-production-bot"
VERIFY="/usr/local/sbin/verify-google-production-bot"
SUDOERS="/etc/sudoers.d/google-production-migration"
LOCK="/var/lock/google-production-migration.lock"
MARKER="/var/tmp/google-production-migration.prepared"

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

cat >"$PREPARE" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
STAGING_DIR="/home/ayman01323/NamecheapMigration/multichain-learning-bot-v2.2-fast-direct-market"
FINAL_DIR="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
LOCK="/var/lock/google-production-migration.lock"
MARKER="/var/tmp/google-production-migration.prepared"
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
if [[ -e "$FINAL_DIR" && ! -f "$MARKER" ]]; then
  echo "Refusing: $FINAL_DIR already exists without migration marker" >&2
  exit 15
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
MARKER="/var/tmp/google-production-migration.prepared"
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
MARKER="/var/tmp/google-production-migration.prepared"
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
echo "claude_root_present=$([[ -d /home/ayman01323/ClaudeServer ]] && echo true || echo false)"
echo "trading_started=false"
EOF

chmod 0755 "$PREPARE" "$REFRESH" "$VERIFY"
chown root:root "$PREPARE" "$REFRESH" "$VERIFY"
cat >"$SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $PREPARE
$RUNNER_USER ALL=(root) NOPASSWD: $REFRESH
$RUNNER_USER ALL=(root) NOPASSWD: $VERIFY
EOF
chmod 0440 "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

cat <<EOF
Installed restricted Google production migration bridge:
  $PREPARE
  $REFRESH
  $VERIFY
Final production path fixed to: $FINAL_DIR
Staging path fixed to: $STAGING_DIR
Trading/service start permission: NOT GRANTED
Runner may prepare, refresh and verify only.
EOF
