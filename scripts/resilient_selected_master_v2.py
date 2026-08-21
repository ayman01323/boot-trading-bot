from __future__ import annotations

import os

import resilient_selected_master as _base

# User-selected master is always attempted first. If it fails, never retry it;
# fall back in this exact order: GPT -> Claude -> Gemini -> any remaining provider.
_FALLBACK = ("gpt", "claude", "gemini", "copilot")


def _provider_order(preferred: str) -> list[str]:
    preferred = str(preferred or "auto").lower().strip()
    out: list[str] = []
    if preferred in _base.PROVIDERS:
        out.append(preferred)
    for provider in _FALLBACK:
        if provider not in out:
            out.append(provider)
    return out


def _call_provider(provider: str, prompt: str):
    if provider != "copilot":
        return _base._call_provider(provider, prompt)

    env = dict(os.environ)
    token = str(
        env.get("COPILOT_GITHUB_TOKEN")
        or env.get("COPILOT_ASSIGN_TOKEN")
        or env.get("GH_TOKEN")
        or env.get("GITHUB_TOKEN")
        or ""
    ).strip()
    if not token:
        return 90, "", "Copilot token unavailable"
    # GitHub Copilot CLI supports CI/non-interactive prompt mode. Plan mode keeps
    # the master read-only; it may adjudicate reports but cannot edit or execute.
    env["GH_TOKEN"] = token
    env["GITHUB_TOKEN"] = token
    cmd = [
        "copilot",
        "--plan",
        "--no-auto-update",
        "--no-ask-user",
        "-s",
        "-p",
        prompt,
    ]
    return _base._run(cmd, "", env)


_base._provider_order = _provider_order
_base._call_provider = _call_provider

if __name__ == "__main__":
    raise SystemExit(_base.main())
