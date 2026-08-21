from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from . import __version__

LEGACY_VALUE = "legacy-unattributed"
_SHA_RE = re.compile(r"^[0-9a-f]{40,64}$")


def exact_git_sha() -> str:
    """Resolve the exact deployed commit or fail closed.

    A trade must never be attributed to "unknown" or guessed from the current
    branch.  Deployments may inject a full SHA; otherwise a Git checkout is
    required so the process can resolve HEAD exactly.
    """
    for key in ("BOOT_GIT_SHA", "GIT_SHA", "GITHUB_SHA", "SOURCE_VERSION"):
        value = str(os.getenv(key) or "").strip().lower()
        if _SHA_RE.fullmatch(value):
            return value

    repo_root = Path(__file__).resolve().parents[1]
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=3,
        )
        value = proc.stdout.strip().lower()
        if _SHA_RE.fullmatch(value):
            return value
    except Exception:
        pass

    raise RuntimeError(
        "Trade provenance cannot resolve an exact Git SHA. "
        "Deploy from a Git checkout or set BOOT_GIT_SHA to the full commit SHA; "
        "trading will not start with ambiguous provenance."
    )


def strategy_version() -> str:
    value = str(os.getenv("BOOT_STRATEGY_VERSION") or "").strip()
    return value or f"v{__version__}"


STRATEGY_VERSION = strategy_version()
GIT_SHA = exact_git_sha()


def current_identity(*, strategy_engine: str | None = None) -> dict[str, str]:
    out = {
        "strategy_version": STRATEGY_VERSION,
        "git_sha": GIT_SHA,
    }
    if strategy_engine:
        out["strategy_engine"] = str(strategy_engine).strip().upper()
    return out
