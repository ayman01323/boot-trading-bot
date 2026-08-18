from __future__ import annotations

import time
from pathlib import Path

from . import cli as _cli
from .config import load_chains
from .user_registry import get_user, join_user, set_user_setting, update_user

TARGET_TELEGRAM_ID = "6760898817"
MARKER = ".telegram_6760898817_full_live_20260818_v1"
_PREV_APP = _cli._app


def _set(app, setting: str, value: str, *, chain_id="*", description="") -> None:
    set_user_setting(
        app.csv_dir,
        TARGET_TELEGRAM_ID,
        setting,
        value,
        chain_id=str(chain_id),
        description=description,
    )


def _arm_full_live(app) -> None:
    marker = Path(app.data_dir) / MARKER
    if marker.exists():
        return

    row = get_user(app.csv_dir, TARGET_TELEGRAM_ID)
    if row is None:
        join_user(app.csv_dir, TARGET_TELEGRAM_ID, "MASTER")
        row = get_user(app.csv_dir, TARGET_TELEGRAM_ID)

    # This change is deliberately scoped to this one Telegram account. It does
    # not alter the platform-wide emergency AUTO/LIVE gates or any other user.
    updates = {
        "role": "MASTER",
        "status": "ACTIVE",
        "allowed_chains": "*",
        "can_manual_trade": "true",
        "can_auto_trade": "true",
        "activated_epoch": int(time.time()),
        "notes": "Explicitly armed for full LIVE trading by owner request on 2026-08-18",
    }
    if not str((row or {}).get("fee_plan_id") or "").strip():
        updates["fee_plan_id"] = "MASTER"
    update_user(app.csv_dir, TARGET_TELEGRAM_ID, **updates)

    # Global account defaults. ARMED is required by the direct AUTO executor;
    # SiBot has its own enable/auto switches.
    global_settings = {
        "auto_trading_enabled": ("true", "User automatic execution enabled"),
        "live_trading_enabled": ("true", "User real-money signing enabled"),
        "recommendation_mode": ("ARMED", "User execution mode explicitly armed"),
        "sibot_enabled": ("true", "SiBot monitoring enabled"),
        "sibot_auto_trade_enabled": ("true", "SiBot real-money copy execution enabled"),
    }
    for setting, (value, description) in global_settings.items():
        _set(app, setting, value, chain_id="*", description=description)

    # Existing chain-specific OFF rows override global settings. Explicitly arm
    # every configured EVM chain so stale per-chain overrides cannot keep this
    # account in SHADOW/OFF. Platform gates, route approval, simulation, profit,
    # gas, cooldown, capital and signing checks remain mandatory at execution.
    evm_chain_ids = []
    for chain in load_chains(app, enabled_only=False):
        if str(getattr(chain, "type", "EVM") or "EVM").upper() != "EVM":
            continue
        cid = int(chain.chain_id)
        evm_chain_ids.append(cid)
        _set(app, "auto_trading_enabled", "true", chain_id=cid, description="User automatic execution enabled")
        _set(app, "live_trading_enabled", "true", chain_id=cid, description="User real-money signing enabled")
        _set(app, "recommendation_mode", "ARMED", chain_id=cid, description="User execution mode explicitly armed")
        _set(app, "sibot_enabled", "true", chain_id=cid, description="SiBot monitoring enabled")
        _set(app, "sibot_auto_trade_enabled", "true", chain_id=cid, description="SiBot real-money copy execution enabled")

    # Solana LIVE is a separate per-user gate. This authorises real execution,
    # while the executor still requires a signing-ready wallet, sufficient SOL,
    # reserve, qualifying leader signal and successful transaction simulation.
    _set(
        app,
        "solana_live_enabled",
        "true",
        chain_id=-101,
        description="Solana real-money automatic execution explicitly armed",
    )

    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        "\n".join(
            [
                f"applied_epoch={int(time.time())}",
                f"telegram_id={TARGET_TELEGRAM_ID}",
                "evm_auto=true",
                "evm_live=true",
                "recommendation_mode=ARMED",
                "sibot_enabled=true",
                "sibot_auto_trade_enabled=true",
                "solana_live_enabled=true",
                "evm_chain_ids=" + ",".join(str(x) for x in evm_chain_ids),
                "platform_gates_unchanged=true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"[telegram-full-live] tid={TARGET_TELEGRAM_ID} active=true auto=true live=true "
        f"armed=true sibot=true sibot_auto=true solana_live=true evm_chains={evm_chain_ids} "
        "platform_gates_unchanged=true"
    )


def _app_with_676_full_live():
    app = _PREV_APP()
    try:
        _arm_full_live(app)
    except Exception as exc:
        print(f"[telegram-full-live] ERROR {type(exc).__name__}: {exc}")
    return app


_cli._app = _app_with_676_full_live
