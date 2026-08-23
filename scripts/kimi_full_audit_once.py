from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

ROOT = pathlib.Path.cwd()
OUT = ROOT / ".kimi_audit"
OUT.mkdir(exist_ok=True)
REPLIES = OUT / "replies"
REPLIES.mkdir(exist_ok=True)
RUN_ID = os.environ.get("GITHUB_RUN_ID", "local")
PY = sys.executable


def _exchange(*args: str, timeout: int = 150) -> dict[str, Any]:
    cmd = [PY, "scripts/ai_agent_ws_send.py", *args]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    lines = [x for x in proc.stdout.splitlines() if x.strip()]
    final: dict[str, Any] = {}
    if lines:
        try:
            final = json.loads(lines[-1])
        except Exception:
            final = {"status": "PARSE_ERROR", "body": lines[-1][-4000:]}
    final["_returncode"] = proc.returncode
    final["_stderr"] = proc.stderr[-1200:]
    return final


def ask_kimi(message: str, tag: str, timeout: int = 150) -> str:
    result = _exchange(
        "--from", "gpt", "--to", "kimi",
        "--message", message,
        "--subject", f"Kimi read-only audit {RUN_ID} {tag}",
        "--message-id", f"kimi-audit-{RUN_ID}-{tag}",
        "--timeout", str(max(30, timeout - 15)),
        timeout=timeout,
    )
    body = str(result.get("body") or "").strip()
    if not body:
        return f"FAILED[{tag}] status={result.get('status')} rc={result.get('_returncode')} error={result.get('error') or result.get('_stderr')}"
    return body


def task(action: str, args: dict[str, Any], tag: str) -> dict[str, Any]:
    result = _exchange(
        "--from", "gpt", "--to", "kimi",
        "--task-action", action,
        "--task-args-json", json.dumps(args, separators=(",", ":")),
        "--task-instruction", "Read-only diagnosis of why trading stopped. Do not modify anything.",
        "--message-id", f"kimi-task-{RUN_ID}-{tag}",
        "--timeout", "90",
        timeout=110,
    )
    body = result.get("body")
    if isinstance(body, str) and body.strip().startswith("{"):
        try:
            result["task_result"] = json.loads(body)
        except Exception:
            pass
    return result


def redact(text: str) -> str:
    text = re.sub(
        r"(?i)(api[_ -]?key|private[_ -]?key|mnemonic|seed phrase|password|authorization|bearer|secret|token)\s*[:=]\s*\S+",
        r"\1=<redacted>", text,
    )
    text = re.sub(r"(?i)(sk|ghp|github_pat)_[A-Za-z0-9_-]{8,}", "<redacted>", text)
    return text


def collect_runtime() -> str:
    status = (OUT / "server_status.txt").read_text(encoding="utf-8", errors="replace") if (OUT / "server_status.txt").exists() else ""
    test_status = (OUT / "test_status.txt").read_text(encoding="utf-8", errors="replace") if (OUT / "test_status.txt").exists() else ""
    pytest_tail = (OUT / "pytest_tail.txt").read_text(encoding="utf-8", errors="replace") if (OUT / "pytest_tail.txt").exists() else ""
    tasks: list[dict[str, Any]] = []
    for action, args, tag in [
        ("GIT_STATUS", {}, "git-status"),
        ("GIT_DIFF", {}, "git-diff"),
        ("LIST_FILES", {"path": "data"}, "list-data"),
        ("LIST_FILES", {"path": "CSVbot"}, "list-csvbot"),
    ]:
        try:
            tasks.append(task(action, args, tag))
        except Exception as exc:
            tasks.append({"action": action, "error": repr(exc)})

    candidates: list[str] = []
    pattern = re.compile(r"(?i)(trade|decision|signal|position|order|execution|profit|loss|live|state|health|metric|history|ledger|journal|shadow|block|reason|candidate)")
    for item in tasks:
        ev = ((item.get("task_result") or {}).get("evidence") or {}) if isinstance(item, dict) else {}
        for path in ev.get("files") or []:
            if isinstance(path, str) and pattern.search(path) and not re.search(r"(?i)(secret|credential|private|mnemonic|seed|\.env)", path):
                candidates.append(path)
    for idx, path in enumerate(sorted(set(candidates), reverse=True)[:10], 1):
        try:
            tasks.append(task("READ_FILE", {"path": path}, f"read-{idx}"))
        except Exception as exc:
            tasks.append({"action": "READ_FILE", "path": path, "error": repr(exc)})

    raw_tasks = redact(json.dumps(tasks, ensure_ascii=False))[:12000]
    context = (
        "=== FRESH SERVER STATUS ===\n" + redact(status[:7000])
        + "\n\n=== TEST STATUS ===\n" + test_status[:1000]
        + "\n" + pytest_tail[-4000:]
        + "\n\n=== BOUNDED PRODUCTION TASKS ===\n" + raw_tasks
    )[:28500]
    diagnosis = ask_kimi(
        "You are Kimi. Diagnose why this production multi-chain bot stopped trading after it previously traded. "
        "Use only the fresh read-only evidence below. Separate confirmed facts from hypotheses; rank root causes by confidence; "
        "identify safe fixes/checks. Do not recommend disabling risk, liquidity, sellability, simulation, wallet/signing or capital controls just to force trades. "
        "Return <=3500 characters.\n\n" + context,
        "runtime",
        timeout=180,
    )
    (OUT / "runtime_diagnosis.txt").write_text(diagnosis, encoding="utf-8")
    return diagnosis


def source_files() -> list[str]:
    tracked = subprocess.check_output(["git", "ls-files"], text=True).splitlines()
    out: list[str] = []
    exts = {".py", ".sh", ".toml", ".ini", ".cfg", ".json", ".yaml", ".yml"}
    for name in tracked:
        p = pathlib.Path(name)
        if p.suffix.lower() not in exts and name not in {"requirements.txt", "pyproject.toml"}:
            continue
        if name.startswith(("tests/", ".github/", "docs/", "data/", "CSVbot/", ".git/")):
            continue
        include = name.startswith("learnerbot/") or len(p.parts) == 1 or name.startswith(("strategy/", "config/"))
        if name.startswith("scripts/"):
            low = name.lower()
            include = any(k in low for k in (
                "trade", "bot", "strategy", "execution", "wallet", "runtime", "health", "monitor", "forensic",
                "solana", "evm", "polygon", "arbit", "copy", "signal", "position", "loss", "profit", "server", "rpc",
            ))
        if include and p.is_file():
            out.append(name)
    return sorted(set(out), key=lambda x: (0 if x.startswith("learnerbot/") else 1, x))


def build_chunks(files: list[str], max_chars: int = 17500) -> tuple[list[str], dict[str, Any]]:
    chunks: list[str] = []
    current = ""
    manifest: list[dict[str, Any]] = []
    for name in files:
        text = pathlib.Path(name).read_text(encoding="utf-8", errors="replace")
        manifest.append({"path": name, "chars": len(text)})
        block = f"\n===== FILE: {name} =====\n{text}\n"
        while block:
            room = max_chars - len(current)
            if room < 800:
                chunks.append(current)
                current = ""
                room = max_chars
            take = block[:room]
            current += take
            block = block[room:]
            if len(current) >= max_chars:
                chunks.append(current)
                current = ""
                if block:
                    block = "\n===== CONTINUED =====\n" + block
        
    if current:
        chunks.append(current)
    return chunks, {"file_count": len(manifest), "chunk_count": len(chunks), "total_chars": sum(x["chars"] for x in manifest), "files": manifest}


def audit_source() -> tuple[str, dict[str, Any]]:
    files = source_files()
    chunks, manifest = build_chunks(files)
    (OUT / "source_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    findings: list[str] = []
    main_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    for idx, chunk in enumerate(chunks, 1):
        prompt = (
            f"You are Kimi doing a read-only code audit of trading bot main SHA {main_sha}. Source chunk {idx}/{len(chunks)}. "
            "Find concrete bugs/regressions/races/state errors/bad defaults/exceptions/gates that can suppress or break LIVE trading after it previously worked, plus correctness/security bugs. "
            "Do not suggest bypassing safety/liquidity/simulation/sellability protections to force trades. Return only high-signal findings <=1100 characters, citing file/function where possible. "
            "If none, say NO_CONCRETE_BUG.\n\n" + chunk
        )
        body = ask_kimi(prompt, f"code-{idx}", timeout=160)
        body = body[:1400]
        findings.append(f"[{idx}/{len(chunks)}] {body}")
        print(f"KIMI_CHUNK {idx}/{len(chunks)} {body[:160].replace(chr(10), ' ')}", flush=True)

    groups: list[str] = []
    cur = ""
    for item in findings:
        if cur and len(cur) + len(item) + 2 > 16000:
            groups.append(cur)
            cur = ""
        cur += item + "\n\n"
    if cur:
        groups.append(cur)

    consolidated: list[str] = []
    for idx, group in enumerate(groups, 1):
        summary = ask_kimi(
            "You are Kimi consolidating your own code-audit findings. Remove duplicates and weak speculation. Preserve concrete file/function references and bugs relevant to stopped trading. "
            "Rank by severity/confidence and return <=2600 characters.\n\n" + group,
            f"consolidate-{idx}",
            timeout=180,
        )
        consolidated.append(f"=== CODE FINDINGS {idx}/{len(groups)} ===\n{summary[:3000]}")
    text = "\n\n".join(consolidated)
    (OUT / "code_findings.txt").write_text(text, encoding="utf-8")
    return text, manifest


def final_report(code: str, runtime: str, manifest: dict[str, Any]) -> str:
    main_sha = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    status = (OUT / "server_status.txt").read_text(encoding="utf-8", errors="replace") if (OUT / "server_status.txt").exists() else ""
    match = re.search(r"^sha:\s*([0-9a-f]{40})", status, re.M)
    server_sha = match.group(1) if match else "unknown"
    tests = (OUT / "test_status.txt").read_text(encoding="utf-8", errors="replace") if (OUT / "test_status.txt").exists() else ""
    prompt = f"""You are Kimi. Produce the FINAL full read-only audit requested by the owner.

Objectives: explain why the bot stopped trading after it previously traded; identify concrete code bugs/regressions; separate CONFIRMED causes from HIGH/MEDIUM/LOW-confidence hypotheses; give a safe ordered fix plan with exact files/functions/settings/checks; state verification required before any LIVE restart/change. Never recommend weakening safety, sellability, liquidity, simulation, wallet/signing, risk or capital controls merely to force trades. Mention deployed-vs-main mismatch if relevant. Do not invent evidence.

Current main SHA: {main_sha}
Production server SHA: {server_sha}
Audit coverage: {json.dumps({k: manifest[k] for k in ('file_count','chunk_count','total_chars')})}
Test status: {tests[:1000]}

YOUR CONSOLIDATED CODE FINDINGS:
{code[:16500]}

YOUR FRESH RUNTIME DIAGNOSIS:
{runtime[:5000]}

FRESH SERVER STATUS:
{redact(status[:3500])}

Use headings: Executive conclusion; Confirmed findings; Why trading stopped; Code bugs; Fix plan; Verification plan; Remaining unknowns. Be substantial and actionable."""
    if len(prompt) > 31500:
        prompt = prompt[:31500]
    return ask_kimi(prompt, "final", timeout=210)


def main() -> int:
    runtime = collect_runtime()
    code, manifest = audit_source()
    report = final_report(code, runtime, manifest)
    (OUT / "final_reply.txt").write_text(report, encoding="utf-8")
    print("KIMI_FINAL_REPLY_BEGIN")
    print(report)
    print("KIMI_FINAL_REPLY_END")
    return 0 if report and not report.startswith("FAILED[") else 1


if __name__ == "__main__":
    raise SystemExit(main())
