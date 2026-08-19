from __future__ import annotations

from decimal import Decimal

from web3 import Web3

from . import live_executor as _live

_ORIGINAL_TRANSFER_NATIVE = _live.LiveTrader.transfer_native


def transfer_native_with_destination(self, to_address: str, amount_native, confirm: str = "CONFIRM") -> dict:
    """Correct native transfers by explicitly binding the validated recipient.

    The previous implementation validated ``to_address`` but omitted ``tx['to']``
    from the transaction passed to gas estimation and signing.  Preserve every
    existing confirmation/balance/gas/audit guard while adding the recipient.
    """
    self._confirm(confirm)
    if not self.address:
        raise _live.LiveTradingError("No signing wallet")
    try:
        to = Web3.to_checksum_address(to_address)
    except Exception as exc:
        raise _live.LiveTradingError("Destination must be a valid 0x address") from exc

    amount = _live._dec(amount_native, "Transfer amount")
    try:
        reserve = Decimal(self.settings.get("min_native_gas_reserve", "0.005"))
    except Exception:
        reserve = Decimal("0.005")
    if self.native_balance() < amount + reserve:
        raise _live.LiveTradingError(
            f"Insufficient {self.chain.native_symbol}; keep {reserve} for gas"
        )

    tx = self._base_tx(value=int(amount * Decimal(10**18)))
    tx["to"] = to
    try:
        gas = int(self.w3.eth.estimate_gas(tx))
    except Exception:
        gas = 21000
    tx["gas"] = max(21000, int(gas * 1.10))
    txh = self._sign_send(tx)
    self._audit(
        "TRANSFER_NATIVE",
        to,
        self.chain.native_symbol,
        str(amount),
        str(amount),
        str(amount),
        txh,
        "BROADCAST",
    )
    return {
        "tx_hash": txh,
        "amount": amount,
        "to": to,
        "explorer": f"{self.chain.explorer_url}/tx/{txh}",
    }


def install() -> None:
    if getattr(_live.LiveTrader, "_native_transfer_destination_hotfix", False):
        return
    _live.LiveTrader.transfer_native = transfer_native_with_destination
    _live.LiveTrader._native_transfer_destination_hotfix = True


install()
