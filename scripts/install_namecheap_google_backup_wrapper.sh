#!/usr/bin/env bash
set -Eeuo pipefail

# One-time root installer for the narrowly scoped Namecheap -> Google backup exporter.
# Security model:
# - github-runner receives exactly one no-argument sudo command;
# - source path and service name are hard-coded by this root installer;
# - the runner may provide only an RSA public key at a fixed /var/tmp path;
# - the root wrapper creates the plaintext archive in a root-only temp directory;
# - learnerbot is stopped only while the point-in-time tar.gz is created and is
#   restarted before encryption/upload work continues;
# - only AES-encrypted archive material and non-secret checksum metadata are
#   handed to github-runner;
# - plaintext archive and AES passphrase are deleted before the wrapper returns.

RUNNER_USER="${RUNNER_USER:-github-runner}"
SOURCE="${SOURCE:-/root/multichain-learning-bot-v2.2-fast-direct-market}"
SERVICE="${SERVICE:-learnerbot.service}"
WRAPPER="/usr/local/sbin/export-namecheap-google-backup"
SUDOERS="/etc/sudoers.d/github-runner-namecheap-google-backup"
PUBLIC_KEY="/var/tmp/namecheap-google-backup-public.pem"
HANDOFF="/var/tmp/namecheap-google-backup-export"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root: sudo bash $0" >&2
  exit 2
fi

id "$RUNNER_USER" >/dev/null 2>&1 || { echo "Runner user not found: $RUNNER_USER" >&2; exit 3; }
test -d "$SOURCE" || { echo "Backup source not found: $SOURCE" >&2; exit 4; }
command -v openssl >/dev/null 2>&1 || { echo "openssl is required" >&2; exit 5; }
command -v tar >/dev/null 2>&1 || { echo "tar is required" >&2; exit 6; }

TMP_WRAPPER="$(mktemp)"
TMP_SUDOERS="$(mktemp)"
trap 'rm -f "$TMP_WRAPPER" "$TMP_SUDOERS"' EXIT

cat >"$TMP_WRAPPER" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail

if [[ \$# -ne 0 ]]; then
  echo 'This command accepts no arguments.' >&2
  exit 2
fi

SOURCE='$SOURCE'
SERVICE='$SERVICE'
RUNNER_USER='$RUNNER_USER'
PUBLIC_KEY='$PUBLIC_KEY'
HANDOFF='$HANDOFF'
LOCK='/var/lock/namecheap-google-backup.lock'

exec 9>"\$LOCK"
flock -w 30 9 || { echo 'Another Namecheap backup export is already running.' >&2; exit 3; }

# The unprivileged runner may supply only this public key. Validate that it is a
# parseable public key before touching the production service.
test -s "\$PUBLIC_KEY" || { echo 'Google transfer public key is missing.' >&2; exit 4; }
openssl pkey -pubin -in "\$PUBLIC_KEY" -noout >/dev/null 2>&1 || {
  echo 'Google transfer public key is invalid.' >&2
  exit 5
}

test -d "\$SOURCE" || { echo 'Fixed backup source is missing.' >&2; exit 6; }
systemctl is-active --quiet "\$SERVICE" || { echo 'learnerbot is not active before backup.' >&2; exit 7; }

ROOT_TMP="\$(mktemp -d /var/tmp/.namecheap-google-backup-root.XXXXXX)"
STOPPED=0
cleanup() {
  rc=\$?
  if [[ "\$STOPPED" -eq 1 ]]; then
    systemctl start "\$SERVICE" || true
  fi
  rm -rf "\$ROOT_TMP"
  if [[ \$rc -ne 0 ]]; then
    rm -rf "\$HANDOFF"
  fi
  exit "\$rc"
}
trap cleanup EXIT

STAMP="\$(date -u +%Y-%m-%d_%H-%M-%SZ)"
NAME="namecheap-old-server-\${STAMP}.tar.gz"
ARCHIVE="\$ROOT_TMP/\$NAME"
ENC="\$ROOT_TMP/\$NAME.enc"
PASS="\$ROOT_TMP/aes.pass"
PASS_ENC="\$ROOT_TMP/aes.pass.enc"
META="\$ROOT_TMP/backup-metadata.env"

rm -rf "\$HANDOFF"
install -d -m 0700 -o root -g root "\$HANDOFF"

printf 'Creating consistent old-server archive...\n'
systemctl stop "\$SERVICE"
STOPPED=1

tar -C /root -czf "\$ARCHIVE" multichain-learning-bot-v2.2-fast-direct-market

printf 'Restarting learnerbot immediately after archive creation...\n'
systemctl start "\$SERVICE"
STOPPED=0
sleep 2
systemctl is-active --quiet "\$SERVICE" || { echo 'learnerbot failed to restart after backup archive.' >&2; exit 8; }

SIZE="\$(stat -c%s "\$ARCHIVE")"
HASH="\$(sha256sum "\$ARCHIVE" | awk '{print \$1}')"
tar -tzf "\$ARCHIVE" >/dev/null

openssl rand -base64 48 > "\$PASS"
chmod 0600 "\$PASS"
openssl enc -aes-256-cbc -salt -pbkdf2 -iter 200000 \
  -in "\$ARCHIVE" -out "\$ENC" -pass file:"\$PASS"
openssl pkeyutl -encrypt -pubin -inkey "\$PUBLIC_KEY" \
  -in "\$PASS" -out "\$PASS_ENC" \
  -pkeyopt rsa_padding_mode:oaep -pkeyopt rsa_oaep_md:sha256

printf 'BACKUP_NAME=%q\nBACKUP_SIZE=%q\nBACKUP_SHA256=%q\nSOURCE_HOST=%q\nCREATED_UTC=%q\n' \
  "\$NAME" "\$SIZE" "\$HASH" "\$(hostname)" "\$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "\$META"

# The runner gets encrypted payload + checksum metadata only. Never hand it the
# plaintext tar.gz or AES passphrase.
install -o "\$RUNNER_USER" -g "\$RUNNER_USER" -m 0600 "\$ENC" "\$HANDOFF/\$NAME.enc"
install -o "\$RUNNER_USER" -g "\$RUNNER_USER" -m 0600 "\$PASS_ENC" "\$HANDOFF/aes.pass.enc"
install -o "\$RUNNER_USER" -g "\$RUNNER_USER" -m 0600 "\$META" "\$HANDOFF/backup-metadata.env"
chown "\$RUNNER_USER:\$RUNNER_USER" "\$HANDOFF"
chmod 0700 "\$HANDOFF"

# Destroy plaintext and passphrase before returning control to the runner.
rm -f "\$ARCHIVE" "\$PASS" "\$ENC" "\$PASS_ENC" "\$META"

printf 'BACKUP_EXPORT_READY=true\n'
printf 'backup_name=%s\n' "\$NAME"
printf 'plaintext_size_bytes=%s\n' "\$SIZE"
printf 'learnerbot_active=true\n'
EOF

cat >"$TMP_SUDOERS" <<EOF
$RUNNER_USER ALL=(root) NOPASSWD: $WRAPPER
EOF

chmod 0755 "$TMP_WRAPPER"
chmod 0440 "$TMP_SUDOERS"
visudo -cf "$TMP_SUDOERS" >/dev/null
install -o root -g root -m 0755 "$TMP_WRAPPER" "$WRAPPER"
install -o root -g root -m 0440 "$TMP_SUDOERS" "$SUDOERS"
visudo -cf "$SUDOERS" >/dev/null

printf 'Installed restricted backup wrapper: %s\n' "$WRAPPER"
printf 'Granted %s exactly one no-argument sudo command.\n' "$RUNNER_USER"
printf 'Source: %s\n' "$SOURCE"
printf 'Handoff: %s\n' "$HANDOFF"
printf 'Test permission: sudo -u %s sudo -n %s\n' "$RUNNER_USER" "$WRAPPER"
