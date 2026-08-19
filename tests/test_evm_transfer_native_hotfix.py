from decimal import Decimal
from types import SimpleNamespace

from web3 import Web3

from learnerbot import evm_transfer_native_hotfix_patch  # noqa: F401
from learnerbot.live_executor import LiveTrader


def test_transfer_native_binds_validated_destination_before_gas_and_signing():
    trader = LiveTrader.__new__(LiveTrader)
    trader.address = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
    trader.settings = {"min_native_gas_reserve": "0.005"}
    trader.chain = SimpleNamespace(native_symbol="ETH", explorer_url="https://example.invalid")
    trader._confirm = lambda confirm: None
    trader.native_balance = lambda: Decimal("1")
    trader._base_tx = lambda value=0: {"from": trader.address, "value": value, "nonce": 7}

    observed = {}

    class Eth:
        @staticmethod
        def estimate_gas(tx):
            observed["estimate"] = dict(tx)
            return 21000

    trader.w3 = SimpleNamespace(eth=Eth())

    def sign_send(tx):
        observed["signed"] = dict(tx)
        return "0xabc"

    trader._sign_send = sign_send
    trader._audit = lambda *args, **kwargs: None

    destination = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
    result = trader.transfer_native(destination, "0.01", "CONFIRM")

    assert observed["estimate"]["to"] == destination
    assert observed["signed"]["to"] == destination
    assert observed["signed"]["value"] == 10**16
    assert observed["signed"]["gas"] >= 21000
    assert result["to"] == destination
    assert result["tx_hash"] == "0xabc"
