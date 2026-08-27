#!/usr/bin/env bash
set -euo pipefail

TARGET="/home/ayman01323/BOOT/testingbots/grok_known_assets_bot"
SOURCE="${1:-$(pwd)}"

mkdir -p "$TARGET"
rsync -a --delete --exclude '.venv/' --exclude '.env' --exclude 'state.sqlite3' "$SOURCE/" "$TARGET/"
cd "$TARGET"
cp -n config.example.json config.json
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -q -e '.[test]'
.venv/bin/pytest -q
.venv/bin/grok-known-assets-bot --config config.json --db state.sqlite3 check

echo "deployed=$TARGET"
echo "mode=PAPER_ONLY"
