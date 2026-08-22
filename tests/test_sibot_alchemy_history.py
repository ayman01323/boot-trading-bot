from types import SimpleNamespace

from learnerbot import sibot_alchemy_history_patch as patch


def _app(tmp_path, etherscan=""):
    return SimpleNamespace(csv_dir=tmp_path, etherscan_api_key=etherscan)


def test_full_alchemy_csv_url_is_primary_history_provider(tmp_path):
    (tmp_path / "rpc_endpoints.csv").write_text(
        "chain_id,name,url,ws_url,enabled,priority\n"
        "137,Alchemy,https://polygon-mainnet.g.alchemy.com/v2/private-key,wss://polygon-mainnet.g.alchemy.com/v2/private-key,true,1\n",
        encoding="utf-8",
    )
    app = _app(tmp_path)
    chain = SimpleNamespace(chain_id=137)
    assert patch.alchemy_rpc_url(app, 137) == "https://polygon-mainnet.g.alchemy.com/v2/private-key"
    assert patch.history_provider(app, chain) == "ALCHEMY"


def test_placeholder_alchemy_url_is_rejected_without_env_dependency(tmp_path):
    (tmp_path / "rpc_endpoints.csv").write_text(
        "chain_id,name,url,ws_url,enabled,priority\n"
        "8453,Alchemy,https://base-mainnet.g.alchemy.com/v2/${ALCHEMY_API_KEY},wss://base-mainnet.g.alchemy.com/v2/${ALCHEMY_API_KEY},true,1\n",
        encoding="utf-8",
    )
    app = _app(tmp_path)
    chain = SimpleNamespace(chain_id=8453)
    assert patch.alchemy_rpc_url(app, 8453) == ""
    assert patch.history_provider(app, chain) == "MISSING"


def test_etherscan_remains_optional_fallback(tmp_path):
    (tmp_path / "rpc_endpoints.csv").write_text(
        "chain_id,name,url,ws_url,enabled,priority\n",
        encoding="utf-8",
    )
    app = _app(tmp_path, etherscan="configured-but-not-exposed")
    chain = SimpleNamespace(chain_id=56)
    assert patch.history_provider(app, chain) == "ETHERSCAN"


def test_alchemy_erc20_transfer_normalises_to_sibot_shape():
    rows = [{
        "category": "erc20",
        "hash": "0xabc",
        "from": "0x0000000000000000000000000000000000000001",
        "to": "0x0000000000000000000000000000000000000002",
        "asset": "TEST",
        "value": 1.5,
        "metadata": {"blockTimestamp": "2026-08-22T20:00:00Z"},
        "rawContract": {
            "address": "0x0000000000000000000000000000000000000010",
            "value": "0x16bcc41e90000",
            "decimal": "0x12",
        },
    }]
    token, internal = patch._normalised_transfer_rows(rows)
    assert not internal
    assert len(token) == 1
    assert token[0]["contractAddress"] == "0x0000000000000000000000000000000000000010"
    assert token[0]["tokenSymbol"] == "TEST"
    assert token[0]["tokenDecimal"] == "18"
    assert int(token[0]["value"]) > 0
    assert int(token[0]["timeStamp"]) > 0
