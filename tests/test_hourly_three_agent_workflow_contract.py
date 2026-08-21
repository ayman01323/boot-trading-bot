from pathlib import Path


WORKFLOW = Path('.github/workflows/hourly-three-agent-strategy-cycle.yml')


def _text():
    return WORKFLOW.read_text(encoding='utf-8')


def test_hourly_strategy_cycle_remains_hourly_and_not_weekly_dependent():
    text = _text()
    # Cadence reduced from hourly to every 4 hours (~75% API cost cut); still
    # its own independent schedule trigger, not gated by the weekly workflow.
    assert "cron: '17 */4 * * *'" in text
    assert 'Weekly GPT Master Corrective Action' not in text


def test_gpt_uses_no_bwrap_workspace_sandbox_and_no_persisted_github_credentials():
    text = _text()
    assert 'persist-credentials: false' in text
    assert 'codex --ask-for-approval never exec --sandbox danger-full-access --ephemeral' in text
    assert '--sandbox workspace-write' not in text
    assert 'CODEX_API_KEY: ${{ secrets.OPENAI_API_KEY }}' in text
    assert 'STRATEGY_REVIEW_JSON_BEGIN' in text


def test_missing_runtime_evidence_fails_safe_without_marking_complete_architecture_review_incomplete():
    text = _text()
    assert 'MISSING_RUNTIME_FORENSICS' in text
    assert 'do not claim live profitability or canary readiness' in text
    assert 'EVIDENCE_FRESH="false"' in text
    assert 'Missing or stale runtime forensics ALONE does NOT make this review INCOMPLETE' in text
    assert 'use HEALTHY' in text and 'CHANGES_PROPOSED' in text
    assert 'Use INCOMPLETE only when the review itself could not be completed, parsed or validated' in text


def test_evidence_digest_binds_exact_file_bytes():
    text = _text()
    assert "sha256sum .strategy_cycle/evidence.json" in text
    assert 'do not canonicalise/re-hash JSON' in text
    assert "json.dumps(p,sort_keys=True,separators=(',',':')" not in text


def test_gemini_extraction_has_repo_pythonpath_and_phase_diagnostics():
    text = _text()
    assert 'PYTHONPATH="$GITHUB_WORKSPACE" python scripts/extract_strategy_report.py' in text
    assert 'gemini_cli_error.txt' in text
    assert 'gemini_extract_error.txt' in text
    assert 'gemini_validate_error.txt' in text
    assert 'Gemini execution/extraction/validation failed' in text


def test_copilot_assignment_matches_real_bot_handle_and_state_can_reconcile():
    text = _text()
    assert 'COPILOT_ASSIGN_TOKEN' in text
    assert 'copilot-swe-agent[bot]' in text
    assert 'test("copilot"; "i")' in text
    assert "state='AWAITING_ASSIGNMENT'" in text
    assert "state='ASSIGNED'" in text
    assert 'WAITING_FOR_COPILOT_ASSIGNMENT' in text
    assert 'Missing/stale runtime evidence' in text
    assert 'alone is NOT a reason to set status=INCOMPLETE' in text


def test_strategy_reviews_remain_report_only_and_no_live_auto_deploy():
    text = _text()
    assert 'REPORT ONLY' in text
    assert "'live_auto_deploy':False" in text
    assert 'Do not edit tracked code, trade, deploy, change capital/risk/live settings' in text
