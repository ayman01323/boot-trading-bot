from __future__ import annotations

from typing import Mapping


AGENT_IDENTITIES: Mapping[str, dict[str, str]] = {
    "gpt": {"emoji": "🟢", "name": "GPT", "pr_prefix": "[GPT]"},
    "gemini": {"emoji": "🔵", "name": "GEMINI", "pr_prefix": "[GEMINI]"},
    "copilot": {"emoji": "🟡", "name": "COPILOT", "pr_prefix": "[COPILOT]"},
    "claude": {"emoji": "🟣", "name": "CLAUDE", "pr_prefix": "[CLAUDE]"},
}


def _key(provider: str) -> str:
    value = str(provider or "").strip().lower()
    if value not in AGENT_IDENTITIES:
        raise ValueError(f"unknown AI provider: {provider!r}")
    return value


def agent_label(provider: str) -> str:
    row = AGENT_IDENTITIES[_key(provider)]
    return f"{row['emoji']} {row['name']}"


def pr_prefix(provider: str) -> str:
    return AGENT_IDENTITIES[_key(provider)]["pr_prefix"]


def github_header(
    provider: str,
    *,
    role: str = "",
    workflow: str = "",
    cycle: str = "",
    source_sha: str = "",
) -> str:
    row = AGENT_IDENTITIES[_key(provider)]
    lines = [f"{row['emoji']} **AGENT: {row['name']}**"]
    if role:
        lines.append(f"Role: {role}")
    if workflow:
        lines.append(f"Workflow/Task: {workflow}")
    if cycle:
        lines.append(f"Cycle: `{cycle}`")
    if source_sha:
        lines.append(f"Source SHA: `{source_sha}`")
    return "\n".join(lines)
