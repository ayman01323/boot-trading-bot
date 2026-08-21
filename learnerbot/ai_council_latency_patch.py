from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, wait

from . import ai_council as _council

_DEFAULT_REVIEW_BUDGET_SECONDS = 15.0


def _review_budget_seconds() -> float:
    raw = str(os.environ.get("PASPUSS_REVIEW_BUDGET_SECONDS") or "").strip()
    try:
        value = float(raw) if raw else _DEFAULT_REVIEW_BUDGET_SECONDS
    except Exception:
        value = _DEFAULT_REVIEW_BUDGET_SECONDS
    return max(5.0, min(value, 45.0))


def run_independent_answers(app, session_id: str) -> dict:
    """Collect useful reviewers quickly instead of waiting up to 300s for every provider."""
    session = _council.load_session(app, session_id)
    question = str(session.get("question") or "")
    session["status"] = "ASKING_AGENTS"
    session["updated_epoch"] = int(time.time())
    _council.save_session(app, session)

    answers: dict[str, dict] = {}
    pool = ThreadPoolExecutor(max_workers=len(_council.PROVIDERS), thread_name_prefix="paspuss-review")
    futures = {
        pool.submit(_council._call_independent, provider, question): provider
        for provider in _council.PROVIDERS
    }
    done, pending = wait(futures, timeout=_review_budget_seconds())

    for future in done:
        provider = futures[future]
        try:
            name, result = future.result()
        except Exception as exc:
            name, result = provider, {
                "status": "FAILED",
                "answer": "",
                "error": f"{type(exc).__name__}: {exc}"[:1200],
                "return_code": 127,
                "duration_ms": 0,
            }
        answers[name] = result

    deadline_ms = int(_review_budget_seconds() * 1000)
    for future in pending:
        provider = futures[future]
        future.cancel()
        answers[provider] = {
            "status": "FAILED",
            "answer": "",
            "error": "review deadline reached; PasPuss continued with completed reviewers",
            "return_code": 124,
            "duration_ms": deadline_ms,
        }

    # Running provider calls may finish in their worker threads later, but they do not
    # mutate the saved Council session. Do not block the user while waiting for them.
    pool.shutdown(wait=False, cancel_futures=True)

    session = _council.load_session(app, session_id)
    session["answers"] = {
        provider: answers.get(
            provider,
            {
                "status": "FAILED",
                "answer": "",
                "error": "no result before review deadline",
                "return_code": 124,
                "duration_ms": deadline_ms,
            },
        )
        for provider in _council.PROVIDERS
    }
    session["status"] = "ANSWERS_READY"
    session["updated_epoch"] = int(time.time())
    return _council.save_session(app, session)


def install() -> None:
    if getattr(_council, "_paspuss_latency_patch_installed", False):
        return
    _council.run_independent_answers = run_independent_answers
    _council._paspuss_latency_patch_installed = True


install()
