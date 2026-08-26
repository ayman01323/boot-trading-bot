#!/usr/bin/env python3
"""claude-trading-bot entrypoint.

Usage:
    python run.py check     # run preflight_check.py only, no trading loop starts
    python run.py start     # validate everything, then run the real learnerbot loop

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


def _check_identity_vars() -> None:
    missing = [v for v in REQUIRED_IDENTITY_VARS if not os.environ.get(v, "").strip()]
    if missing:
        raise StartupError(
            "Missing required claude-trading-bot identity/isolation variables, "
            "refusing to start: " + ", ".join(missing)
        )
    csv_dir = Path(os.environ["CSV_DIR"]).resolve()
    data_dir = Path(os.environ["DATA_DIR"]).resolve()
    production_csv_dir = (REPO_ROOT / "CSVbot").resolve()
    production_data_dir = (REPO_ROOT / "data").resolve()
    if csv_dir == production_csv_dir:
        raise StartupError(f"CSV_DIR must not equal the production bot's CSVbot/ ({production_csv_dir})")
    if data_dir == production_data_dir:
        raise StartupError(f"DATA_DIR must not equal the production bot's data/ ({production_data_dir})")


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=THIS_DIR, capture_output=True, text=True, check=True
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def cmd_check() -> int:
    _load_own_env()
    import preflight_check

    return preflight_check.main()


def cmd_start() -> int:
    _load_own_env()
    _check_identity_vars()

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
        f"max_capital=${limits.max_capital_usd:,.2f} "
        f"max_position=${limits.max_position_usd:,.2f} "
        f"max_exposure=${limits.max_total_exposure_usd:,.2f} "
        f"max_open_positions={limits.max_open_positions} "
        f"max_daily_loss=${limits.max_daily_loss_usd:,.2f}"
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
        max_capital_usd=limits.max_capital_usd,
        max_position_usd=limits.max_position_usd,
        max_total_exposure_usd=limits.max_total_exposure_usd,
        max_daily_loss_usd=limits.max_daily_loss_usd,
        wallet_balance_summary="see /balance in Telegram after startup",
        signer_ready=signer_status.ready,
    )
    send_to_chats(app.telegram_bot_token, app.telegram_chat_ids, startup_text)

    # Hand off to the existing, unmodified bot via `python -m learnerbot run` —
    # deliberately a subprocess/exec, NOT `from learnerbot.cli import main`.
    #
    # learnerbot/__main__.py imports ~60 *_patch.py modules (the hard-floor
    # quality gates, profit guards, drawdown protections, and the final
    # trading_runtime_invariant / final_runtime_integrity fail-closed checks)
    # at module import time, then unconditionally does `raise SystemExit(main())`
    # — it is a process entry point, not an importable library call. Importing
    # `learnerbot.cli` directly would skip that entire patch chain and silently
    # run the strategy WITHOUT the hardened gates this project depends on, which
    # is exactly what this bot must never do. `python -m learnerbot run` is the
    # only invocation that reproduces production's exact behaviour — it's the
    # same command in systemd/learnerbot.service's ExecStart.
    os.chdir(REPO_ROOT)
    os.execvpe(sys.executable, [sys.executable, "-m", "learnerbot", "run"], os.environ)


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"check", "start"}:
        print(__doc__)
        return 2
    try:
        if sys.argv[1] == "check":
            return cmd_check()
        return cmd_start()
    except StartupError as exc:
        print(f"REFUSED TO START: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
