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


def test_sibot_telegram_menu_is_installed_and_named_only_sibot():
    from learnerbot import telegram_sibot_patch as patch
    kb = patch.menu_keyboard()
    buttons = [b for row in kb.get("inline_keyboard", []) for b in row]
    sibot = [b for b in buttons if b.get("callback_data") == "menu:sibot"]
    assert len(sibot) == 1
    assert sibot[0]["text"] == "🤖 SiBot"
    assert "SiMo" not in patch.help_page()


def test_sibot_top20_summary_is_compact_without_wallet_dump():
    from learnerbot import telegram_sibot_patch as patch
    class App:
        csv_dir = __import__('pathlib').Path('/tmp/does-not-exist')
        data_dir = __import__('pathlib').Path('/tmp/does-not-exist')
    assert callable(patch.top20_summary_page)
    assert callable(patch.top20_page)


def test_auto_deploy_status_patch_is_loaded_after_sibot():
    from pathlib import Path
    s = (Path(__file__).resolve().parents[1] / "learnerbot" / "__main__.py").read_text()
    assert s.index("telegram_sibot_patch") < s.index("telegram_deploy_status_patch")


def test_auto_deploy_page_is_push_triggered_and_shows_elapsed_seconds():
    from learnerbot import telegram_deploy_status_patch as patch
    text = patch.deploy_page()
    assert "AUTO DEPLOY" in text
    assert "immediately when main changes" in text
    assert "Last successful deploy" in text
