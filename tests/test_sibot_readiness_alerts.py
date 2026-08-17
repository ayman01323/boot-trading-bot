from decimal import Decimal

from learnerbot import sibot_readiness_alert_patch as alerts


def test_buy_readiness_flags_low_gas_and_missing_native():
    issues = alerts._buy_readiness_issues(
        Decimal("0.003"), Decimal("0.005"), Decimal("0.001"), Decimal("0")
    )
    assert any(x.startswith("LOW_GAS:") for x in issues)
    assert any(x.startswith("MISSING_NATIVE:") for x in issues)


def test_buy_readiness_does_not_mislabel_exposure_cap_as_missing_assets():
    issues = alerts._buy_readiness_issues(
        Decimal("1"), Decimal("0.01"), Decimal("0.001"), Decimal("0")
    )
    assert issues == []


def test_exit_readiness_checks_both_gas_and_required_token_balance():
    issues = alerts._exit_readiness_issues(
        Decimal("0.002"), Decimal("0.005"), 700, 1000
    )
    assert any(x.startswith("LOW_GAS:") for x in issues)
    assert any(x.startswith("MISSING_TOKEN:") for x in issues)


def test_exit_readiness_passes_when_token_and_gas_are_available():
    assert alerts._exit_readiness_issues(
        Decimal("0.02"), Decimal("0.005"), 1200, 1000
    ) == []
