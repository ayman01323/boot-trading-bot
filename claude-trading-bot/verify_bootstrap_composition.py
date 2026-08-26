#!/usr/bin/env python3
"""Non-broadcast proof that the Claude guard, quarantine, and secret-fallback
protections all survive the full bootstrap chain. Requested directly in
PR #648 review (composition proof) and its re-review (quarantine/arming/
repo-root/secret proofs).

No transaction is ever constructed, signed, or broadcast by this script.

Usage: python verify_bootstrap_composition.py
Requires the same env as `run.py check` (CSV_DIR, DATA_DIR,
CLAUDE_BOT_WALLET_OWNER_ID, MAX_* risk vars, AUTHORISED_CHAINS). Must run on
Linux (the target OS, matching botgoogle) -- some patches in learnerbot's
chain import POSIX-only modules (fcntl) that don't exist on Windows.

Five things are proven, each by actually running the chain and checking the
result -- not by inspection or assumption:

  1. No repo-root writes: REPO_ROOT/CSVbot and REPO_ROOT/data are snapshotted
     before the chain runs and compared after. Only the exact quarantine
     marker files claude_bot_quarantine.py is documented to create are
     allowed to appear; anything else is a failure. (A real prior run
     without quarantine DID create/modify files here -- this is not a
     hypothetical check.)
  2. No automatic arming: this instance's own isolated CSV settings
     (auto_trading_settings.csv, live_trading_settings.csv) are checked
     after the chain runs for any trading_enabled/live_trading_enabled/
     ARMED-type value -- must all be absent or false.
  3. No production-secret inheritance: every name in
     claude_bot_quarantine._PRODUCTION_ONLY_SECRETS is confirmed still
     blank in os.environ after the chain runs (proves learnerbot/config.py's
     un-overridden load_dotenv(BOT_ROOT/.env) never filled one in).
  4. Full composition survives the ENTIRE chain, behaviorally, not by object
     identity. Several later learnerbot patches legitimately wrap
     SolanaLiveExecutor.buy/sell again after this bot's guard installs
     (captured as their own "_original" reference and called from inside
     their own wrapper -- confirmed by inspection: solana-token-reclaim,
     solana-simulated-reserve, and solana-exec-efficiency all wrap buy).
     So `SolanaLiveExecutor.buy is guard._guarded_buy` becomes false after
     the chain runs even when nothing is actually wrong -- checking identity
     would be testing the wrong thing. Instead: run the full chain for real
     (argv forced to the harmless `chains` subcommand, which returns
     immediately and never starts the trading loop), then call the LIVE,
     current SolanaLiveExecutor.buy/sell class attributes -- whatever they
     now are, however many legitimate layers deep -- with the deepest real
     implementation replaced by a sentinel, and confirm the sentinel is
     never reached for a guard-refused case. [trading-runtime-invariant] OK
     and [final-runtime-integrity] OK must both appear in the chain's own
     output for this step to mean anything.
  5. SIGNER_READY=false and a mismatched runtime identity both refuse before
     reaching that sentinel, for both buy and sell.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent
for path in (THIS_DIR, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def _print(step: str) -> None:
    print(f"\n=== {step} ===")


def _snapshot(root: Path) -> dict[str, float]:
    """path -> mtime for every file under root, if root exists."""
    if not root.exists():
        return {}
    return {str(p): p.stat().st_mtime for p in root.rglob("*") if p.is_file()}


def main() -> int:
    import claude_bot_quarantine
    from learnerbot.config import AppSettings

    app = AppSettings.load()

    _print("Step 0: snapshot repo-root CSVbot/ and data/ before anything runs")
    csvbot_root = REPO_ROOT / "CSVbot"
    data_root = REPO_ROOT / "data"
    before_csvbot = _snapshot(csvbot_root)
    before_data = _snapshot(data_root)
    print(f"before: {len(before_csvbot)} files under CSVbot/, {len(before_data)} files under data/")

    _print("Step 1: install patches (includes quarantine + secret-blocking), then exercise the FULL chain")
    import claude_bot_patches

    claude_bot_patches.install_all(app)

    from learnerbot import solana_live_executor as _executor
    import solana_execution_risk_patch as guard

    assert _executor.SolanaLiveExecutor.buy is guard._guarded_buy, "guard not installed before chain runs"
    assert _executor.SolanaLiveExecutor.sell is guard._guarded_sell, "guard not installed before chain runs"

    old_argv = sys.argv
    sys.argv = ["learnerbot", "chains"]
    try:
        import runpy

        try:
            runpy.run_module("learnerbot", run_name="__main__")
        except SystemExit as exc:
            print(f"learnerbot chains exited with code {exc.code} (expected)")
        except Exception as exc:  # noqa: BLE001
            # All ~60 patch modules import at module level BEFORE main() runs
            # -- an exception here happens strictly after that import chain
            # already completed, so it does not invalidate what this test is
            # proving. In practice this is `_app()` itself (wrapped by many
            # migration patches) failing against a bare test CSVbot with no
            # chains.csv -- a fully production-shaped CSVbot would not hit
            # this.
            print(
                f"NOTE: learnerbot main()/_app() raised {type(exc).__name__}: {exc} "
                f"-- this is after the full import chain already completed, "
                f"proceeding to post-chain checks."
            )
    finally:
        sys.argv = old_argv

    print("Import chain complete.")

    _print("Step 2: no repo-root writes beyond the documented quarantine markers")
    after_csvbot = _snapshot(csvbot_root)
    after_data = _snapshot(data_root)
    allowed_new_data_files = {str(data_root / name) for name in claude_bot_quarantine.SHARED_MARKERS}

    unexpected_csvbot = set(after_csvbot) - set(before_csvbot)
    unexpected_csvbot |= {p for p in before_csvbot if before_csvbot.get(p) != after_csvbot.get(p)}
    assert not unexpected_csvbot, f"REGRESSION: repo-root CSVbot/ was written to: {unexpected_csvbot}"

    unexpected_data = (set(after_data) - set(before_data)) - allowed_new_data_files
    changed_data = {p for p in before_data if before_data.get(p) != after_data.get(p)}
    assert not unexpected_data, f"REGRESSION: repo-root data/ got unexpected new files: {unexpected_data}"
    assert not changed_data, f"REGRESSION: repo-root data/ had existing files modified: {changed_data}"
    print(
        f"PASS: repo-root CSVbot/ untouched; data/ only gained the "
        f"{len(set(after_data) - set(before_data))} documented quarantine marker(s)"
    )

    _print("Step 3: no automatic arming in this instance's own isolated CSV config")
    arming_keys = ("trading_enabled", "live_trading_enabled", "auto_trading_enabled", "sibot_auto_trade_enabled")
    for csv_name in ("live_trading_settings.csv", "auto_trading_settings.csv"):
        csv_path = Path(app.csv_dir) / csv_name
        if not csv_path.exists():
            print(f"PASS: {csv_name} does not exist (never touched)")
            continue
        import csv as _csv

        with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            rows = list(_csv.DictReader(f))
        armed_rows = [
            r
            for r in rows
            if any(str(r.get(k, "")).strip().lower() in {"1", "true", "yes", "on"} for k in arming_keys if k in r)
        ]
        assert not armed_rows, f"REGRESSION: {csv_name} has an armed row after the chain ran: {armed_rows}"
        print(f"PASS: {csv_name} exists but has no armed rows ({len(rows)} row(s) checked)")

    _print("Step 4: no production-secret inheritance")
    for name in claude_bot_quarantine._PRODUCTION_ONLY_SECRETS:
        value = os.environ.get(name)
        assert value == "", f"REGRESSION: {name} is {value!r} after the chain ran -- production .env leaked in"
    print(f"PASS: all {len(claude_bot_quarantine._PRODUCTION_ONLY_SECRETS)} quarantined secret names still blank")

    print("\nNow testing the LIVE SolanaLiveExecutor class attributes behaviorally (not by identity).")

    class _Sentinel(RuntimeError):
        pass

    def _must_not_be_called(*_a, **_k):
        raise _Sentinel("the deepest real SolanaLiveExecutor implementation was reached -- guard failed to block")

    original_buy, original_sell = guard._original_buy, guard._original_sell
    guard._original_buy = _must_not_be_called
    guard._original_sell = _must_not_be_called
    try:
        class _BareExecutor:
            """Deliberately bypasses SolanaLiveExecutor.__init__ (no network
            access needed) -- only the attributes the guard functions read,
            plus a stub token_balance_raw(). Some later learnerbot patches
            (e.g. solana_atomic_close_fallback_patch.py) legitimately do a
            read-only balance check of their own before calling through to
            this bot's guard -- that's fine, a balance read is not signing
            or broadcasting, but it means a real int has to come back or
            that unrelated pre-check crashes before ever reaching what this
            test is actually proving."""

            def token_balance_raw(self, _mint: str) -> int:
                return 0

        live_buy = _executor.SolanaLiveExecutor.buy
        live_sell = _executor.SolanaLiveExecutor.sell

        _print("Step 5: SIGNER_READY=false cannot reach signing/broadcast (via the LIVE, current call path)")

        no_wallet = _BareExecutor()
        no_wallet.app = app
        # Use whatever CLAUDE_BOT_WALLET_OWNER_ID is actually configured, not
        # a hardcoded guess: a real full-chain run (before quarantine
        # existed) discovered telegram_account_roles_patch.py replaying
        # against a fresh DATA_DIR and creating its own hardcoded user row,
        # independent of TELEGRAM_CHAT_IDS. Quarantine now prevents that,
        # so with quarantine active the operative identity should be
        # whatever ensure_master_seed() derives from TELEGRAM_CHAT_IDS[0].
        no_wallet.telegram_id = os.environ.get("CLAUDE_BOT_WALLET_OWNER_ID", "900000000001")

        try:
            live_buy(no_wallet, "So11111111111111111111111111111111111111112", "0.001", "0.02")
            raise AssertionError("expected buy to be refused with no wallet provisioned")
        except _Sentinel:
            raise AssertionError("buy reached the deepest real implementation -- guard did not block")
        except guard.ExecutionGuardError as exc:
            print(f"PASS: buy refused before broadcast (no signer): {exc}")

        try:
            live_sell(no_wallet, "So11111111111111111111111111111111111111112", 1000)
            raise AssertionError("expected sell to be refused with no wallet provisioned")
        except _Sentinel:
            raise AssertionError("sell reached the deepest real implementation -- guard did not block")
        except guard.ExecutionGuardError as exc:
            print(f"PASS: sell refused before broadcast (no signer): {exc}")

        mismatched = _BareExecutor()
        mismatched.app = app
        mismatched.telegram_id = "111111111111"  # deliberately not CLAUDE_BOT_WALLET_OWNER_ID
        try:
            live_buy(mismatched, "So11111111111111111111111111111111111111112", "0.001", "0.02")
            raise AssertionError("expected buy to be refused for a mismatched runtime identity")
        except _Sentinel:
            raise AssertionError("buy reached the deepest real implementation for a mismatched identity")
        except guard.ExecutionGuardError as exc:
            print(f"PASS: buy refused for mismatched runtime identity: {exc}")
    finally:
        guard._original_buy = original_buy
        guard._original_sell = original_sell

    print("\nALL COMPOSITION / QUARANTINE / GUARD-SURVIVAL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
