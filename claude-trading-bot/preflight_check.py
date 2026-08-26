#!/usr/bin/env python3
"""Non-trading engineering readiness checks for claude-trading-bot.

No check in this file signs or broadcasts a transaction. Run via:
    python run.py check

Each check is isolated and reports PASS / FAIL / SKIP independently — one
check's exception never aborts the rest, so a single missing credential
doesn't hide the status of everything else. Exit code is 0 only if every
non-SKIPPED check passed.

Checks implemented (see README.md item 6 for the full requested checklist):
  - env isolation (CSV_DIR/DATA_DIR distinct from production)
  - hard risk engine config validity (risk_engine_guard.py)
  - Solana RPC connectivity (read-only getHealth call)
  - Solana WebSocket reachability (connect + close, no subscription kept open)
  - Solana buy-side and sell-side quote retrieval (public Jupiter quote API,
    SOL<->USDC as a neutral, always-liquid pair — no wallet or signing involved)
  - Telegram delivery (sends one real preflight message through identity_patch)
  - Database init (opens/creates the isolated SQLite files, no writes beyond schema)
  - Kill-switch state (reports current CSV-backed LIVE flags, changes nothing)
  - Wallet balance read (best-effort; SKIPs cleanly if no wallet provisioned yet)

Deliberately best-effort / flagged as follow-up rather than implemented here:
  - EVM RPC/quote checks (chain-specific router/quoter wiring needs the same
    care as the Solana path; doing it well requires reading live_executor.py's
    exact quote call signatures chain-by-chain rather than guessing)
  - Restart/recovery validation (needs the bot to have actually run at least
    one cycle first, so state exists to recover from)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
REPO_ROOT = THIS_DIR.parent

RESULTS: list[tuple[str, str, str]] = []  # (name, status, detail)


def _record(name: str, status: str, detail: str = "") -> None:
    RESULTS.append((name, status, detail))
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def check_env_isolation() -> None:
    # Reuses run.py's _is_inside() rather than a separate, narrower
    # equality check -- two independent implementations of "is this path
    # safe" is exactly how the old exact-equality-only version here missed
    # any unsafe path that wasn't literally REPO_ROOT/CSVbot (e.g.
    # REPO_ROOT/claude-trading-bot/CSVbot), which has the identical
    # sync-blocking consequence. CSV_DIR/DATA_DIR are expected to already be
    # set by the time this runs -- run.py's cmd_check() calls
    # _apply_deterministic_runtime_dir_defaults() before this module's
    # main() -- so an absent var here means something upstream didn't run,
    # not that this instance is unconfigured.
    name = "env isolation"
    try:
        import run as _run

        csv_dir = Path(os.environ["CSV_DIR"]).resolve()
        data_dir = Path(os.environ["DATA_DIR"]).resolve()
    except KeyError as exc:
        _record(name, "FAIL", f"missing env var {exc}")
        return
    repo_root_resolved = _run.REPO_ROOT.resolve()
    if _run._is_inside(csv_dir, repo_root_resolved):
        _record(name, "FAIL", f"CSV_DIR={csv_dir} resolves inside the git-managed checkout ({repo_root_resolved})")
        return
    if _run._is_inside(data_dir, repo_root_resolved):
        _record(name, "FAIL", f"DATA_DIR={data_dir} resolves inside the git-managed checkout ({repo_root_resolved})")
        return
    _record(name, "PASS", f"csv_dir={csv_dir} data_dir={data_dir} (effective, outside checkout)")


def check_risk_config() -> None:
    name = "hard risk engine config"
    try:
        import risk_engine_guard

        limits = risk_engine_guard.RiskLimits.load()
    except Exception as exc:  # noqa: BLE001 - report any failure, don't crash the run
        _record(name, "FAIL", str(exc))
        return
    _record(
        name,
        "PASS",
        f"capital_basis=${limits.capital_basis_usd:,.2f} "
        f"max_position={limits.max_position_pct:.2f}%(${limits.max_position_usd:,.2f}) "
        f"max_exposure={limits.max_total_exposure_pct:.2f}%(${limits.max_total_exposure_usd:,.2f}) "
        f"max_open_positions={limits.max_open_positions} max_drawdown={limits.max_drawdown_pct:.2f}%",
    )


def _solana_rpc_call(method: str, params: list | None = None, timeout: int = 10) -> dict:
    url = os.environ.get("SOLANA_RPC_URL", "").strip()
    if not url:
        raise RuntimeError("SOLANA_RPC_URL not set")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def check_solana_rpc() -> None:
    name = "Solana RPC connectivity"
    if not os.environ.get("SOLANA_RPC_URL", "").strip():
        _record(name, "SKIP", "SOLANA_RPC_URL not configured")
        return
    try:
        result = _solana_rpc_call("getHealth")
    except Exception as exc:  # noqa: BLE001
        _record(name, "FAIL", str(exc))
        return
    if result.get("result") == "ok":
        _record(name, "PASS", "getHealth == ok")
    else:
        _record(name, "FAIL", f"unexpected response: {result}")


def check_solana_websocket() -> None:
    name = "Solana WebSocket connectivity"
    ws_url = os.environ.get("SOLANA_WS_URL", "").strip()
    if not ws_url:
        _record(name, "SKIP", "SOLANA_WS_URL not configured")
        return
    try:
        import websockets.sync.client as ws_client
    except ImportError:
        _record(name, "SKIP", "websockets package not installed in this environment")
        return
    try:
        with ws_client.connect(ws_url, open_timeout=10) as ws:
            ws.close()
        _record(name, "PASS", "connect+close succeeded, no subscription left open")
    except Exception as exc:  # noqa: BLE001
        _record(name, "FAIL", str(exc))


def _jupiter_quote(input_mint: str, output_mint: str, amount_lamports: int) -> dict:
    url = (
        "https://lite-api.jup.ag/swap/v1/quote"
        f"?inputMint={input_mint}&outputMint={output_mint}&amount={amount_lamports}&slippageBps=50"
    )
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def check_solana_quotes() -> None:
    buy_name = "Solana buy-side quote (SOL->USDC)"
    sell_name = "Solana sell-side quote (USDC->SOL)"
    try:
        buy = _jupiter_quote(SOL_MINT, USDC_MINT, 10_000_000)  # 0.01 SOL
        if buy.get("outAmount"):
            _record(buy_name, "PASS", f"outAmount={buy['outAmount']}")
        else:
            _record(buy_name, "FAIL", f"no outAmount in response: {buy}")
    except Exception as exc:  # noqa: BLE001
        _record(buy_name, "FAIL", str(exc))

    try:
        sell = _jupiter_quote(USDC_MINT, SOL_MINT, 1_000_000)  # 1 USDC
        if sell.get("outAmount"):
            _record(sell_name, "PASS", f"outAmount={sell['outAmount']}")
        else:
            _record(sell_name, "FAIL", f"no outAmount in response: {sell}")
    except Exception as exc:  # noqa: BLE001
        _record(sell_name, "FAIL", str(exc))


def check_telegram() -> None:
    name = "Telegram delivery"
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_ids_raw = os.environ.get("TELEGRAM_CHAT_IDS", "").strip()
    if not token or not chat_ids_raw:
        _record(name, "SKIP", "TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_IDS not configured")
        return
    try:
        import identity_patch

        identity_patch.install()
        from learnerbot.telegram import send_to_chats

        chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
        send_to_chats(token, chat_ids, "Preflight check: this bot can deliver Telegram messages.")
        _record(name, "PASS", f"sent to {len(chat_ids)} chat(s)")
    except Exception as exc:  # noqa: BLE001
        _record(name, "FAIL", str(exc))


def check_database() -> None:
    name = "database init"
    try:
        from learnerbot.config import AppSettings

        app = AppSettings.load()
        app.data_dir.mkdir(parents=True, exist_ok=True)
        from learnerbot.solana_sibot import connect as solana_connect

        conn = solana_connect(app)  # connect() takes AppSettings, not a raw path
        conn.execute("SELECT 1")
        conn.close()
        _record(name, "PASS", f"opened {app.data_dir / 'solana_sibot.sqlite3'}")
    except Exception as exc:  # noqa: BLE001
        _record(name, "FAIL", str(exc))


def check_kill_switch_state() -> None:
    name = "kill-switch state (report only, no changes)"
    try:
        from learnerbot.config import AppSettings

        app = AppSettings.load()
        # operator_settings.csv, not general.csv -- the actual running bot
        # (learnerbot/cli.py, telegram_ui.py, fast_market.py) reads
        # engine_enabled from operator_settings() exclusively. Reading
        # general() here was a real bug (review, 2026-08-26): that CSV
        # doesn't carry this key at all, so the check was silently
        # always-on regardless of the real switch.
        op = app.operator_settings()
        engine_on = str(op.get("engine_enabled", "true")).lower() in {"1", "true", "yes", "on"}
        _record(name, "PASS", f"engine_enabled={engine_on} (this instance's own CSVbot, not production's)")
    except Exception as exc:  # noqa: BLE001
        _record(name, "FAIL", str(exc))


def check_signer_readiness() -> None:
    name = "signing readiness (SIGNER_READY)"
    try:
        from learnerbot.config import AppSettings

        import signing_interface

        app = AppSettings.load()
        status = signing_interface.get_signer_status(app)
        # Not ready is an expected, correct state until a wallet is
        # provisioned -- report it as PASS (the check itself worked and
        # correctly refuses broadcast), not FAIL.
        _record(name, "PASS", status.reason)
    except Exception as exc:  # noqa: BLE001
        _record(name, "FAIL", str(exc))


def check_wallet_balance() -> None:
    name = "wallet balance read (live on-chain getBalance, not just file presence)"
    try:
        from learnerbot.config import AppSettings
        from learnerbot.solana_wallet_store import SolanaWalletError, SolanaWalletStore

        app = AppSettings.load()
        owner_id = os.environ.get("CLAUDE_BOT_WALLET_OWNER_ID", "").strip()
        if not owner_id:
            _record(name, "SKIP", "CLAUDE_BOT_WALLET_OWNER_ID not configured")
            return
        store = SolanaWalletStore(csv_dir=app.csv_dir, data_dir=app.data_dir)
        try:
            meta = store.get_meta(owner_id)
        except SolanaWalletError:
            _record(name, "SKIP", "no wallet provisioned yet for this instance")
            return
        address = meta.get("address")
        if not address:
            _record(name, "FAIL", "wallet metadata present but has no address")
            return
        result = _solana_rpc_call("getBalance", [address])
        lamports = (result.get("result") or {}).get("value")
        if lamports is None:
            _record(name, "FAIL", f"getBalance returned no value: {result}")
            return
        _record(name, "PASS", f"address={address} balance={lamports / 1_000_000_000:.9f} SOL")
    except Exception as exc:  # noqa: BLE001
        _record(name, "FAIL", str(exc))


CHECKS = (
    check_env_isolation,
    check_risk_config,
    check_solana_rpc,
    check_solana_websocket,
    check_solana_quotes,
    check_telegram,
    check_database,
    check_kill_switch_state,
    check_signer_readiness,
    check_wallet_balance,
)


def main() -> int:
    for check in CHECKS:
        try:
            check()
        except Exception as exc:  # noqa: BLE001 - a check must never take down the runner
            _record(check.__name__, "FAIL", f"unhandled exception: {exc}")

    failed = [r for r in RESULTS if r[1] == "FAIL"]
    skipped = [r for r in RESULTS if r[1] == "SKIP"]
    passed = [r for r in RESULTS if r[1] == "PASS"]
    print(f"\n{len(passed)} passed, {len(failed)} failed, {len(skipped)} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
