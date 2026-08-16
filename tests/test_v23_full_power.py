from pathlib import Path
import csv

ROOT=Path(__file__).resolve().parents[1]

def rows(name):
    with (ROOT/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def test_v3_path_encoding_shape():
    from learnerbot.full_power_scanner import encode_v3_path
    a='0x'+'11'*20;b='0x'+'22'*20;c='0x'+'33'*20
    p=encode_v3_path([a,b,c],[500,2500])
    assert len(p)==20+3+20+3+20
    assert p[:20]==bytes.fromhex('11'*20)
    assert int.from_bytes(p[20:23],'big')==500
    assert int.from_bytes(p[43:46],'big')==2500

def test_live_headers_have_full_power_route_fields():
    from learnerbot.route_scanner import LIVE_HEADERS
    for k in ['route_kind','protocol','quoter_address','route_fees','venue_plan','execution_mode']:
        assert k in LIVE_HEADERS

def test_pancake_v3_config_four_chains_live():
    rr=rows('CSVbot/dex_registry.csv')
    v3=[r for r in rr if r['version']=='V3' and r['dex_name']=='PancakeSwap']
    assert {int(r['chain_id']) for r in v3}=={56,8453,1,42161}
    assert all(r['factory'].lower()=='0x0bfbcf9fa4f9c56b0f40a671ad40e0805a091865' for r in v3)
    assert all(r['router'].lower()=='0x1b81d678ffb9c0263b24a97847620c99d213eb14' for r in v3)
    assert all(r['quoter'].lower()=='0xb048bbc1ee6b733fffcfb9e9cef7375518e25997' for r in v3)
    assert all(r['auto_execute'].lower()=='true' for r in v3)

def test_polygon_algebra_is_not_silently_live_as_uni_v3():
    rr=rows('CSVbot/dex_registry.csv')
    q=[r for r in rr if r['chain_id']=='137' and r['version']=='V3_ALGEBRA']
    assert len(q)==1 and q[0]['auto_execute'].lower()=='false'

def test_second_v2_venues_for_cross_dex_are_shadow_only():
    rr=rows('CSVbot/dex_registry.csv')
    assert any(r['chain_id']=='8453' and r['dex_name']=='QuickSwap' and r['version']=='V2' and r['auto_execute'].lower()=='false' for r in rr)
    assert any(r['chain_id']=='1' and r['dex_name']=='Uniswap' and r['version']=='V2' and r['auto_execute'].lower()=='false' for r in rr)

def test_hot_path_is_parallel_and_uses_full_power_scanner():
    fp=(ROOT/'learnerbot/fast_market.py').read_text()
    ps=(ROOT/'learnerbot/full_power_scanner.py').read_text()
    assert 'scan_full_power_hot_routes' in fp
    assert 'ThreadPoolExecutor' in ps
    assert '_scan_v2_hot_chain' in ps and '_scan_v3_chain' in ps and '_scan_cross_v2_chain' in ps

def test_discovery_is_separate_thread():
    c=(ROOT/'learnerbot/cli.py').read_text();d=(ROOT/'learnerbot/power_discovery.py').read_text()
    assert 'start_power_discovery_thread(app)' in c
    assert 'full_power_discovery_interval_seconds' in d

def test_v3_live_has_wallet_sim_and_final_eth_call_before_sign():
    s=(ROOT/'learnerbot/live_executor.py').read_text()
    assert 'def simulate_v3_cycle' in s
    assert 'def _prebroadcast_v3_cycle' in s
    assert 'AUTO_PREFLIGHT_V3' in s
    pre=s.index('def _prebroadcast_v3_cycle')
    call=s.index('self.w3.eth.call(call_tx)',pre)
    sign=s.index('txh=self._sign_send(built)',call)
    assert call < sign

def test_pancake_v3_swaprouter_exactinput_abi_has_deadline_field():
    from learnerbot.live_executor import V3_ROUTER_ABI
    comps=V3_ROUTER_ABI[0]["inputs"][0]["components"]
    assert [x["name"] for x in comps] == ["path","recipient","deadline","amountIn","amountOutMinimum"]
    s=(ROOT/"learnerbot/live_executor.py").read_text()
    assert 'self._deadline(),q["amount_in_raw"]' in s
    assert 'self._deadline(),sim["amount_in_raw"]' in s

def test_auto_trader_dispatches_v3_and_refuses_cross_shadow():
    s=(ROOT/'learnerbot/auto_trader.py').read_text()
    assert 'simulate_v3_cycle' in s and 'execute_v3_cycle' in s
    assert 'route_kind.startswith("CROSS_")' in s

def test_cross_dex_rows_fail_closed():
    s=(ROOT/'learnerbot/full_power_scanner.py').read_text()
    assert "'route_kind':'CROSS_DEX_V2'" in s
    assert "'execution_mode':'SHADOW_ATOMIC_EXECUTOR_REQUIRED'" in s
    assert "'enabled':'false'" in s
    assert 'NO_SEQUENTIAL_EOA_EXECUTION' in s

def test_autoprep_exact_approves_live_v2_v3_routers():
    s=(ROOT/'learnerbot/live_executor.py').read_text()
    assert 'def approve_wrapped_cap_for' in s
    assert 'def _auto_execution_routers' in s
    assert 'if current>0:self._send_approval(c,0,spender)' in s
    assert 'approve_wrapped_cap_for(spender,amount' in s

def test_atomic_executor_source_has_owner_router_and_profit_guards():
    s=(ROOT/'contracts/AtomicV2ArbExecutor.sol').read_text()
    assert 'modifier onlyOwner()' in s
    assert 'allowedRouter' in s
    assert 'MIN_PROFIT' in s
    assert 'nonReentrant' in s

def test_master_gates_are_explicit_booleans():
    """Runtime deployment tests must not require LIVE/AUTO to be OFF.

    The production CSV is intentionally hot-reloaded and an operator may have the
    gates ON during a controlled live session. Validate that the safety gates are
    present and explicit booleans rather than asserting a particular runtime state.
    """
    auto={r['setting']:r['value'] for r in rows('CSVbot/auto_trading_settings.csv') if r['chain_id']=='*'}
    live={r['setting']:r['value'] for r in rows('CSVbot/live_trading_settings.csv') if r['chain_id']=='*'}
    assert auto['auto_trading_enabled'].lower() in {'true','false'}
    assert live['trading_enabled'].lower() in {'true','false'}

def test_full_power_defaults():
    auto={r['setting']:r['value'] for r in rows('CSVbot/auto_trading_settings.csv') if r['chain_id']=='*'}
    assert auto['full_power_enabled']=='true'
    assert auto['v3_scanner_enabled']=='true'
    assert auto['fast_market_interval_seconds']=='5'
    assert auto['fast_market_pairs_per_dex_pass']=='0'
    assert auto['full_power_parallel_chains']=='5'
