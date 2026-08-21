from __future__ import annotations

import json
import os
from pathlib import Path

import resilient_selected_master as _base

# User-selected master is always attempted first. If it fails, never retry it;
# fall back in this exact order: GPT -> Claude -> Gemini -> any remaining provider.
_FALLBACK = ("gpt", "claude", "gemini", "copilot")
_BASE_STRATEGY_PROMPT = _base._strategy_prompt
_BASE_ENGINEERING_PROMPT = _base._engineering_prompt


def _provider_order(preferred: str) -> list[str]:
    preferred = str(preferred or "auto").lower().strip()
    out: list[str] = []
    if preferred in _base.PROVIDERS:
        out.append(preferred)
    for provider in _FALLBACK:
        if provider not in out:
            out.append(provider)
    return out


def _bounded_vps_context() -> str:
    path = str(os.environ.get("CLAUDE_VPS_CONTEXT_PATH") or "").strip()
    if not path:
        return ""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(raw, dict):
        return ""
    # Only these already-sanitised operational fields may enter MASTER prompts.
    security = raw.get("security") if isinstance(raw.get("security"), dict) else {}
    clean = {
        "generated_epoch": int(raw.get("generated_epoch") or 0),
        "action": str(raw.get("action") or "none")[:20],
        "status": str(raw.get("status") or "UNKNOWN")[:30],
        "action_return_code": int(raw.get("action_return_code") or 0),
        "target_sha": str(raw.get("target_sha") or "")[:40],
        "deployed_sha": str(raw.get("deployed_sha") or "")[:40],
        "service_active": bool(raw.get("service_active")),
        "claude_analysis": str(raw.get("claude_analysis") or "")[:1200],
        "security": {
            "root_shell": bool(security.get("root_shell")),
            "arbitrary_sudo": bool(security.get("arbitrary_sudo")),
            "wallet_or_private_key_access": bool(security.get("wallet_or_private_key_access")),
            "arbitrary_deploy_sha": bool(security.get("arbitrary_deploy_sha")),
            "deploy_current_main_via_restricted_wrapper_only": bool(security.get("deploy_current_main_via_restricted_wrapper_only")),
        },
    }
    return json.dumps(clean, sort_keys=True, separators=(",", ":"))


def _with_vps_context(prompt: str) -> str:
    context = _bounded_vps_context()
    if not context:
        return prompt
    return prompt + "\n\nBOUNDED VPS OPERATIONAL CONTEXT:\n" + context + "\nThis context is observational/operational evidence only. It does not grant root, wallet/signing, arbitrary sudo, arbitrary deploy-SHA, LIVE-risk, or safety-gate bypass authority.\n"


def _strategy_prompt(identity: str, source: str, evidence: str, reports: dict[str, dict]) -> str:
    return _with_vps_context(_BASE_STRATEGY_PROMPT(identity, source, evidence, reports))


def _engineering_prompt(source: str, reports: dict[str, dict]) -> str:
    return _with_vps_context(_BASE_ENGINEERING_PROMPT(source, reports))


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
_base._strategy_prompt = _strategy_prompt
_base._engineering_prompt = _engineering_prompt

if __name__ == "__main__":
    raise SystemExit(_base.main())
