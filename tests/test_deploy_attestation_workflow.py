from pathlib import Path


WORKFLOW = Path('.github/workflows/deploy-vps.yml')


def _text() -> str:
    return WORKFLOW.read_text(encoding='utf-8')


def test_attestation_uses_deploy_and_status_evidence_for_startup_markers():
    text = _text()
    assert "deploy_text=read('/tmp/boot-deploy-output.txt')" in text
    assert "status_text=read('/tmp/boot-attest-status.txt')" in text
    assert "evidence_text=deploy_text+'\\n'+status_text" in text
    assert "watcher_in_status='[ai-ops-watcher] started' in status_text" in text
    assert "watcher_in_deploy='[ai-ops-watcher] started' in deploy_text" in text
    assert 'watcher_ok=watcher_in_status or watcher_in_deploy' in text
    assert "markers=re.findall" in text
    assert 'evidence_text,re.I' in text


def test_sha_and_service_truth_remain_bound_to_current_status_snapshot():
    text = _text()
    assert "sha_match_obj=re.search" in text
    assert "status_text)" in text
    assert "service_ok=bool(re.search" in text
    assert "status_text,re.I" in text
    assert "'schema_version':3" in text
    assert "'ai_ops_watcher_evidence_source':watcher_source" in text
