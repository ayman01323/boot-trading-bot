from decimal import Decimal

from learnerbot.sibot import reconstruct_spot_trades


def test_sibot_fifo_reconstructs_direct_native_spot_profit():
    wallet = "0x1111111111111111111111111111111111111111"
    router = "0x2222222222222222222222222222222222222222"
    token = "0x3333333333333333333333333333333333333333"
    normal = [
        {"hash":"0xbuy","from":wallet,"to":router,"isError":"0","txreceipt_status":"1","timeStamp":"1000","value":str(10**18),"gasUsed":"21000","gasPrice":str(10**9)},
        {"hash":"0xsell","from":wallet,"to":router,"isError":"0","txreceipt_status":"1","timeStamp":"2000","value":"0","gasUsed":"21000","gasPrice":str(10**9)},
    ]
    token_rows = [
        {"hash":"0xbuy","contractAddress":token,"from":router,"to":wallet,"value":str(100*10**18),"tokenDecimal":"18","tokenSymbol":"TEST"},
        {"hash":"0xsell","contractAddress":token,"from":wallet,"to":router,"value":str(100*10**18),"tokenDecimal":"18","tokenSymbol":"TEST"},
    ]
    internal = [{"hash":"0xsell","to":wallet,"from":router,"value":str(int(1.2*10**18)),"isError":"0","timeStamp":"2000"}]
    trades, unmatched = reconstruct_spot_trades(wallet,{router.lower()},normal,token_rows,internal,56,"bsc")
    assert unmatched == 0
    assert len(trades) == 1
    assert trades[0]["symbol"] == "TEST"
    assert Decimal(trades[0]["net_native"]) > Decimal("0.19")


def test_sibot_fifo_marks_unmatched_sell_provisional():
    wallet = "0x1111111111111111111111111111111111111111"
    router = "0x2222222222222222222222222222222222222222"
    token = "0x3333333333333333333333333333333333333333"
    normal = [{"hash":"0xsell","from":wallet,"to":router,"isError":"0","txreceipt_status":"1","timeStamp":"2000","value":"0","gasUsed":"21000","gasPrice":str(10**9)}]
    token_rows = [{"hash":"0xsell","contractAddress":token,"from":wallet,"to":router,"value":str(10**18),"tokenDecimal":"18","tokenSymbol":"TEST"}]
    internal = [{"hash":"0xsell","to":wallet,"from":router,"value":str(2*10**18),"isError":"0","timeStamp":"2000"}]
    trades, unmatched = reconstruct_spot_trades(wallet,{router.lower()},normal,token_rows,internal,56,"bsc")
    assert trades == []
    assert unmatched == 1
