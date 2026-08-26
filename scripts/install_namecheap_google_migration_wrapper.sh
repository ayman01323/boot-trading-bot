#!/usr/bin/env bash
set -Eeuo pipefail

# One-time root installer for a narrowly scoped live Namecheap -> Google migration.
# The GitHub runner receives two no-argument commands only:
#   1) prepare-namecheap-google-migration
#   2) transfer-namecheap-google-migration
# Both are fixed to the production bot source and the Google staging path.

RUNNER_USER="${RUNNER_USER:-github-runner}"
SOURCE="${SOURCE:-/root/multichain-learning-bot-v2.2-fast-direct-market}"
SERVICE="${SERVICE:-learnerbot.service}"
GOOGLE_USER="ayman01323"
GOOGLE_DEST="/home/ayman01323/NamecheapMigration/multichain-learning-bot-v2.2-fast-direct-market/"
STATE="/var/tmp/namecheap-google-migration"
PREPARE="/usr/local/sbin/prepare-namecheap-google-migration"
TRANSFER="/usr/local/sbin/transfer-namecheap-google-migration"
SUDOERS="/etc/sudoers.d/github-runner-namecheap-google-migration"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 2
fi

id "$RUNNER_USER" >/dev/null 2>&1 || { echo "Runner user not found: $RUNNER_USER" >&2; exit 3; }
test -d "$SOURCE" || { echo "Source not found: $SOURCE" >&2; exit 4; }
command -v rsync >/dev/null 2>&1 || { echo 'rsync is required' >&2; exit 5; }
command -v ssh >/dev/null 2>&1 || { echo 'ssh is required' >&2; exit 6; }
command -v ssh-keygen >/dev/null 2>&1 || { echo 'ssh-keygen is required' >&2; exit 7; }

TMP_PREPARE="$(mktemp)"
TMP_TRANSFER="$(mktemp)"
TMP_SUDOERS="$(mktemp)"
trap 'rm -f "$TMP_PREPARE" "$TMP_TRANSFER" "$TMP_SUDOERS"' EXIT

cat >"$TMP_PREPARE" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ \$# -ne 0 ]]; then echo 'This command accepts no arguments.' >&2; exit 2; fi
SOURCE='$SOURCE'
SERVICE='$SERVICE'
STATE='$STATE'
RUNNER_USER='$RUNNER_USER'

systemctl is-active --quiet "\$SERVICE" || { echo 'learnerbot is not active'; exit 10; }
test -d "\$SOURCE" || { echo 'fixed migration source missing'; exit 11; }
rm -rf "\$STATE"
install -d -o root -g root -m 0700 "\$STATE"
ssh-keygen -q -t ed25519 -N '' -C 'namecheap-google-live-migration' -f "\$STATE/id_ed25519"
chmod 0600 "\$STATE/id_ed25519"
install -o "\$RUNNER_USER" -g "\$RUNNER_USER" -m 0600 "\$STATE/id_ed25519.pub" "\$STATE/public-key.txt"
SOURCE_IP="\$(curl -4fsS --max-time 10 https://api.ipify.org || true)"
test -n "\$SOURCE_IP" || { echo 'Could not determine Namecheap public IP'; exit 12; }
printf '%s\n' "\$SOURCE_IP" > "\$STATE/source-ip.txt"
chown "\$RUNNER_USER:\$RUNNER_USER" "\$STATE/source-ip.txt"
chmod 0600 "\$STATE/source-ip.txt"

SIZE="\$(du -sh "\$SOURCE" | awk '{print \$1}')"
FILES="\$(find "\$SOURCE" -xdev -type f | wc -l)"
echo 'MIGRATION_PREPARED=true'
echo "source_size=\$SIZE"
echo "source_files=\$FILES"
echo 'learnerbot_active=true'
EOF

cat >"$TMP_TRANSFER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
if [[ \$# -ne 0 ]]; then echo 'This command accepts no arguments.' >&2; exit 2; fi
SOURCE='$SOURCE'
SERVICE='$SERVICE'
STATE='$STATE'
GOOGLE_USER='$GOOGLE_USER'
GOOGLE_DEST='$GOOGLE_DEST'

systemctl is-active --quiet "\$SERVICE" || { echo 'learnerbot is not active'; exit 20; }
test -s "\$STATE/id_ed25519" || { echo 'one-time migration private key missing'; exit 21; }
test -s "\$STATE/google-ip.txt" || { echo 'Google endpoint missing'; exit 22; }
GOOGLE_IP="\$(tr -d '[:space:]' < "\$STATE/google-ip.txt")"
if [[ ! "\$GOOGLE_IP" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
  echo 'Invalid Google IPv4 endpoint' >&2
  exit 23
fi
IFS=. read -r a b c d <<<"\$GOOGLE_IP"
for octet in "\$a" "\$b" "\$c" "\$d"; do
  (( octet >= 0 && octet <= 255 )) || { echo 'Invalid Google IPv4 endpoint' >&2; exit 23; }
done

SSH="ssh -i \$STATE/id_ed25519 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=\$STATE/known_hosts -o ConnectTimeout=15"
\$SSH "\$GOOGLE_USER@\$GOOGLE_IP" 'echo GOOGLE_SSH_READY'

# Live first pass: production remains running. Mutable databases/state are reconciled
# by a final short delta pass during cutover.
rsync -aH --partial --human-readable \
  --no-owner --no-group \
  --info=stats2,progress2 \
  -e "\$SSH" \
  "\$SOURCE/" \
  "\$GOOGLE_USER@\$GOOGLE_IP:\$GOOGLE_DEST"

systemctl is-active --quiet "\$SERVICE" || { echo 'learnerbot stopped unexpectedly'; exit 24; }
echo 'FIRST_PASS_RSYNC_COMPLETE=true'
echo 'learnerbot_remained_active=true'
EOF

cat >"$TMP_SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $PREPARE
$RUNNER_USER ALL=(root) NOPASSWD: $TRANSFER
EOF

chmod 0755 "$TMP_PREPARE" "$TMP_TRANSFER"
chmod 0440 "$TMP_SUDOERS"
visudo -cf "$TMP_SUDOERS" >/dev/null
install -o root -g root -m 0755 "$TMP_PREPARE" "$PREPARE"
install -o root -g root -m 0755 "$TMP_TRANSFER" "$TRANSFER"
install -o root -g root -m 0440 "$TMP_SUDOERS" "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

printf 'Installed restricted live migration wrappers:\n'
printf '  %s\n' "$PREPARE"
printf '  %s\n' "$TRANSFER"
printf 'Source fixed to: %s\n' "$SOURCE"
printf 'Google staging fixed to: %s\n' "$GOOGLE_DEST"
printf 'learnerbot is not stopped by either wrapper.\n'
