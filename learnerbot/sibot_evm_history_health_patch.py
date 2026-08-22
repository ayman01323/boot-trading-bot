from __future__ import annotations

import time
from contextlib import closing

from . import sibot as _sibot

_PREV_REFRESH = _sibot.refresh_wallet_history


def _mark(app, key: str, value) -> None:
    try:
        with _sibot._DB_LOCK, closing(_sibot.connect(app)) as conn:
            _sibot._set_state(conn, key, value)
    except Exception:
        pass


def _clean_error(value: object) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").split())
    return text[:500]


def refresh_wallet_history_with_health(app, chain, wallet: str) -> dict:
    now = int(time.time())
    _mark(app, "worker:history:last_run", now)
    _mark(app, f"worker:history:{int(chain.chain_id)}:last_run", now)
    try:
        result = _PREV_REFRESH(app, chain, wallet)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {_clean_error(exc)}"
        _mark(app, "worker:history:last_error", reason)
        _mark(app, "worker:history:last_error_epoch", now)
        _mark(app, f"worker:history:{int(chain.chain_id)}:last_error", reason)
        raise

    error = _clean_error((result or {}).get("error"))
    if error:
        _mark(app, "worker:history:last_error", error)
        _mark(app, "worker:history:last_error_epoch", now)
        _mark(app, f"worker:history:{int(chain.chain_id)}:last_error", error)
    else:
        _mark(app, "worker:history:last_success", now)
        _mark(app, "worker:history:last_error", "")
        _mark(app, f"worker:history:{int(chain.chain_id)}:last_success", now)
        _mark(app, f"worker:history:{int(chain.chain_id)}:last_error", "")
    return result


_sibot.refresh_wallet_history = refresh_wallet_history_with_health
