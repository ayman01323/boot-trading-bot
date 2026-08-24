from pathlib import Path

from learnerbot.basic_engine_v0.csv_config import load_evm_v2_dry_run_settings
from learnerbot.basic_engine_v0.evm_v2_csv import load_atomic_v2_routes
from learnerbot.config import AppSettings


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _app(tmp_path: Path) -> AppSettings:
    return AppSettings(
        root=tmp_path,
        csv_dir=tmp_path,
        data_dir=tmp_path / "data",
        telegram_bot_token="",
        telegram_chat_ids=[],
        etherscan_api_key="",
    )


def test_csv_settings_use_global_defaults_and_chain_overrides(tmp_path):
    _write(
        tmp_path / "chains.csv",
        "chain_id,slug,name,type,enabled,explorer_url,native_symbol,wrapped_base_symbol,wrapped_base_address,finality_lag_blocks,scan_blocks_per_cycle\n"
        "8453,base,Base,EVM,true,,ETH,WETH,0x4200000000000000000000000000000000000006,3,10\n",
    )
    _write(
        tmp_path / "rpc_endpoints.csv",
        "chain_id,url,enabled,priority\n8453,https://rpc.example,true,1\n",
    )
    _write(
        tmp_path / "dex_registry.csv",
        "chain_id,dex_name,version,router,factory,enabled\n"
        "8453,PancakeSwap,V2,0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb,0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E,true\n",
    )
    _write(
        tmp_path / "basic_engine_v0_settings.csv",
        "chain_id,setting,value,description\n"
        "*,enabled,true,test\n"
        "*,input_amount_native,0.001,test\n"
        "*,min_net_profit_native,0.0001,test\n"
        "*,max_price_impact_bps,500,test\n"
        "8453,input_amount_native,0.002,chain override\n"
        "8453,v2_dex_name,PancakeSwap,test\n",
    )

    settings = load_evm_v2_dry_run_settings(_app(tmp_path), "base")
    assert settings.enabled is True
    assert str(settings.input_amount_native) == "0.002"
    assert str(settings.min_net_profit_native) == "0.0001"
    assert settings.router_address.lower() == "0x8cfe327cec66d1c090dd72bd0ff11d690c33a2eb"
    assert settings.rpc_url == "https://rpc.example"


def test_csv_routes_only_load_enabled_matching_chain(tmp_path):
    _write(
        tmp_path / "chains.csv",
        "chain_id,slug,name,type,enabled,explorer_url,native_symbol,wrapped_base_symbol,wrapped_base_address,finality_lag_blocks,scan_blocks_per_cycle\n"
        "8453,base,Base,EVM,true,,ETH,WETH,0x4200000000000000000000000000000000000006,3,10\n",
    )
    _write(
        tmp_path / "rpc_endpoints.csv",
        "chain_id,url,enabled,priority\n8453,https://rpc.example,true,1\n",
    )
    _write(
        tmp_path / "dex_registry.csv",
        "chain_id,dex_name,version,router,factory,enabled\n"
        "8453,PancakeSwap,V2,0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb,0x02a84c1b3BBD7401a5f7fa98a384EBC70bB5749E,true\n",
    )
    _write(
        tmp_path / "basic_engine_v0_settings.csv",
        "chain_id,setting,value,description\n*,enabled,true,test\n",
    )
    wrapped = "0x4200000000000000000000000000000000000006"
    token_a = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    token_b = "0x3055913c90Fcc1A6CE9a358911721eEb942013A1"
    _write(
        tmp_path / "basic_engine_v0_routes.csv",
        "chain_id,route_id,path,input_amount_native,priority,enabled,description\n"
        f"8453,on,{wrapped}>{token_a}>{token_b}>{wrapped},0.003,9,true,enabled\n"
        f"8453,off,{wrapped}>{token_b}>{token_a}>{wrapped},0.004,10,false,disabled\n"
        f"56,other,{wrapped}>{token_a}>{token_b}>{wrapped},0.005,11,true,other chain\n",
    )

    settings = load_evm_v2_dry_run_settings(_app(tmp_path), "base")
    candidates = load_atomic_v2_routes(tmp_path, settings).scan()
    assert len(candidates) == 1
    assert candidates[0].candidate_id == "atomic:base:on"
    assert str(candidates[0].payload["input_value"]) == "0.003"
