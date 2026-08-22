from __future__ import annotations

import re

from . import telegram_trade_blocker_health_patch as _trade

_PREV_EVM_HISTORY_SUMMARY = _trade._evm_history_summary


def _redact(value: object) -> str:
    text = str(value or "").replace("\x00", " ")
    text = re.sub(r"(?i)(apikey=)[^&\s]+", r"\1<redacted>", text)
    text = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)[^\s,&]+", r"\1<redacted>", text)
    text = re.sub(r"\b(sk|gh[opusr]?|github_pat)_[A-Za-z0-9_-]{8,}\b", "<redacted>", text)
    return re.sub(r"\s+", " ", text).strip()[:180]


def evm_history_summary_redacted(app, tid, chain):
    row = dict(_PREV_EVM_HISTORY_SUMMARY(app, tid, chain))
    row["dominant"] = _redact(row.get("dominant"))
    return row


def install():
    if getattr(_trade, "_trade_blocker_secret_redaction_installed", False):
        return
    _trade._evm_history_summary = evm_history_summary_redacted
    _trade._trade_blocker_secret_redaction_installed = True


install()
