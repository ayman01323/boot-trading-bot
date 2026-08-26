#!/usr/bin/env bash
set -Eeuo pipefail

RUNNER_USER="github-runner"
SOURCE="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
GOOGLE_USER="ayman01323"
GOOGLE_DEST="/home/ayman01323/NamecheapMigration/multichain-learning-bot-v2.2-fast-direct-market/"
STATE="/var/lib/namecheap-google-cutover"
HANDOFF="/var/tmp/namecheap-google-cutover-handoff"
QUIESCE="/usr/local/sbin/quiesce-namecheap-for-google-cutover"
TRANSFER="/usr/local/sbin/final-sync-namecheap-to-google"
VERIFY="/usr/local/sbin/verify-namecheap-cutover-stopped"
RESUME="/usr/local/sbin/resume-namecheap-after-cutover-failure"
SUDOERS="/etc/sudoers.d/github-runner-namecheap-google-cutover"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 2
fi
id "$RUNNER_USER" >/dev/null 2>&1 || { echo "Runner user missing: $RUNNER_USER" >&2; exit 3; }
[[ -d "$SOURCE/.git" && -x "$SOURCE/.venv/bin/python" ]] || { echo "Production source/venv missing" >&2; exit 4; }
command -v rsync >/dev/null 2>&1 || { echo 'rsync is required' >&2; exit 5; }
command -v ssh-keygen >/dev/null 2>&1 || { echo 'ssh-keygen is required' >&2; exit 6; }

install -d -o root -g root -m 0700 "$STATE"

cat >"$QUIESCE" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -eq 0 ]] || exit 2
SOURCE="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
STATE="/var/lib/namecheap-google-cutover"
HANDOFF="/var/tmp/namecheap-google-cutover-handoff"
RUNNER_USER="github-runner"
exec 9>/var/lock/namecheap-google-cutover.lock
flock -w 60 9 || { echo 'Cutover lock busy' >&2; exit 10; }
[[ -d "$SOURCE/.git" ]] || { echo 'Production source missing' >&2; exit 11; }
systemctl is-active --quiet "$SERVICE" || { echo 'Refusing: Namecheap learnerbot is not active before cutover' >&2; exit 12; }
rm -rf "$HANDOFF"
install -d -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0700 "$HANDOFF"
rm -f "$STATE/id_ed25519" "$STATE/id_ed25519.pub" "$STATE/known_hosts" "$STATE/quiesced"
ssh-keygen -q -t ed25519 -N '' -C 'namecheap-google-final-cutover' -f "$STATE/id_ed25519"
chmod 0600 "$STATE/id_ed25519"
install -o "$RUNNER_USER" -g "$RUNNER_USER" -m 0600 "$STATE/id_ed25519.pub" "$HANDOFF/public-key.txt"
SOURCE_IP="$(curl -4fsS --max-time 10 https://api.ipify.org || true)"
[[ "$SOURCE_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo 'Could not resolve Namecheap public IPv4' >&2; exit 13; }
printf '%s\n' "$SOURCE_IP" > "$HANDOFF/source-ip.txt"
: > "$HANDOFF/google-ip.txt"
chown "$RUNNER_USER:$RUNNER_USER" "$HANDOFF/source-ip.txt" "$HANDOFF/google-ip.txt"
chmod 0600 "$HANDOFF/source-ip.txt" "$HANDOFF/google-ip.txt"
SHA="$(git -C "$SOURCE" rev-parse HEAD)"
echo 'Stopping Namecheap learnerbot for final cutover...'
systemctl stop "$SERVICE"
for _ in $(seq 1 30); do
  systemctl is-active --quiet "$SERVICE" || break
  sleep 1
done
if systemctl is-active --quiet "$SERVICE"; then
  echo 'Failed to stop Namecheap learnerbot' >&2
  exit 14
fi
systemctl disable "$SERVICE" >/dev/null 2>&1 || true
printf 'quiesced_at=%s\nsha=%s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')" "$SHA" > "$STATE/quiesced"
chmod 0600 "$STATE/quiesced"
echo 'NAMECHEAP_QUIESCED=true'
echo "source_sha=$SHA"
echo 'service_active=false'
echo 'service_enabled=false'
EOF

cat >"$TRANSFER" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -eq 0 ]] || exit 2
SOURCE="/root/multichain-learning-bot-v2.2-fast-direct-market"
SERVICE="learnerbot.service"
STATE="/var/lib/namecheap-google-cutover"
HANDOFF="/var/tmp/namecheap-google-cutover-handoff"
GOOGLE_USER="ayman01323"
GOOGLE_DEST="/home/ayman01323/NamecheapMigration/multichain-learning-bot-v2.2-fast-direct-market/"
exec 9>/var/lock/namecheap-google-cutover.lock
flock -w 60 9 || { echo 'Cutover lock busy' >&2; exit 20; }
[[ -f "$STATE/quiesced" ]] || { echo 'Namecheap is not marked quiesced' >&2; exit 21; }
if systemctl is-active --quiet "$SERVICE"; then echo 'Refusing final sync: learnerbot is active' >&2; exit 22; fi
[[ -s "$STATE/id_ed25519" && -s "$HANDOFF/google-ip.txt" ]] || { echo 'Final-sync key/endpoint missing' >&2; exit 23; }
GOOGLE_IP="$(tr -d '[:space:]' < "$HANDOFF/google-ip.txt")"
[[ "$GOOGLE_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || { echo 'Invalid Google IPv4' >&2; exit 24; }
SSH="ssh -i $STATE/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$STATE/known_hosts -o ConnectTimeout=15"
$SSH "$GOOGLE_USER@$GOOGLE_IP" 'echo GOOGLE_FINAL_SYNC_SSH_READY'
rsync -aH --delete --partial --human-readable --no-owner --no-group --info=stats2 \
  -e "$SSH" "$SOURCE/" "$GOOGLE_USER@$GOOGLE_IP:$GOOGLE_DEST"
if systemctl is-active --quiet "$SERVICE"; then echo 'Namecheap unexpectedly became active' >&2; exit 25; fi
SHA="$(git -C "$SOURCE" rev-parse HEAD)"
FILES="$(find "$SOURCE" -xdev -type f 2>/dev/null | wc -l)"
echo 'NAMECHEAP_FINAL_SYNC_COMPLETE=true'
echo "source_sha=$SHA"
echo "source_files=$FILES"
echo 'namecheap_service_active=false'
EOF

cat >"$VERIFY" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -eq 0 ]] || exit 2
SERVICE="learnerbot.service"
STATE="/var/lib/namecheap-google-cutover"
[[ -f "$STATE/quiesced" ]] || exit 31
if systemctl is-active --quiet "$SERVICE"; then echo 'namecheap_service_active=true'; exit 32; fi
echo 'NAMECHEAP_CUTOVER_STOPPED=true'
echo 'namecheap_service_active=false'
echo "namecheap_service_enabled=$(systemctl is-enabled "$SERVICE" 2>/dev/null || true)"
EOF

cat >"$RESUME" <<'EOF'
#!/usr/bin/env bash
set -Eeuo pipefail
[[ $# -eq 0 ]] || exit 2
SERVICE="learnerbot.service"
STATE="/var/lib/namecheap-google-cutover"
[[ -f "$STATE/quiesced" ]] || { echo 'No cutover quiesce marker; refusing resume' >&2; exit 41; }
if systemctl is-active --quiet "$SERVICE"; then echo 'NAMECHEAP_ALREADY_ACTIVE=true'; exit 0; fi
systemctl enable "$SERVICE" >/dev/null 2>&1 || true
systemctl start "$SERVICE"
sleep 5
systemctl is-active --quiet "$SERVICE" || { echo 'Namecheap resume failed' >&2; exit 42; }
echo 'NAMECHEAP_ROLLBACK_RESUMED=true'
EOF

chmod 0755 "$QUIESCE" "$TRANSFER" "$VERIFY" "$RESUME"
chown root:root "$QUIESCE" "$TRANSFER" "$VERIFY" "$RESUME"
cat >"$SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $QUIESCE
$RUNNER_USER ALL=(root) NOPASSWD: $TRANSFER
$RUNNER_USER ALL=(root) NOPASSWD: $VERIFY
$RUNNER_USER ALL=(root) NOPASSWD: $RESUME
EOF
chmod 0440 "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

echo 'Installed Namecheap final-cutover bridge:'
echo "  $QUIESCE"
echo "  $TRANSFER"
echo "  $VERIFY"
echo "  $RESUME"
echo 'No service was stopped by this installer.'
