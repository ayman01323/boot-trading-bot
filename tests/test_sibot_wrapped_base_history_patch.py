import subprocess
import sys
import textwrap


def _run(code: str):
    p = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert p.returncode == 0, p.stdout + "\n" + p.stderr


def test_wrapped_base_buy_and_sell_reconstructs_closed_trade():
    _run(r'''
        from decimal import Decimal
        from learnerbot import sibot_wrapped_base_history_patch as p

        wallet="0x"+"1"*40
        router="0x"+"2"*40
        token="0x"+"3"*40
        weth=p._WRAPPED_BASE[1]
        normal=[
            {"hash":"buy","from":wallet,"to":router,"value":"0","timeStamp":"100","gasUsed":"21000","gasPrice":"1000000000","isError":"0","txreceipt_status":"1"},
            {"hash":"sell","from":wallet,"to":router,"value":"0","timeStamp":"200","gasUsed":"21000","gasPrice":"1000000000","isError":"0","txreceipt_status":"1"},
        ]
        erc20=[
            {"hash":"buy","contractAddress":weth,"from":wallet,"to":router,"value":str(10**18),"tokenSymbol":"WETH","tokenDecimal":"18"},
            {"hash":"buy","contractAddress":token,"from":router,"to":wallet,"value":str(100*10**18),"tokenSymbol":"TKN","tokenDecimal":"18"},
            {"hash":"sell","contractAddress":token,"from":wallet,"to":router,"value":str(100*10**18),"tokenSymbol":"TKN","tokenDecimal":"18"},
            {"hash":"sell","contractAddress":weth,"from":router,"to":wallet,"value":str(12*10**17),"tokenSymbol":"WETH","tokenDecimal":"18"},
        ]
        trades, unmatched=p.reconstruct_spot_trades(wallet,{router},normal,erc20,[],1,"ethereum")
        assert unmatched==0
        assert len(trades)==1
        t=trades[0]
        assert t["source"]=="WRAPPED_BASE_DIRECT_FIFO"
        assert Decimal(t["proceeds_native"])==Decimal("1.2")
        assert Decimal(t["cost_native"]) > Decimal("1")
        assert Decimal(t["net_native"]) > Decimal("0.19")
    ''')


def test_native_buy_can_close_into_wrapped_base_without_losing_fifo_history():
    _run(r'''
        from learnerbot import sibot_wrapped_base_history_patch as p
        wallet="0x"+"1"*40; router="0x"+"2"*40; token="0x"+"3"*40; weth=p._WRAPPED_BASE[1]
        normal=[
            {"hash":"buy","from":wallet,"to":router,"value":str(10**18),"timeStamp":"100","gasUsed":"0","gasPrice":"0","isError":"0","txreceipt_status":"1"},
            {"hash":"sell","from":wallet,"to":router,"value":"0","timeStamp":"200","gasUsed":"0","gasPrice":"0","isError":"0","txreceipt_status":"1"},
        ]
        erc20=[
            {"hash":"buy","contractAddress":token,"from":router,"to":wallet,"value":"1000","tokenSymbol":"TKN","tokenDecimal":"0"},
            {"hash":"sell","contractAddress":token,"from":wallet,"to":router,"value":"1000","tokenSymbol":"TKN","tokenDecimal":"0"},
            {"hash":"sell","contractAddress":weth,"from":router,"to":wallet,"value":str(11*10**17),"tokenSymbol":"WETH","tokenDecimal":"18"},
        ]
        trades, unmatched=p.reconstruct_spot_trades(wallet,{router},normal,erc20,[],1,"ethereum")
        assert unmatched==0 and len(trades)==1
        assert trades[0]["buy_tx"]=="buy" and trades[0]["sell_tx"]=="sell"
        assert trades[0]["source"]=="WRAPPED_BASE_DIRECT_FIFO"
    ''')


def test_wrapped_base_reconstruction_keeps_router_filter_fail_closed():
    _run(r'''
        from learnerbot import sibot_wrapped_base_history_patch as p
        wallet="0x"+"1"*40; configured="0x"+"2"*40; other="0x"+"4"*40; token="0x"+"3"*40; weth=p._WRAPPED_BASE[1]
        normal=[{"hash":"buy","from":wallet,"to":other,"value":"0","timeStamp":"100","gasUsed":"0","gasPrice":"0","isError":"0","txreceipt_status":"1"}]
        erc20=[
            {"hash":"buy","contractAddress":weth,"from":wallet,"to":other,"value":str(10**18),"tokenSymbol":"WETH","tokenDecimal":"18"},
            {"hash":"buy","contractAddress":token,"from":other,"to":wallet,"value":"1000","tokenSymbol":"TKN","tokenDecimal":"0"},
        ]
        trades, unmatched=p.reconstruct_spot_trades(wallet,{configured},normal,erc20,[],1,"ethereum")
        assert trades==[] and unmatched==0
    ''')
