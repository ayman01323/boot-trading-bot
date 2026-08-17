from __future__ import annotations

import base64
import time
from decimal import Decimal

from solders.hash import Hash
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from solders.transaction import VersionedTransaction

from . import solana_sibot as _sol
from .solana_wallet_store import SolanaWalletStore, validate_solana_address


class SolanaManualTransferError(RuntimeError):
    pass


def _dec(value, default="0") -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(str(default))


def _config(app) -> dict:
    cfg = _sol.settings(app)
    return {
        "min_reserve_sol": max(Decimal("0.005"), _dec(cfg.get("live_min_sol_reserve"), ".02")),
        "max_transfer_sol": max(Decimal("0.001"), _dec(cfg.get("manual_transfer_max_sol"), "1")),
        "require_simulation": str(cfg.get("manual_transfer_require_simulation") or "true").strip().lower() in {"1", "true", "yes", "on"},
    }


def prepare_native_transfer(app, telegram_id, destination: str, lamports: int) -> dict:
    """Validate a proposed manual SOL transfer without broadcasting it."""
    tid = str(telegram_id)
    destination = validate_solana_address(destination)
    lamports = int(lamports)
    if lamports <= 0:
        raise SolanaManualTransferError("Transfer amount must be positive")

    store = SolanaWalletStore(app.csv_dir, app.data_dir)
    meta = store.get_meta(tid)
    if not store.has_private_key(tid, meta.get("wallet_id")):
        raise SolanaManualTransferError("Active Solana wallet is not SIGNING READY")
    sender = str(meta.get("address") or "")
    if destination == sender:
        raise SolanaManualTransferError("Destination cannot be the active sending wallet")

    cfg = _config(app)
    amount_sol = Decimal(lamports) / Decimal(1_000_000_000)
    if amount_sol > cfg["max_transfer_sol"]:
        raise SolanaManualTransferError(
            f"Transfer exceeds the configured manual limit of {cfg['max_transfer_sol']} SOL"
        )

    result = _sol._rpc(app, "getBalance", [sender, {"commitment": "confirmed"}]) or {}
    balance_lamports = int(result.get("value") or 0)
    reserve_lamports = int(cfg["min_reserve_sol"] * Decimal(1_000_000_000))
    if balance_lamports < lamports + reserve_lamports:
        have = Decimal(balance_lamports) / Decimal(1_000_000_000)
        raise SolanaManualTransferError(
            f"Insufficient SOL: have {have:.9f}; transfer {amount_sol:.9f} plus reserve {cfg['min_reserve_sol']:.9f}"
        )
    return {
        "sender": sender,
        "destination": destination,
        "lamports": lamports,
        "amount_sol": amount_sol,
        "balance_lamports": balance_lamports,
        "balance_sol": Decimal(balance_lamports) / Decimal(1_000_000_000),
        "reserve_sol": cfg["min_reserve_sol"],
        "require_simulation": cfg["require_simulation"],
        "wallet_id": str(meta.get("wallet_id") or ""),
    }


def broadcast_native_transfer(app, telegram_id, destination: str, lamports: int) -> dict:
    """Sign and submit one already user-confirmed native SOL transfer."""
    prepared = prepare_native_transfer(app, telegram_id, destination, lamports)
    store = SolanaWalletStore(app.csv_dir, app.data_dir)
    keypair_bytes = store.keypair_bytes(str(telegram_id), prepared["wallet_id"])
    kp = Keypair.from_bytes(bytes(keypair_bytes))
    if str(kp.pubkey()) != prepared["sender"]:
        raise SolanaManualTransferError("Encrypted signer does not match the active Solana wallet")

    block = _sol._rpc(app, "getLatestBlockhash", [{"commitment": "confirmed"}]) or {}
    value = block.get("value") or {}
    blockhash_text = str(value.get("blockhash") or "")
    if not blockhash_text:
        raise SolanaManualTransferError("Solana RPC returned no recent blockhash")

    try:
        receiver = Pubkey.from_string(prepared["destination"])
        recent_blockhash = Hash.from_string(blockhash_text)
        ix = transfer(TransferParams(from_pubkey=kp.pubkey(), to_pubkey=receiver, lamports=int(prepared["lamports"])))
        msg = MessageV0.try_compile(kp.pubkey(), [ix], [], recent_blockhash)
        tx = VersionedTransaction(msg, [kp])
        raw = bytes(tx)
    except Exception as exc:
        raise SolanaManualTransferError(f"Could not build Solana transfer transaction: {type(exc).__name__}") from exc

    signed_b64 = base64.b64encode(raw).decode("ascii")
    simulation = None
    if prepared["require_simulation"]:
        sim = _sol._rpc(
            app,
            "simulateTransaction",
            [signed_b64, {"encoding": "base64", "sigVerify": True, "commitment": "confirmed"}],
        ) or {}
        simulation = sim.get("value") or {}
        if simulation.get("err") is not None:
            logs = simulation.get("logs") or []
            raise SolanaManualTransferError(
                f"Solana simulation failed: {simulation.get('err')} | {' | '.join(map(str, logs[-4:]))[:500]}"
            )

    signature = _sol._rpc(
        app,
        "sendTransaction",
        [
            signed_b64,
            {
                "encoding": "base64",
                "skipPreflight": False,
                "preflightCommitment": "confirmed",
                "maxRetries": 5,
            },
        ],
    )
    signature = str(signature or "")
    if not signature:
        raise SolanaManualTransferError("Solana RPC returned no transaction signature")

    status = "SUBMITTED"
    err = None
    for _ in range(8):
        time.sleep(0.5)
        try:
            result = _sol._rpc(app, "getSignatureStatuses", [[signature], {"searchTransactionHistory": True}]) or {}
            rows = result.get("value") or []
            row = rows[0] if rows else None
            if not row:
                continue
            err = row.get("err")
            confirmation = str(row.get("confirmationStatus") or "")
            if err is not None:
                status = "FAILED"
                break
            if confirmation in {"confirmed", "finalized"}:
                status = confirmation.upper()
                break
        except Exception:
            break

    return {
        **prepared,
        "signature": signature,
        "status": status,
        "err": err,
        "simulation": simulation,
    }
