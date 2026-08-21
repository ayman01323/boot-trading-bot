from pathlib import Path


def test_prior_failure_mode_is_covered_by_combined_evidence_contract():
    workflow = Path('.github/workflows/deploy-vps.yml').read_text(encoding='utf-8')
    # Prior healthy deployment had the watcher marker in deploy/startup output while
    # the later status snapshot still had service/SHA/Telegram verification. The
    # attestation must therefore combine those sources for startup markers.
    assert "deploy_text=read('/tmp/boot-deploy-output.txt')" in workflow
    assert "status_text=read('/tmp/boot-attest-status.txt')" in workflow
    assert "watcher_ok=watcher_in_status or watcher_in_deploy" in workflow
