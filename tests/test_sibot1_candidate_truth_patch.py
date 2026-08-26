from __future__ import annotations

from types import SimpleNamespace

from learnerbot import sibot1_candidate_truth_patch as patch


def test_candidate_alert_never_claims_prevalidation_candidate_is_live(monkeypatch):
    sent = []

    def fake_send(module, app, tid, **kwargs):
        sent.append(kwargs["text"])

    monkeypatch.setattr(patch._alerts, "_send", fake_send)
    candidate = {
        "candidate_id": "c1",
        "kind": "ENTRY",
        "engine_id": "grok",
        "asset_out": "4Lr65cMLJoCd11111111111111111111111111111111",
        "poolcheck_verdict": "SHADOW_ONLY",
    }

    patch.candidate_selected_truth(SimpleNamespace(), "solana", object(), "123", candidate)

    assert len(sent) == 1
    text = sent[0]
    assert "candidate selected for LIVE evaluation" in text
    assert "LIVE status: <b>PENDING</b>" in text
    assert "Candidate PoolCheck: <b>SHADOW_ONLY</b>" in text
    assert "LIVE candidate selected" not in text


def test_unowned_exit_is_not_forwarded_to_live_bridge(monkeypatch):
    forwarded = []
    monkeypatch.setattr(patch._sol, "_position", lambda app, tid, lot_id: None)
    monkeypatch.setattr(
        patch,
        "_PREV_PROCESS_CANDIDATE",
        lambda app, tid, candidate: forwarded.append(candidate),
    )

    patch.process_candidate_with_owned_exit_prefilter(
        object(),
        "123",
        {"kind": "EXIT", "shadow_lot_id": "shadow-only-lot"},
    )

    assert forwarded == []


def test_exact_owned_exit_still_reaches_existing_exit_safety(monkeypatch):
    forwarded = []
    monkeypatch.setattr(
        patch._sol,
        "_position",
        lambda app, tid, lot_id: {"shadow_lot_id": lot_id, "status": "OPEN"},
    )
    monkeypatch.setattr(
        patch,
        "_PREV_PROCESS_CANDIDATE",
        lambda app, tid, candidate: forwarded.append(candidate),
    )
    candidate = {"kind": "EXIT", "shadow_lot_id": "live-lot"}

    patch.process_candidate_with_owned_exit_prefilter(object(), "123", candidate)

    assert forwarded == [candidate]


def test_entry_path_is_unchanged(monkeypatch):
    forwarded = []
    monkeypatch.setattr(
        patch,
        "_PREV_PROCESS_CANDIDATE",
        lambda app, tid, candidate: forwarded.append(candidate),
    )
    candidate = {"kind": "ENTRY", "shadow_lot_id": "shadow-lot"}

    patch.process_candidate_with_owned_exit_prefilter(object(), "123", candidate)

    assert forwarded == [candidate]
