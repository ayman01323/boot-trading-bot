from pathlib import Path


WORKFLOW = Path('.github/workflows/ci.yml')


def test_boot_ci_gemini_connectivity_is_manual_opt_in_only():
    text = WORKFLOW.read_text(encoding='utf-8')
    assert 'live_gemini_check:' in text
    assert "if: github.event_name == 'workflow_dispatch' && inputs.live_gemini_check == true" in text
    assert 'default: false' in text


def test_normal_pr_and_push_tests_do_not_call_gemini_api():
    text = WORKFLOW.read_text(encoding='utf-8')
    api_step = text[text.index('      - name: Optional Gemini connectivity check'):text.index('  deploy_current_main:')]
    assert "if: github.event_name == 'workflow_dispatch' && inputs.live_gemini_check == true" in api_step
    assert 'generativelanguage.googleapis.com' in api_step
    before = text[:text.index('      - name: Optional Gemini connectivity check')]
    assert 'generativelanguage.googleapis.com' not in before
