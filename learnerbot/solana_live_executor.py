from __future__ import annotations

import base64
import os
from decimal import Decimal

import requests
from solders.keypair import Keypair
from solders.message import to_bytes_versioned
from solders.signature import Signature
from solders.transaction import VersionedTransaction

from . import solana_sibot as _sol
from .solana_wallet_store import SolanaWalletStore

JUPITER_BASE = "https://api.jup.ag/swap/v2"


class SolanaLiveError(RuntimeError):
    pass


class SolanaLivePostExecutionError(SolanaLiveError):
    """A transaction landed/reported success but failed economic-result validation."""

    def __init__(self, message: str, result: dict | None = None):
        super().__init__(message)
        self.result = dict(result or {})
        self.signature = str(self.result.get("signature") or "")


def _amount_int(value, default=0) -> int:
    try:
        return int(str(value))
    except Exception:
        return int(default)


def sign_versioned_transaction(raw_transaction: bytes, keypair_bytes: bytes) -> bytes:
    """Sign exactly one-wallet-signer Jupiter v0 transaction without changing its message."""
    tx = VersionedTransaction.from_bytes(bytes(raw_transaction))
    kp = Keypair.from_bytes(bytes(keypair_bytes))
    message = tx.message
    keys = list(message.account_keys)
    required = int(message.header.num_required_signatures)
    signer_keys = keys[:required]
    matches = [i for i, key in enumerate(signer_keys) if key == kp.pubkey()]
    if len(matches) != 1:
        raise SolanaLiveError("Active Solana wallet is not the required transaction signer")
    # Canary path excludes JupiterZ/RFQ. Reject unexpected multi-signer orders rather
    # than risking an incomplete or incorrectly modified transaction.
    if required != 1:
        raise SolanaLiveError(f"LIVE canary refuses a transaction requiring {required} signers")
    sigs = list(tx.signatures)
    while len(sigs) < required:
        sigs.append(Signature.default())
    sigs[matches[0]] = kp.sign_message(to_bytes_versioned(message))
    tx.signatures = sigs
    return bytes(tx)


class SolanaLiveExecutor:
    def __init__(self, app, telegram_id):
        self.app = app
        self.telegram_id = str(telegram_id)
        self.store = SolanaWalletStore(app.csv_dir, app.data_dir)
        self.meta = self.store.get_meta(self.telegram_id)
        if not self.store.has_private_key(self.telegram_id, self.meta.get("wallet_id")):
            raise SolanaLiveError("Active Solana wallet has no encrypted signing key")
        self.address = str(self.meta.get("address") or "")
        self.keypair = self.store.keypair_bytes(self.telegram_id, self.meta.get("wallet_id"))

    def _headers(self, json_body=False):
        headers = {"User-Agent": "BOOT-SiBot-Solana-LIVE/1.1"}
        if json_body:
            headers["Content-Type"] = "application/json"
        api_key = os.getenv("JUPITER_API_KEY", "").strip()
        if api_key:
            headers["x-api-key"] = api_key
        return headers

    def native_balance_lamports(self) -> int:
        result = _sol._rpc(self.app, "getBalance", [self.address, {"commitment": "confirmed"}]) or {}
        return int(result.get("value") or 0)

    def token_balance_raw(self, mint: str) -> int:
        result = _sol._rpc(
            self.app,
            "getTokenAccountsByOwner",
            [self.address, {"mint": str(mint)}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
        ) or {}
        total = 0
        for row in result.get("value") or []:
            try:
                total += int(row["account"]["data"]["parsed"]["info"]["tokenAmount"]["amount"])
            except Exception:
                continue
        return total

    def _order(self, input_mint: str, output_mint: str, amount_raw: int) -> dict:
        if int(amount_raw) <= 0:
            raise SolanaLiveError("Swap amount must be positive")
        params = {
            "inputMint": str(input_mint),
            "outputMint": str(output_mint),
            "amount": str(int(amount_raw)),
            "taker": self.address,
            # Local simulation + single signer is mandatory for the first LIVE path.
            "excludeRouters": "jupiterz",
        }
        r = requests.get(f"{JUPITER_BASE}/order", params=params, headers=self._headers(), timeout=30)
        r.raise_for_status()
        order = r.json()
        if order.get("errorCode") or not order.get("transaction"):
            raise SolanaLiveError(f"Jupiter order failed: {order.get('errorMessage') or order.get('errorCode') or 'no transaction'}")
        if str(order.get("router") or "").lower() == "jupiterz":
            raise SolanaLiveError("JupiterZ/RFQ is disabled for the LIVE canary path")
        if not order.get("requestId"):
            raise SolanaLiveError("Jupiter order returned no requestId")
        # Reject an explicitly zero quote before signing/broadcasting. Missing fields
        # are tolerated because Jupiter response shapes can evolve; post-execute
        # validation below remains mandatory.
        for key in ("outAmount", "outputAmount", "estimatedOutputAmount"):
            if key in order and _amount_int(order.get(key), 0) <= 0:
                raise SolanaLiveError(f"Jupiter order has non-positive {key}")
        return order

    def _simulate(self, signed_b64: str) -> dict:
        result = _sol._rpc(
            self.app,
            "simulateTransaction",
            [signed_b64, {"encoding": "base64", "sigVerify": True, "commitment": "confirmed"}],
        ) or {}
        value = result.get("value") or {}
        if value.get("err") is not None:
            logs = value.get("logs") or []
            raise SolanaLiveError(f"Solana simulation failed: {value.get('err')} | {' | '.join(map(str, logs[-5:]))[:600]}")
        return value

    def swap(self, input_mint: str, output_mint: str, amount_raw: int) -> dict:
        # A pre-execution wallet snapshot is mandatory. This lets the caller account
        # for the real SOL cash movement rather than a tiny fixed fee estimate.
        wallet_before = self.native_balance_lamports()
        order = self._order(input_mint, output_mint, amount_raw)
        try:
            raw = base64.b64decode(order["transaction"], validate=True)
        except Exception as exc:
            raise SolanaLiveError("Jupiter returned an invalid base64 transaction") from exc
        signed_raw = sign_versioned_transaction(raw, self.keypair)
        signed_b64 = base64.b64encode(signed_raw).decode("ascii")
        simulation = self._simulate(signed_b64)
        body = {"signedTransaction": signed_b64, "requestId": str(order["requestId"])}
        if order.get("lastValidBlockHeight") is not None:
            body["lastValidBlockHeight"] = str(order["lastValidBlockHeight"])
        r = requests.post(f"{JUPITER_BASE}/execute", json=body, headers=self._headers(True), timeout=45)
        r.raise_for_status()
        result = r.json()
        if str(result.get("status") or "") != "Success" or int(result.get("code") or 0) != 0:
            raise SolanaLiveError(f"Jupiter execute failed: code={result.get('code')} {result.get('error') or result.get('status')}")
        if not result.get("signature"):
            raise SolanaLiveError("Jupiter reported success without a transaction signature")

        # Best-effort confirmed post-execution snapshot. A successful swap can still
        # be recorded if the second balance RPC is temporarily unavailable; the
        # result carries wallet_balance_reconciled=False and the P&L layer falls back
        # conservatively rather than losing the position record.
        wallet_after = None
        try:
            wallet_after = self.native_balance_lamports()
        except Exception:
            wallet_after = None
        wallet_delta = None if wallet_after is None else int(wallet_after) - int(wallet_before)
        combined = {
            **result,
            "order": order,
            "simulation": simulation,
            "wallet_before_lamports": int(wallet_before),
            "wallet_after_lamports": wallet_after,
            "wallet_delta_lamports": wallet_delta,
            "wallet_balance_reconciled": wallet_after is not None,
        }

        cfg = _sol.settings(self.app)
        require_output = _sol._bool(cfg.get("live_require_execute_output"), True)
        require_events = _sol._bool(cfg.get("live_require_swap_events"), True)
        input_result = _amount_int(result.get("totalInputAmount") or result.get("inputAmountResult"), 0)
        output_result = _amount_int(result.get("totalOutputAmount") or result.get("outputAmountResult"), 0)
        swap_events = result.get("swapEvents") or []

        problems = []
        if require_output and input_result <= 0:
            problems.append("non-positive executed input")
        if require_output and output_result <= 0:
            problems.append("non-positive executed output")
        if require_events and not swap_events:
            problems.append("missing swapEvents")
        if problems:
            raise SolanaLivePostExecutionError(
                "Jupiter transaction reported Success but failed economic validation: " + ", ".join(problems),
                combined,
            )
        return combined

    def buy(self, output_mint: str, amount_sol: Decimal, reserve_sol: Decimal) -> dict:
        amount_sol = Decimal(str(amount_sol))
        reserve_sol = Decimal(str(reserve_sol))
        lamports = int(amount_sol * Decimal(1_000_000_000))
        reserve = int(reserve_sol * Decimal(1_000_000_000))
        balance = self.native_balance_lamports()
        if balance < lamports + reserve:
            have = Decimal(balance) / Decimal(1_000_000_000)
            raise SolanaLiveError(f"Insufficient SOL: have {have:.9f}, need trade {amount_sol} + reserve {reserve_sol}")
        return self.swap(_sol.WSOL_MINT, output_mint, lamports)

    def sell(self, input_mint: str, amount_raw: int) -> dict:
        actual = self.token_balance_raw(input_mint)
        if actual < int(amount_raw):
            raise SolanaLiveError(f"Insufficient token balance for exit: have {actual}, need {int(amount_raw)}")
        return self.swap(input_mint, _sol.WSOL_MINT, int(amount_raw))
