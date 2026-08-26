#!/usr/bin/env python3
"""claude-trading-bot entrypoint.

Usage:
    python run.py check               # run preflight_check.py only, no trading loop starts
    python run.py start               # validate everything, then run the real learnerbot loop
    python run.py send-test-telegram  # explicit, human-triggered only: sends the one-time
                                       # connectivity/format test message via THIS instance's
                                       # own token, then exits. Never called automatically.

Design choice (deliberately conservative): `start` always requires the hard risk
engine config (risk_engine_guard.py) to be present and valid, even if this
instance's own CSV-backed LIVE flags are still off. Branching on those flags first
would mean one more piece of parsing logic that could hide a bug in the fail-closed
path; requiring the risk config unconditionally is simpler to verify and cannot
accidentally let a misconfigured instance start. See README.md.

This file does not implement trading logic. It isolates environment/config,
validates fail-closed preconditions, and then hands off to the existing,
unmodified learnerbot package.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent

# Default to the real, operator-provisioned runtime config location on the
# Google server (outside git, placed there directly -- not something this
# bot writes or commits). CLAUDE_BOT_ENV_FILE overrides this for local dev/
# testing, so the same code works off-server without editing this constant.
DEFAULT_ENV_FILE = Path("/home/ayman01323/ClaudeServer/runtime/claude-trading-bot.env")
ENV_FILE = Path(os.environ.get("CLAUDE_BOT_ENV_FILE") or DEFAULT_ENV_FILE)

# Same directory as DEFAULT_ENV_FILE -- the one location on the Google server
# that's already fixed and known, independent of what any operator types in.
DEFAULT_RUNTIME_DIR = DEFAULT_ENV_FILE.parent

# Make `learnerbot` importable regardless of cwd or whether it's pip-installed —
# don't depend on install state matching production's venv.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REQUIRED_IDENTITY_VARS = (
    "CSV_DIR",
    "DATA_DIR",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_IDS",
)


class StartupError(RuntimeError):
    pass


def _load_own_env() -> None:
    """Load this instance's own runtime env file only — never the production bot's .env.

    Loaded with override=True: if this process somehow already has a stale
    production value set (e.g. a shared parent shell), this instance's own
    runtime env file always wins for the keys it defines.
    """
    if not ENV_FILE.exists():
        raise StartupError(
            f"{ENV_FILE} not found. Provision it at that path (see env.example "
            f"for the required keys), or set CLAUDE_BOT_ENV_FILE to point at a "
            f"local copy for testing off-server."
        )
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise StartupError("python-dotenv not installed in this environment") from exc
    load_dotenv(ENV_FILE, override=True)


def _apply_deterministic_runtime_dir_defaults() -> None:
    """Fill CSV_DIR/DATA_DIR from a fixed, known-safe location if this
    instance's own env file didn't set them explicitly -- never overrides an
    explicit value, only fills the gap.

    Preflight against the real Google-server runtime file found CSV_DIR/
    DATA_DIR simply blank (an operator hasn't typed them in yet), which
    means every startup depended on that manual entry being both present
    and correct (outside the git checkout) -- exactly the kind of manual
    step review asked to make deterministic instead. Since
    DEFAULT_RUNTIME_DIR is already a fixed, known-safe location (the same
    directory DEFAULT_ENV_FILE lives in, structurally guaranteed outside
    the git checkout), deriving CSV_DIR/DATA_DIR from it removes the need
    to type them at all in the common case, while an operator who DOES set
    them explicitly (e.g. for local/off-server testing via
    CLAUDE_BOT_ENV_FILE) is still fully respected.

    Deliberately NOT os.environ.setdefault(): the real runtime file has
    `CSV_DIR=` with no value, not the key entirely absent -- load_dotenv()
    loads that as os.environ["CSV_DIR"] = "" (present, blank), and
    setdefault() only fills a key that is not present at all, so it would
    never have actually applied the default against the real file (caught
    by testing this against the exact blank-key shape, not just an unset
    key). Treats present-but-blank the same as absent.
    """
    if not os.environ.get("CSV_DIR", "").strip():
        os.environ["CSV_DIR"] = str(DEFAULT_RUNTIME_DIR / "CSVbot")
    if not os.environ.get("DATA_DIR", "").strip():
        os.environ["DATA_DIR"] = str(DEFAULT_RUNTIME_DIR / "data")


def _is_inside(path: Path, container: Path) -> bool:
    try:
        path.relative_to(container)
        return True
    except ValueError:
        return False


def _check_identity_vars() -> None:
    """Fail closed on ANY unsafe CSV_DIR/DATA_DIR, not just an exact match
    with production's own paths.

    Case 3 of the runtime-directory hardening: an operator (or a stale copy
    of this instance's env file) could set CSV_DIR/DATA_DIR to some OTHER
    path inside the git checkout -- e.g. REPO_ROOT/claude-trading-bot/CSVbot
    -- which the old exact-equality check against REPO_ROOT/CSVbot would
    have silently let through, even though it has exactly the same
    sync-blocking consequence documented in README.md. This checks
    "anywhere inside REPO_ROOT" generally, and refuses to start rather than
    silently substituting a safe value -- an operator who explicitly
    (if mistakenly) set an unsafe path needs to see why it was rejected,
    not have it quietly overridden.
    """
    missing = [v for v in REQUIRED_IDENTITY_VARS if not os.environ.get(v, "").strip()]
    if missing:
        raise StartupError(
            "Missing required claude-trading-bot identity/isolation variables, "
            "refusing to start: " + ", ".join(missing)
        )
    csv_dir = Path(os.environ["CSV_DIR"]).resolve()
    data_dir = Path(os.environ["DATA_DIR"]).resolve()
    repo_root_resolved = REPO_ROOT.resolve()
    if _is_inside(csv_dir, repo_root_resolved):
        raise StartupError(
            f"CSV_DIR={csv_dir} resolves inside the git-managed checkout ({repo_root_resolved}) "
            f"-- refusing to start rather than silently using a different path. "
            f"Unset CSV_DIR to use the safe deterministic default, or point it "
            f"somewhere outside the checkout entirely."
        )
    if _is_inside(data_dir, repo_root_resolved):
        raise StartupError(
            f"DATA_DIR={data_dir} resolves inside the git-managed checkout ({repo_root_resolved}) "
            f"-- refusing to start rather than silently using a different path. "
            f"Unset DATA_DIR to use the safe deterministic default, or point it "
            f"somewhere outside the checkout entirely."
        )


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=THIS_DIR, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _quarantine_before_learnerbot() -> None:
    """Must run before the first `import learnerbot` (any submodule) in this
    process -- review confirmed the parent process (this file) imports
    learnerbot before exec just as much as the child (bootstrap_run.py)
    does, so both need this at the same point in their own startup, not
    just the child. See claude_bot_quarantine.py for what this actually
    does and why."""
    import claude_bot_quarantine

    claude_bot_quarantine.quarantine_before_any_learnerbot_import()


def cmd_check() -> int:
    _load_own_env()
    _apply_deterministic_runtime_dir_defaults()
    _quarantine_before_learnerbot()
    import preflight_check

    return preflight_check.main()


def cmd_start() -> int:
    _load_own_env()
    _apply_deterministic_runtime_dir_defaults()
    _check_identity_vars()
    _quarantine_before_learnerbot()

    # Fail closed: hard risk engine config must be valid before the loop starts.
    import risk_engine_guard

    try:
        limits = risk_engine_guard.RiskLimits.load()
    except risk_engine_guard.RiskGuardConfigError as exc:
        raise StartupError(f"Hard risk engine config invalid, refusing to arm: {exc}") from exc

    import identity_patch

    identity_patch.install()

    # Import learnerbot only after env isolation + risk config are confirmed —
    # this guarantees AppSettings.load() below reads THIS instance's CSV_DIR/
    # DATA_DIR, never production's.
    from learnerbot.config import AppSettings

    app = AppSettings.load()
    print(f"claude-trading-bot starting: csv_dir={app.csv_dir} data_dir={app.data_dir}")
    print(
        "Hard risk limits: "
        f"capital_basis=${limits.capital_basis_usd:,.2f} "
        f"max_position={limits.max_position_pct:.2f}%(${limits.max_position_usd:,.2f}) "
        f"max_exposure={limits.max_total_exposure_pct:.2f}%(${limits.max_total_exposure_usd:,.2f}) "
        f"max_open_positions={limits.max_open_positions} "
        f"max_drawdown={limits.max_drawdown_pct:.2f}%"
    )

    import signing_interface

    signer_status = signing_interface.get_signer_status(app)
    print(f"Signer status: {signer_status.reason}")

    from learnerbot.telegram import send_to_chats

    startup_text = identity_patch.build_startup_message(
        version="claude-trading-bot/0.1",
        github_sha=_git_sha(),
        server_sha=os.environ.get("CLAUDE_BOT_SERVER_SHA", "unknown"),
        mode="LIVE-capable (platform gates apply, see README)",
        authorised_chains=[c.strip() for c in os.environ.get("AUTHORISED_CHAINS", "").split(",") if c.strip()],
        active_strategy="learnerbot leader-quality / momentum (reused, unmodified)",
        capital_basis_usd=limits.capital_basis_usd,
        max_position_usd=limits.max_position_usd,
        max_total_exposure_usd=limits.max_total_exposure_usd,
        max_drawdown_pct=limits.max_drawdown_pct,
        wallet_balance_summary="see /balance in Telegram after startup",
        signer_ready=signer_status.ready,
    )
    send_to_chats(app.telegram_bot_token, app.telegram_chat_ids, startup_text)

    # Hand off via bootstrap_run.py — deliberately a subprocess/exec, NOT
    # `from learnerbot.cli import main`.
    #
    # learnerbot/__main__.py imports ~60 *_patch.py modules (the hard-floor
    # quality gates, profit guards, drawdown protections, and the final
    # trading_runtime_invariant / final_runtime_integrity fail-closed checks)
    # at module import time, then unconditionally does `raise SystemExit(main())`
    # — it is a process entry point, not an importable library call. Importing
    # `learnerbot.cli` directly would skip that entire patch chain and silently
    # run the strategy WITHOUT the hardened gates this project depends on, which
    # is exactly what this bot must never do.
    #
    # bootstrap_run.py (not a bare `python -m learnerbot run`) is the exec
    # target because os.execvpe() replaces the process image entirely — the
    # identity_patch/risk-guard patches installed above in THIS process are
    # gone the instant exec happens. bootstrap_run.py installs them again
    # inside the child, before it runs learnerbot exactly the way `-m` would
    # (runpy.run_module(..., run_name="__main__")), so the effect is
    # equivalent to `python -m learnerbot run` plus those two patches, not a
    # different invocation.
    os.chdir(REPO_ROOT)
    os.execvpe(sys.executable, [sys.executable, str(THIS_DIR / "bootstrap_run.py")], os.environ)


def cmd_send_test_telegram() -> int:
    """Explicit, human-triggered only. Never called from cmd_start() or any
    patch chain -- see telegram_connectivity_test.py's module docstring for
    why that separation matters. Uses this instance's own isolated env/app,
    never production's."""
    _load_own_env()
    _apply_deterministic_runtime_dir_defaults()
    _quarantine_before_learnerbot()

    import identity_patch

    identity_patch.install()

    from learnerbot.config import AppSettings

    import telegram_connectivity_test

    app = AppSettings.load()
    result = telegram_connectivity_test.send_once(app)
    print(f"send-test-telegram: {result}")
    return 0 if result.get("sent") else 1


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"check", "start", "send-test-telegram"}:
        print(__doc__)
        return 2
    try:
        if sys.argv[1] == "check":
            return cmd_check()
        if sys.argv[1] == "send-test-telegram":
            return cmd_send_test_telegram()
        return cmd_start()
    except StartupError as exc:
        print(f"REFUSED TO START: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
