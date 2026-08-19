from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

EXPECTED_AI_COMMANDS = {"aiaudit", "aidecision", "aistrategy", "aiupdates"}


def _git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, timeout=15, check=False)
    if p.returncode:
        raise RuntimeError((p.stderr or p.stdout or "git failed").strip()[:500])
    return p.stdout.strip()


def _status_ok(text: str) -> bool:
    low = str(text or "").lower()
    bad = ("inactive (dead)", "failed", "not running", "stopped")
    return bool(low.strip()) and not any(x in low for x in bad)


def build_attestation(repo: Path, expected_sha: str, status_log: Path | None = None) -> dict:
    repo = repo.resolve()
    deployed_sha = _git(repo, "rev-parse", "HEAD")
    status_text = ""
    if status_log and status_log.exists():
        status_text = status_log.read_text(encoding="utf-8", errors="replace")[-12000:]

    code_commands: list[str] = []
    telegram = {
        "attempted": False,
        "verified": False,
        "masters": [],
        "missing_by_master": {},
        "detail": "not attempted",
    }
    import_error = ""
    try:
        sys.path.insert(0, str(repo))
        from learnerbot import telegram_ai_ops_patch as ai_patch
        code_commands = sorted(str(x[0]) for x in ai_patch.AI_MASTER_COMMANDS)
        code_ok = EXPECTED_AI_COMMANDS.issubset(set(code_commands))
        try:
            from learnerbot.cli import _app
            from learnerbot.ai_ops_status import master_chat_ids
            from learnerbot import telegram as tg

            app = _app()
            token = str(getattr(app, "telegram_bot_token", "") or "").strip()
            masters = master_chat_ids(Path(app.csv_dir))
            telegram["masters"] = masters
            if token and masters:
                telegram["attempted"] = True
                # Re-apply the command registration idempotently, then verify the exact
                # chat-scoped Telegram command list returned by Telegram itself.
                ai_patch.set_commands(token)
                missing = {}
                for tid in masters:
                    scope = {"type": "chat", "chat_id": int(tid)}
                    rows = tg._json("getMyCommands", token, payload={"scope": scope}, timeout=15) or []
                    names = {str((r or {}).get("command") or "").strip().lower() for r in rows}
                    absent = sorted(EXPECTED_AI_COMMANDS - names)
                    if absent:
                        missing[str(tid)] = absent
                telegram["missing_by_master"] = missing
                telegram["verified"] = not missing
                telegram["detail"] = "Telegram getMyCommands verified" if not missing else "one or more MASTER command scopes are incomplete"
            else:
                telegram["detail"] = "bot token or ACTIVE MASTER list unavailable to attestation process"
        except Exception as exc:
            telegram["detail"] = f"{type(exc).__name__}: {str(exc)[:400]}"
    except Exception as exc:
        code_ok = False
        import_error = f"{type(exc).__name__}: {str(exc)[:500]}"

    return {
        "schema_version": 1,
        "generated_at": int(time.time()),
        "repo_path": str(repo),
        "expected_sha": str(expected_sha),
        "deployed_sha": deployed_sha,
        "sha_match": deployed_sha == str(expected_sha),
        "service_status_available": bool(status_text.strip()),
        "service_status_ok": _status_ok(status_text),
        "ai_ops_module_imported": not bool(import_error),
        "ai_ops_import_error": import_error,
        "ai_master_commands_in_code": code_commands,
        "ai_master_commands_code_ok": bool(code_ok),
        "telegram_command_verification": telegram,
        "deployment_attested": bool(deployed_sha == str(expected_sha) and _status_ok(status_text) and code_ok),
        "status_tail": status_text[-3000:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--expected-sha", required=True)
    ap.add_argument("--status-log")
    ap.add_argument("--output", required=True)
    ns = ap.parse_args()
    out = build_attestation(Path(ns.repo), ns.expected_sha, Path(ns.status_log) if ns.status_log else None)
    Path(ns.output).write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "deployment_attested": out["deployment_attested"],
        "sha_match": out["sha_match"],
        "service_status_ok": out["service_status_ok"],
        "ai_master_commands_code_ok": out["ai_master_commands_code_ok"],
        "telegram_commands_verified": out["telegram_command_verification"]["verified"],
    }, sort_keys=True))
    return 0 if out["deployment_attested"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
