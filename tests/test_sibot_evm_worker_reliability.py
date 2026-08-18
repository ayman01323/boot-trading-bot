from types import SimpleNamespace

from learnerbot import sibot as sibot
from learnerbot import sibot_evm_worker_reliability_patch as reliability


def _app(tmp_path):
    return SimpleNamespace(csv_dir=tmp_path / "CSVbot", data_dir=tmp_path / "data")


def test_evm_leader_receipt_failure_does_not_advance_cursor(monkeypatch, tmp_path):
    app = _app(tmp_path)
    chain = SimpleNamespace(chain_id=1, slug="test")
    with sibot.connect(app) as conn:
        sibot._set_state(conn, "leader_last_block:1", 4)

    class Eth:
        block_number = 6
        def get_block(self, number, full_transactions=True):
            return {
                "timestamp": 100,
                "transactions": [
                    {"from": "0xabc", "to": "0xrouter", "hash": "0xtx", "value": 1}
                ],
            }
        def get_transaction_receipt(self, tx_hash):
            raise RuntimeError("temporary receipt RPC failure")

    w3 = SimpleNamespace(eth=Eth())
    monkeypatch.setattr(reliability._sibot, "_leader_set", lambda app, cid: {"0xabc": 0})
    monkeypatch.setattr(reliability._sibot, "_rpc", lambda chain: w3)
    monkeypatch.setattr(reliability._sibot, "_routers", lambda app, chain: {"0xrouter"})

    assert reliability.poll_leader_blocks_reliable(app, chain) == []
    with sibot.connect(app) as conn:
        assert int(sibot._state(conn, "leader_last_block:1", 0)) == 4


def test_evm_successful_block_advances_cursor(monkeypatch, tmp_path):
    app = _app(tmp_path)
    chain = SimpleNamespace(chain_id=1, slug="test")
    with sibot.connect(app) as conn:
        sibot._set_state(conn, "leader_last_block:1", 4)

    class Eth:
        block_number = 5
        def get_block(self, number, full_transactions=True):
            return {"timestamp": 100, "transactions": []}

    w3 = SimpleNamespace(eth=Eth())
    monkeypatch.setattr(reliability._sibot, "_leader_set", lambda app, cid: {"0xabc": 0})
    monkeypatch.setattr(reliability._sibot, "_rpc", lambda chain: w3)
    monkeypatch.setattr(reliability._sibot, "_routers", lambda app, chain: set())

    reliability.poll_leader_blocks_reliable(app, chain)
    with sibot.connect(app) as conn:
        assert int(sibot._state(conn, "leader_last_block:1", 0)) == 5
