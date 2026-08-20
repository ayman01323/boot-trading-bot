from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from . import strategy_canary as _canary

_PREV_REFRESH_APPROVALS = _canary.refresh_approvals


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def local_source_commit(repo_root: Path | None = None) -> str:
    """Return the exact deployed worktree commit, or an empty string on uncertainty."""
    root = Path(repo_root or _repo_root())
    try:
        p = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return ""
    value = (p.stdout or "").strip().lower()
    if p.returncode or len(value) != 40 or any(c not in "0123456789abcdef" for c in value):
        return ""
    return value


def guard_approval_state(state: dict[str, Any] | None, deployed_source: str) -> dict[str, Any]:
    """Fail closed when AI approval refers to code other than the deployed source.

    Strategy implementation may be merged and deployed automatically after policy/tests, but that
    deployment must not inherit the older cycle's live-canary approval. The next strategy review
    must explicitly approve the exact newly deployed main commit before CANARY can use it.
    """
    out = dict(state or {})
    approvals = dict(out.get("approvals") or {})
    reviewed = str(out.get("source_commit") or "").strip().lower()
    deployed = str(deployed_source or "").strip().lower()
    source_match = bool(reviewed and deployed and reviewed == deployed)
    out["deployed_source_commit"] = deployed
    out["approval_source_match"] = source_match

    if approvals and not source_match:
        out["approvals"] = {}
        if not deployed:
            reason = "deployed source commit could not be verified"
        elif not reviewed:
            reason = "AI strategy approval has no reviewed source commit"
        else:
            reason = f"AI approval source {reviewed[:12]} does not match deployed source {deployed[:12]}"
        out["approval_guard_reason"] = reason
    elif source_match:
        out["approval_guard_reason"] = "exact reviewed source matches deployed source"
    else:
        out["approval_guard_reason"] = "no live-canary approvals are currently available"
    return out


def refresh_approvals(app=None, *, force: bool = False, now: int | None = None) -> dict:
    state = _PREV_REFRESH_APPROVALS(app, force=force, now=now)
    return guard_approval_state(state, local_source_commit())


def install() -> None:
    if getattr(_canary, "_exact_source_guard_installed", False):
        return
    _canary.refresh_approvals = refresh_approvals
    _canary._exact_source_guard_installed = True


install()
