from __future__ import annotations

import argparse
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from learnerbot.ai_council_http_patch import call_provider

AGENTS = ("gpt", "claude", "gemini", "deepseek", "copilot")
_AGENT_SET = set(AGENTS)
_MESSAGE_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,120}$")
_SECRET_ENV_KEYS = (
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "DEEPSEEK_API_KEY",
    "COPILOT_GITHUB_TOKEN",
    "COPILOT_ASSIGN_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)
_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]+"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]+"),
)
_ROUTE_TO_RE = re.compile(r"(?mi)^ROUTE_TO:\s*([A-Za-z]+)\s*$")
_ROUTE_QUESTION_RE = re.compile(r"(?mis)^ROUTE_QUESTION:\s*(.+?)(?=\n[A-Z_]+:|\Z)")


@dataclass(frozen=True)
class Envelope:
    message_id: str
    sender: str
    target: str
    mode: str
    max_hops: int
    body: str


def parse_envelope(text: str) -> Envelope:
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "AI_BUS":
        raise ValueError("message must start with AI_BUS")

    headers: dict[str, str] = {}
    body_start = len(lines)
    for idx, line in enumerate(lines[1:], start=1):
        if not line.strip():
            body_start = idx + 1
            break
        if ":" not in line:
            raise ValueError(f"invalid header line: {line[:80]}")
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    message_id = headers.get("message_id", "")
    if not _MESSAGE_ID_RE.fullmatch(message_id):
        raise ValueError("invalid message_id")

    sender = headers.get("from", "").strip().lower()
    if sender not in _AGENT_SET | {"user"}:
        raise ValueError("from must be USER, GPT, CLAUDE, GEMINI, DEEPSEEK or COPILOT")

    target = headers.get("to", "").strip().lower()
    if target not in _AGENT_SET | {"all"}:
        raise ValueError("to must be GPT, CLAUDE, GEMINI, DEEPSEEK, COPILOT or ALL")

    mode = headers.get("mode", "direct").strip().lower()
    if mode not in {"direct", "collaborate"}:
        raise ValueError("mode must be DIRECT or COLLABORATE")

    try:
        max_hops = int(headers.get("max_hops", "1"))
    except Exception as exc:
        raise ValueError("max_hops must be an integer") from exc
    if max_hops < 1 or max_hops > 3:
        raise ValueError("max_hops must be between 1 and 3")
    if mode == "direct":
        max_hops = 1
    if target == "all":
        # Explicit broadcast is already expensive; do not recursively fan out.
        max_hops = 1

    body = "\n".join(lines[body_start:]).strip()
    if not body:
        raise ValueError("message body cannot be empty")
    if len(body) > 8000:
        raise ValueError("message body exceeds 8000 characters")

    return Envelope(
        message_id=message_id,
        sender=sender,
        target=target,
        mode=mode,
        max_hops=max_hops,
        body=body,
    )


def needs_copilot(envelope: Envelope) -> bool:
    return envelope.target in {"copilot", "all"} or envelope.mode == "collaborate"


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


def _clean_answer(text: str) -> str:
    value = _redact(text).strip()
    value = _ROUTE_TO_RE.sub("", value)
    value = _ROUTE_QUESTION_RE.sub("", value)
    return re.sub(r"\n{3,}", "\n\n", value).strip()


def _route_request(text: str) -> tuple[str, str]:
    route_match = _ROUTE_TO_RE.search(str(text or ""))
    if not route_match:
        return "", ""
    target = route_match.group(1).strip().lower()
    if target == "none":
        return "", ""
    if target not in _AGENT_SET:
        return "", ""
    question_match = _ROUTE_QUESTION_RE.search(str(text or ""))
    question = question_match.group(1).strip() if question_match else ""
    if not question:
        return "", ""
    return target, question[:3000]


def _prompt(
    *,
    envelope: Envelope,
    target: str,
    hop: int,
    current_question: str,
    transcript: list[tuple[str, str]],
) -> str:
    history = "\n\n".join(
        f"===== {agent.upper()} PRIOR REPLY =====\n{answer}"
        for agent, answer in transcript[-2:]
    )
    route_instruction = (
        "This is DIRECT mode. Answer the sender and do not request another agent."
        if envelope.mode == "direct" or hop >= envelope.max_hops or envelope.target == "all"
        else (
            "This is COLLABORATE mode. If another AI agent's input is materially useful, you may finish with exactly "
            "two routing lines: ROUTE_TO: <GPT|CLAUDE|GEMINI|DEEPSEEK|COPILOT> and "
            "ROUTE_QUESTION: <one bounded question>. Otherwise finish with ROUTE_TO: NONE. "
            "Routing is optional and the router enforces the hop limit."
        )
    )
    return "\n".join(
        [
            f"You are {target.upper()} receiving an event-driven message on the repository's bounded AI bus.",
            f"Original sender: {envelope.sender.upper()}",
            f"Message ID: {envelope.message_id}",
            f"Hop: {hop} of {envelope.max_hops}",
            "",
            "Communication only. Do not edit files, push or merge code, deploy or restart services, submit trades,",
            "change LIVE/ARMED/risk/capital settings, access wallets/signing material, request secrets, or use arbitrary sudo.",
            "Do not claim you performed shell/Git/GitHub actions. Identify uncertainty rather than inventing repository state.",
            route_instruction,
            "",
            "CURRENT MESSAGE:",
            current_question[:8000],
            "",
            "PRIOR BUS CONTEXT:" if history else "PRIOR BUS CONTEXT: [none]",
            history,
            "",
            "Return a concise substantive reply.",
        ]
    )


def _call(target: str, prompt: str) -> tuple[int, str, str]:
    old_cwd = os.getcwd()
    try:
        if target == "copilot":
            # Keep Copilot away from the checked-out repository; it receives only
            # the bounded bus prompt and existing CLI credential.
            with tempfile.TemporaryDirectory(prefix="ai-bus-copilot-") as tmp:
                os.chdir(tmp)
                return call_provider(target, prompt)
        return call_provider(target, prompt)
    finally:
        os.chdir(old_cwd)


def run_bus(envelope: Envelope) -> str:
    if envelope.target == "all":
        initial_targets = [a for a in AGENTS if a != envelope.sender]
        if not initial_targets:
            initial_targets = list(AGENTS)
    else:
        initial_targets = [envelope.target]

    transcript: list[tuple[str, str]] = []
    records: list[dict[str, object]] = []

    for initial_target in initial_targets:
        target = initial_target
        question = envelope.body
        local_transcript = list(transcript) if envelope.target != "all" else []

        for hop in range(1, envelope.max_hops + 1):
            prompt = _prompt(
                envelope=envelope,
                target=target,
                hop=hop,
                current_question=question,
                transcript=local_transcript,
            )
            rc, out, err = _call(target, prompt)
            rc = int(rc)
            raw = str(out or "").strip()
            error = _redact(str(err or "").strip())
            answer = _clean_answer(raw)
            status = "COMPLETED" if rc == 0 and answer else "BLOCKED"
            if not answer:
                answer = error or f"Provider returned code {rc} without an answer."

            records.append(
                {
                    "agent": target,
                    "hop": hop,
                    "return_code": rc,
                    "status": status,
                    "answer": answer,
                }
            )
            local_transcript.append((target, answer))
            if envelope.target != "all":
                transcript.append((target, answer))

            if status != "COMPLETED" or envelope.mode != "collaborate" or hop >= envelope.max_hops:
                break
            route_to, route_question = _route_request(raw)
            if not route_to or route_to == target:
                break
            target = route_to
            question = route_question

    completed = sum(1 for row in records if row["status"] == "COMPLETED")
    overall = "COMPLETED" if completed == len(records) and records else ("PARTIAL" if completed else "BLOCKED")

    lines = [
        "AI_BUS_REPLY",
        f"message_id: {envelope.message_id}",
        f"from: BUS",
        f"to: {envelope.sender.upper()}",
        f"status: {overall}",
        f"mode: {envelope.mode.upper()}",
        f"provider_calls: {len(records)}",
        f"max_hops: {envelope.max_hops}",
        "",
    ]
    for row in records:
        lines.extend(
            [
                f"### {str(row['agent']).upper()} · hop {row['hop']} · {row['status']} · rc {row['return_code']}",
                "",
                str(row["answer"]),
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one bounded event-driven AI bus message.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args()

    envelope = parse_envelope(Path(args.input).read_text(encoding="utf-8", errors="replace"))
    if args.preflight:
        print(f"target={envelope.target}")
        print(f"mode={envelope.mode}")
        print(f"needs_copilot={'true' if needs_copilot(envelope) else 'false'}")
        print(f"message_id={envelope.message_id}")
        return 0

    Path(args.output).write_text(run_bus(envelope), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
