from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_trade_lookup_workflow_is_bounded_and_self_hosted() -> None:
    text = _text(".github/workflows/deepseek-trade-lookup.yml")
    assert "DeepSeek Bounded Trade Lookup" in text
    assert "runs-on: [self-hosted, linux, x64, boot-vps]" in text
    assert "python3.11 scripts/deepseek_trade_lookup.py" in text
    assert "trades/deepseek/latest.json" in text
    assert "deepseek-v4-flash" in text
    assert "persist-credentials: false" in text
    assert "/var/tmp/boot/deepseek_trade_lookup_request.json" in text
    assert "lookup_type" in text and "identifier" in text
    for forbidden in (
        "sudo bash", "sudo sh", "sudo -i", "gh pr merge", "git push origin main",
        "PRIVATE_KEY", "wallet/private", "eval ",
    ):
        assert forbidden not in text


def test_runtime_lookup_uses_read_only_fixed_sources_and_parameterised_sql() -> None:
    text = _text("scripts/deepseek_trade_lookup.py")
    assert 'RUNTIME_ROOT = Path("/root/multichain-learning-bot-v2.2-fast-direct-market")' in text
    assert 'mode=ro' in text
    assert 'PRAGMA query_only=ON' in text
    assert 'trade_provenance.sqlite3' in text
    assert 'sibot.sqlite3' in text
    assert 'solana_sibot.sqlite3' in text
    assert 'telegram_id=?' in text
    assert 'position_id=?' in text
    assert 'event_id=? OR tx_hash=?' in text
    assert 'csv.DictReader' in text
    for forbidden in ("subprocess", "os.system", "eval(", "exec(", "input("):
        assert forbidden not in text


def test_master_telegram_uses_buttons_and_bounded_request_file() -> None:
    text = _text("learnerbot/telegram_deepseek_trade_lookup_patch.py")
    assert "🔍 My latest trade" in text
    assert "🔎 Exact trade/position" in text
    assert "_menu._is_master" in text
    assert "/var/tmp/boot/deepseek_trade_lookup_request.json" in text
    assert '"account"' in text and '"exact"' in text
    assert "No paths, shell commands or SQL are accepted" in text
    assert "request_nonce" in text
    for forbidden in ("subprocess", "os.system", "sudo ", "PRIVATE_KEY"):
        assert forbidden not in text


def test_command_scope_loads_trade_lookup_after_deepseek_control() -> None:
    text = _text("learnerbot/telegram_command_scope_patch.py")
    base = text.index("from . import telegram_deepseek_control_patch")
    trade = text.index("from . import telegram_deepseek_trade_lookup_patch")
    assert trade > base
