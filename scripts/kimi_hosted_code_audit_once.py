from __future__ import annotations

import json
import os
import pathlib
import subprocess
import time
import urllib.request

ROOT = pathlib.Path(os.environ.get("AUDIT_TARGET", ".")).resolve()
OUT = ROOT / ".kimi_hosted"
OUT.mkdir(exist_ok=True)
KEY = os.environ["KIMI_API_KEY"]
MODEL = os.environ.get("KIMI_COUNCIL_MODEL", "kimi-k2.6")


def call_kimi(prompt: str, max_tokens: int = 1800) -> str:
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if MODEL.startswith(("kimi-k2.6", "kimi-k2.5")):
        payload["thinking"] = {"type": "enabled"}
    data = json.dumps(payload).encode("utf-8")
    last_error = ""
    for attempt in range(4):
        req = urllib.request.Request(
            "https://api.moonshot.ai/v1/chat/completions",
            data=data,
            headers={
                "Authorization": "Bearer " + KEY,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                body = json.loads(response.read().decode("utf-8", "replace"))
            text = body["choices"][0]["message"]["content"]
            if isinstance(text, list):
                text = "\n".join(
                    str(part.get("text") or "")
                    for part in text
                    if isinstance(part, dict)
                )
            result = str(text or "").strip()
            if result:
                return result
            last_error = "empty Kimi response"
        except Exception as exc:
            last_error = repr(exc)
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(last_error)


def source_files() -> list[str]:
    tracked = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files"], text=True
    ).splitlines()
    exts = {".py", ".sh", ".toml", ".ini", ".cfg", ".json", ".yaml", ".yml"}
    files: list[str] = []
    for name in tracked:
        path = ROOT / name
        suffix = pathlib.Path(name).suffix.lower()
        if suffix not in exts and name not in {"requirements.txt", "pyproject.toml"}:
            continue
        if name.startswith(("tests/", ".github/", "docs/", "data/", "CSVbot/", ".git/")):
            continue
        include = (
            name.startswith("learnerbot/")
            or len(pathlib.Path(name).parts) == 1
            or name.startswith(("strategy/", "config/"))
        )
        if name.startswith("scripts/"):
            low = name.lower()
            include = any(
                token in low
                for token in (
                    "trade", "bot", "strategy", "execution", "wallet", "runtime",
                    "health", "monitor", "forensic", "solana", "evm", "polygon",
                    "arbit", "copy", "signal", "position", "loss", "profit", "server", "rpc",
                )
            )
        if include and path.is_file():
            files.append(name)
    return sorted(set(files), key=lambda x: (0 if x.startswith("learnerbot/") else 1, x))


def build_chunks(files: list[str], max_chars: int = 50000):
    chunks: list[str] = []
    current = ""
    manifest: list[dict[str, object]] = []
    for name in files:
        text = (ROOT / name).read_text(encoding="utf-8", errors="replace")
        manifest.append({"path": name, "chars": len(text)})
        block = "\n===== FILE: " + name + " =====\n" + text + "\n"
        while block:
            room = max_chars - len(current)
            if room < 1000:
                chunks.append(current)
                current = ""
                room = max_chars
            current += block[:room]
            block = block[room:]
            if len(current) >= max_chars:
                chunks.append(current)
                current = ""
                if block:
                    block = "\n===== CONTINUED =====\n" + block
    if current:
        chunks.append(current)
    meta = {
        "file_count": len(manifest),
        "chunk_count": len(chunks),
        "total_chars": sum(int(row["chars"]) for row in manifest),
        "files": manifest,
    }
    return chunks, meta


def main() -> int:
    files = source_files()
    chunks, meta = build_chunks(files)
    (OUT / "manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    sha = subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()
    findings: list[str] = []
    for index, chunk in enumerate(chunks, 1):
        prompt = (
            "You are Kimi performing a rigorous read-only audit of a multi-chain automated trading bot. "
            f"Current main SHA is {sha}. This is source chunk {index}/{len(chunks)}. "
            "Find concrete bugs and regressions: exceptions, stale state, races, bad defaults, gating mistakes, "
            "provider/RPC/execution failures, chain-specific errors, persistence bugs, and logic that can make LIVE trading stop after it previously worked. "
            "Also flag material security/correctness defects. Never recommend weakening risk, liquidity, sellability, simulation, wallet/signing or capital controls merely to force trades. "
            "Return high-signal findings only, no more than 1700 characters, citing file/function references. If there is no concrete defect in this chunk, say NO_CONCRETE_BUG.\n\n"
            + chunk
        )
        answer = call_kimi(prompt, 1700)[:2100]
        findings.append(f"[{index}/{len(chunks)}] {answer}")
        preview = answer[:180].replace("\n", " ")
        print(f"KIMI_CODE_CHUNK {index}/{len(chunks)} {preview}", flush=True)

    groups: list[str] = []
    current = ""
    for item in findings:
        if current and len(current) + len(item) + 2 > 28000:
            groups.append(current)
            current = ""
        current += item + "\n\n"
    if current:
        groups.append(current)

    summaries: list[str] = []
    for index, group in enumerate(groups, 1):
        answer = call_kimi(
            "You are Kimi consolidating your own code-audit findings. Remove duplicates and unsupported speculation. "
            "Rank concrete defects by severity and confidence, preserve file/function references, and emphasize anything capable of stopping trading after it previously worked. "
            "Return no more than 4500 characters.\n\n" + group,
            2200,
        )
        summaries.append(f"=== KIMI CODE AUDIT GROUP {index}/{len(groups)} ===\n{answer}")

    consolidated = "\n\n".join(summaries)
    (OUT / "code_findings.txt").write_text(consolidated, encoding="utf-8")
    test_status = (OUT / "test_status.txt").read_text(encoding="utf-8", errors="replace") if (OUT / "test_status.txt").exists() else "not run"
    pytest_tail = (OUT / "pytest_tail.txt").read_text(encoding="utf-8", errors="replace") if (OUT / "pytest_tail.txt").exists() else ""
    coverage = {key: meta[key] for key in ("file_count", "chunk_count", "total_chars")}
    final_prompt = (
        "You are Kimi. Produce the FINAL code-audit report requested by the owner of this trading bot.\n"
        f"Current main SHA: {sha}\n"
        f"Coverage: {json.dumps(coverage)}\n"
        f"Test evidence:\n{test_status[:1000]}\n{pytest_tail[-4000:]}\n\n"
        f"Your consolidated code findings:\n{consolidated[:24000]}\n\n"
        "Explain which concrete bugs could stop trading after it previously worked. Separate CONFIRMED CODE DEFECTS from hypotheses that require runtime evidence. "
        "Give an ordered safe fix plan with exact files/functions and a verification plan. Do not weaken safety/risk/liquidity/simulation/sellability/wallet/signing controls to force trades. "
        "Use headings: Executive code conclusion; Confirmed code defects; Stopped-trading candidates; Fix plan; Tests/verification; Runtime evidence still required."
    )
    final = call_kimi(final_prompt[:50000], 2400)
    (OUT / "final_code_audit.txt").write_text(final, encoding="utf-8")
    print("KIMI_HOSTED_FINAL_BEGIN")
    print(final)
    print("KIMI_HOSTED_FINAL_END")
    print("KIMI_COVERAGE=" + json.dumps(coverage))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
