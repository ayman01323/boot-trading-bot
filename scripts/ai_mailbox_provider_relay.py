from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
import types
from pathlib import Path

# This relay is deliberately communication-only and runs in a tiny isolated
# environment. Importing learnerbot normally executes learnerbot/__init__.py,
# which bootstraps trading/runtime patches (including Web3 dependencies) that
# the relay neither needs nor should load. Create only the package namespace so
# the provider/cost modules can use their normal relative imports without
# executing the trading runtime package initialiser.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LEARNERBOT_DIR = _REPO_ROOT / "learnerbot"
if "learnerbot" not in sys.modules:
    package = types.ModuleType("learnerbot")
    package.__path__ = [str(_LEARNERBOT_DIR)]
    package.__package__ = "learnerbot"
    package.__file__ = str(_LEARNERBOT_DIR / "__init__.py")
    sys.modules["learnerbot"] = package

from learnerbot.ai_cost_provider_patch import call_provider

_ALLOWED_PROVIDERS = {"deepseek", "gemini", "grok", "kimi", "copilot"}
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_SECRET_ENV_KEYS = (
    "DEEPSEEK_API_KEY",
    "GEMINI_API_KEY",
    "XAI_API_KEY",
    "KIMI_API_KEY",
    "MOONSHOT_API_KEY",
    "COPILOT_ASSIGN_TOKEN",
    "COPILOT_GITHUB_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"xai-[A-Za-z0-9_-]{12,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]+"),
)


def _parse_headers(text: str) -> tuple[str, dict[str, str], str]:
    lines = str(text or "").splitlines()
    first = lines[0].strip() if lines else ""
    headers: dict[str, str] = {}
    body_start = len(lines)
    for idx, line in enumerate(lines[1:], start=1):
        if not line.strip():
            body_start = idx + 1
            break
        if ":" in line:
            key, value = line.split(":", 1)
            headers[key.strip().lower()] = value.strip()
    body = "\n".join(lines[body_start:]).strip()
    return first, headers, body


def _redact(text: str) -> str:
    value = str(text or "")
    for key in _SECRET_ENV_KEYS:
        secret = str(os.environ.get(key) or "").strip()
        if secret:
            value = value.replace(secret, "[REDACTED]")
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    value = re.sub(
        r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"']+",
        r"\1[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?i)(api[_ -]?key|private[_ -]?key|mnemonic|seed phrase|password|token)\s*[:=]\s*[^\s,\"'}]+",
        lambda match: match.group(1) + ": [REDACTED]",
        value,
    )
    return value[:12000]


def _build_prompt(provider: str, source_sha: str, incoming: str) -> str:
    return "\n".join(
        [
            f"You are {provider.upper()} replying to GPT through a bounded GitHub mailbox relay.",
            "The mailbox message below is the only task context supplied to you.",
            f"Relevant source SHA, if provided by GPT: {source_sha or '[not supplied]'}",
            "",
            "This is advisory/report-only communication. Do not edit repository files, create or merge pull requests,",
            "deploy or restart services, submit trades, change LIVE/ARMED or capital/risk settings, access wallets/signing",
            "material, request secrets, use arbitrary sudo, or claim you executed shell/Git/GitHub operations.",
            "Do not attempt to retrieve additional mailbox files. Answer the supplied GPT message directly and identify",
            "material uncertainty or any action that must be performed by the trusted GitHub workflow instead.",
            "",
            "GPT MAILBOX MESSAGE:",
            incoming[:12000],
        ]
    )


def relay(provider: str, message_id: str, source_sha: str, incoming: str) -> str:
    provider = str(provider or "").strip().lower()
    if provider not in _ALLOWED_PROVIDERS:
        raise ValueError("unsupported mailbox provider")
    if not _MESSAGE_ID_RE.fullmatch(message_id or ""):
        raise ValueError("invalid mailbox message id")

    expected = "GPT_TO_" + provider.upper()
    first, headers, _ = _parse_headers(incoming)
    if first != expected:
        raise ValueError(f"mailbox prefix must be {expected}")
    if headers.get("status", "").upper() != "REQUEST":
        raise ValueError("mailbox status must be REQUEST")
    if headers.get("message_id") != message_id:
        raise ValueError("mailbox message id mismatch")

    prompt = _build_prompt(provider, source_sha, incoming)

    old_kind = os.environ.get("AI_COST_TASK_KIND")
    old_level = os.environ.get("AI_COST_ROUTE_LEVEL")
    os.environ["AI_COST_TASK_KIND"] = "git-mailbox-relay"
    os.environ["AI_COST_ROUTE_LEVEL"] = "1"
    # Copilot is authenticated through its existing bounded CLI harness. Run it
    # from an empty directory so repository files are not ambient prompt context.
    old_cwd = os.getcwd()
    try:
        if provider == "copilot":
            with tempfile.TemporaryDirectory(prefix="ai-mailbox-copilot-") as tmp:
                os.chdir(tmp)
                rc, out, err = call_provider(provider, prompt)
        else:
            rc, out, err = call_provider(provider, prompt)
    finally:
        os.chdir(old_cwd)
        if old_kind is None:
            os.environ.pop("AI_COST_TASK_KIND", None)
        else:
            os.environ["AI_COST_TASK_KIND"] = old_kind
        if old_level is None:
            os.environ.pop("AI_COST_ROUTE_LEVEL", None)
        else:
            os.environ["AI_COST_ROUTE_LEVEL"] = old_level

    rc = int(rc)
    answer = _redact(str(out or "").strip())
    error = _redact(str(err or "").strip())
    status = "COMPLETED" if rc == 0 and answer else "BLOCKED"
    if not answer:
        answer = error or f"Provider returned code {rc} without an answer."

    prefix = provider.upper() + "_TO_GPT"
    return (
        f"{prefix}\n"
        f"in_reply_to: {message_id}\n"
        f"status: {status}\n"
        f"provider_return_code: {rc}\n\n"
        f"{answer}\n"
    )


def _blocked_reply(provider: str, message_id: str, exc: Exception) -> str:
    provider = str(provider or "").strip().lower()
    prefix = provider.upper() + "_TO_GPT"
    detail = _redact(f"{type(exc).__name__}: {exc}")
    return (
        f"{prefix}\n"
        f"in_reply_to: {message_id}\n"
        "status: BLOCKED\n"
        "provider_return_code: 1\n\n"
        f"Provider relay exception: {detail or 'unknown error'}\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Relay one bounded GPT mailbox message to an AI provider.")
    parser.add_argument("--provider", required=True, choices=sorted(_ALLOWED_PROVIDERS))
    parser.add_argument("--message-id", required=True)
    parser.add_argument("--source-sha", default="")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    incoming = Path(args.input).read_text(encoding="utf-8", errors="replace")
    try:
        reply = relay(args.provider, args.message_id, args.source_sha, incoming)
    except Exception as exc:
        # Return a sanitised diagnostic reply instead of making the workflow
        # collapse to an opaque rc=1. The caller still sees BLOCKED and no
        # protected authority is granted.
        reply = _blocked_reply(args.provider, args.message_id, exc)
    Path(args.output).write_text(reply, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
