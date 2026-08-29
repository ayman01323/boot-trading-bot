"""Thin adapter from the Grok canary to the audited learnerbot Solana executor.

Only this module and ``live_canary_runner`` touch ``learnerbot``. The PAPER
runner and every other Grok module stay fully isolated. Nothing here is imported
unless the canary runner is started with ``--enable-live-canary``.

Reuse (not reimplement): ``learnerbot.solana_live_executor.SolanaLiveExecutor``
provides Jupiter order -> local single-signer signing -> mandatory
``simulateTransaction`` -> execute -> post-execution economic proof.

Direction: the Grok known-asset is native SOL, so an ENTRY is USDC -> SOL and an
EXIT is SOL -> USDC. ``swap()`` is direction-generic and Jupiter handles
wrap/unwrap. Entry and exit funding checks are owned by this adapter because the
executor's ``buy``/``sell`` helpers assume SOL->SPL-token direction.
"""
from __future__ import annotations

import base64
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .live_canary import SOL_FEE_RESERVE_LAMPORTS

USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL_MINT = "So11111111111111111111111111111111111111112"


class ExecConfigError(RuntimeError):
    """Adapter/environment is not configured for the canary."""


class ExecPreBroadcastError(RuntimeError):
    """Failed before any transaction was broadcast. Safe: no reconciliation needed."""


class ExecAmbiguousError(RuntimeError):
    """Failed at or after broadcast. A transaction may have gone out."""


class ExecPostLandError(RuntimeError):
    """Landed / reported success but economic output could not be proven."""

    def __init__(self, message: str, signature: str = ""):
        super().__init__(message)
        self.signature = str(signature or "")


@dataclass(frozen=True)
class _ExecApp:
    csv_dir: Path
    data_dir: Path
    telegram_bot_token: str = ""


def _repo_root_on_path() -> None:
    root = os.environ.get("GROK_BOOT_REPO_ROOT", "").strip()
    if not root:
        return
    p = str(Path(root).expanduser())
    if p not in sys.path:
        sys.path.insert(0, p)


def _learnerbot():
    _repo_root_on_path()
    try:
        from learnerbot import solana_live_executor as _exec  # noqa: WPS433
        from learnerbot import solana_sibot as _sol  # noqa: WPS433
        from learnerbot import solana_rpc_failover_patch as _rpc_failover  # noqa: WPS433
        from learnerbot.solana_wallet_store import SolanaWalletStore  # noqa: WPS433
    except Exception as exc:  # pragma: no cover - env-specific
        raise ExecConfigError(
            "learnerbot Solana executor is not importable; set GROK_BOOT_REPO_ROOT "
            f"and install the [live] extra ({type(exc).__name__}: {exc})"
        ) from exc

    # The standalone Grok process does not run learnerbot's normal patch chain.
    # Install the same secret-safe multi-endpoint RPC failover locally so signer,
    # balance and simulation calls can fail over on 401/403/429/transient faults.
    if _sol._rpc is not _rpc_failover.rpc_failover:
        _rpc_failover.install()
    return _exec, _sol, SolanaWalletStore


def _exec_app() -> _ExecApp:
    csv_dir = os.environ.get("GROK_LEARNERBOT_CSV_DIR", "").strip()
    data_dir = os.environ.get("GROK_LEARNERBOT_DATA_DIR", "").strip()
    if not csv_dir or not data_dir:
        raise ExecConfigError(
            "GROK_LEARNERBOT_CSV_DIR and GROK_LEARNERBOT_DATA_DIR must point at the "
            "shared learnerbot wallet/settings directories"
        )
    return _ExecApp(csv_dir=Path(csv_dir).expanduser(), data_dir=Path(data_dir).expanduser())


def canary_telegram_id() -> str:
    tid = os.environ.get("GROK_LIVE_CANARY_TELEGRAM_ID", "").strip()
    if not tid:
        raise ExecConfigError("GROK_LIVE_CANARY_TELEGRAM_ID is required for the canary signer")
    return tid


def build_executor():
    _exec, _sol, _store = _learnerbot()
    return _exec.SolanaLiveExecutor(_exec_app(), canary_telegram_id())


def signer_status() -> tuple[bool, str]:
    try:
        _exec, _sol, SolanaWalletStore = _learnerbot()
        app = _exec_app()
        store = SolanaWalletStore(app.csv_dir, app.data_dir)
        tid = canary_telegram_id()
        meta = store.get_meta(tid)
        if not store.has_private_key(tid, meta.get("wallet_id")):
            return False, "encrypted Solana signer is not available for the canary wallet"
        if not str(meta.get("address") or "").strip():
            return False, "canary wallet has no active address"
        return True, "ready"
    except ExecConfigError as exc:
        return False, str(exc)
    except Exception as exc:  # pragma: no cover - env-specific
        return False, f"{type(exc).__name__}: {exc}"


def _amount_int(value: Any, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return int(default)


def _pre_broadcast_gate(executor, input_mint: str, output_mint: str, amount_raw: int) -> None:
    """Order + local single-signer sign + simulateTransaction, without executing.

    Any failure here is genuinely pre-broadcast. Raised as ExecPreBroadcastError
    so the caller can reject the ticket without a reconciliation hold.
    """
    from learnerbot.solana_live_executor import sign_versioned_transaction  # noqa: WPS433

    try:
        order = executor._order(input_mint, output_mint, int(amount_raw))
        raw = base64.b64decode(order["transaction"], validate=True)
        signed = sign_versioned_transaction(raw, executor.keypair)
        signed_b64 = base64.b64encode(signed).decode("ascii")
        executor._simulate(signed_b64)
    except Exception as exc:
        raise ExecPreBroadcastError(f"{type(exc).__name__}: {exc}") from exc


def execute_swap(
    *,
    input_mint: str,
    output_mint: str,
    amount_raw: int,
    min_out_raw: int,
    executor=None,
    on_broadcast_submitted=None,
) -> dict[str, Any]:
    """Run the canary swap through the audited executor.

    Sequence: pre-broadcast gate (safe) -> mark BROADCAST_SUBMITTED -> real swap.
    Returns ``{"signature", "out_raw", "wallet_delta_lamports"}``.
    """
    executor = executor or build_executor()
    amount_raw = int(amount_raw)
    if amount_raw <= 0:
        raise ExecPreBroadcastError("swap amount must be positive")

    _pre_broadcast_gate(executor, input_mint, output_mint, amount_raw)

    if callable(on_broadcast_submitted):
        on_broadcast_submitted()

    try:
        result = executor.swap(input_mint, output_mint, amount_raw)
    except Exception as exc:
        name = type(exc).__name__
        if name == "SolanaLivePostExecutionError":
            raise ExecPostLandError(str(exc), getattr(exc, "signature", "")) from exc
        # We already passed the pre-broadcast gate and signalled submission, so a
        # fresh failure here cannot be assumed to be pre-broadcast.
        raise ExecAmbiguousError(f"{name}: {exc}") from exc

    signature = str(result.get("signature") or "")
    out_raw = _amount_int(result.get("totalOutputAmount") or result.get("outputAmountResult"), 0)
    delta = result.get("wallet_delta_lamports")
    if not signature:
        raise ExecAmbiguousError("executor returned success without a signature")
    if int(min_out_raw) > 0 and 0 < out_raw < int(min_out_raw):
        # Landed but under the approved minimum: treat as needing reconciliation.
        raise ExecPostLandError(
            f"executed output {out_raw} below approved minimum {int(min_out_raw)}", signature
        )
    return {"signature": signature, "out_raw": out_raw, "wallet_delta_lamports": delta}


def preflight_funding(*, need_input_micro_usdc: int, executor=None) -> tuple[bool, str]:
    """Confirm USDC to spend and native SOL fee reserve are present for entry."""
    need = int(need_input_micro_usdc)
    if need <= 0:
        return False, "entry input must be positive"
    try:
        executor = executor or build_executor()
        usdc = int(executor.token_balance_raw(USDC_MINT))
        sol = int(executor.native_balance_lamports())
    except ExecConfigError as exc:
        return False, str(exc)
    except Exception as exc:  # pragma: no cover - env-specific
        return False, f"balance check failed: {type(exc).__name__}: {exc}"
    if usdc < need:
        return False, f"insufficient USDC: have {usdc} micro, need {need}"
    if sol < SOL_FEE_RESERVE_LAMPORTS:
        return False, f"insufficient SOL fee reserve: have {sol} lamports, need {SOL_FEE_RESERVE_LAMPORTS}"
    return True, "ok"


def preflight_exit_funding(*, approved_exit_input_lamports: int, executor=None) -> tuple[bool, str]:
    """Confirm the approved native-SOL position is spendable without using fee reserve.

    This is deliberately separate from the ledger check. The runner first proves
    that the exact quantity belongs to a still-open CONFIRMED Grok entry, then this
    function proves the wallet can currently spend that quantity plus the fixed
    native-SOL fee/rent reserve. A larger unrelated wallet balance never enlarges
    the approved exit amount.
    """
    need = int(approved_exit_input_lamports)
    if need <= 0:
        return False, "exit input must be positive"
    try:
        executor = executor or build_executor()
        sol = int(executor.native_balance_lamports())
    except ExecConfigError as exc:
        return False, str(exc)
    except Exception as exc:  # pragma: no cover - env-specific
        return False, f"exit balance check failed: {type(exc).__name__}: {exc}"
    required = need + SOL_FEE_RESERVE_LAMPORTS
    if sol < required:
        return False, (
            f"insufficient spendable SOL for approved exit: have {sol} lamports, "
            f"need position {need} + reserve {SOL_FEE_RESERVE_LAMPORTS}"
        )
    return True, "ok"
