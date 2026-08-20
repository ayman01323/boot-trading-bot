# Gemini hourly pipeline failure audit

The hourly multi-agent pipeline failure is a compound event spanning environment restrictions, logic bugs, CLI flag placement, and error masking. 1) Missing runtime evidence forced an architecture-only fallback. 2) The GPT bot crashed due to AppArmor restrictions on bubblewrap in ubuntu-latest. 3) Gemini's actual failure was masked by a flawed stderr capture mechanism that missed the Python extraction traceback. 4) Copilot completed its work, but a strict jq string mismatch incorrectly logged its state as BLOCKED_COPILOT_AUTH. 5) Finally, the GPT Master PR workflow hung on an interactive approval prompt (action_required) due to misordered CLI flags, preventing the final Telegram broadcast.

## MEDIUM RC_01_MISSING_RUNTIME_FORENSICS — VPS runtime forensics
Strategy cycle defaults to architecture-only review, preventing any live or canary promotion.

Root cause: The external VPS runtime forensics pipeline failed to push a fresh `latest_loss_forensics.json` artifact to the `audit-status` branch within the required 2-hour window.
Fix: Investigate the external VPS cron jobs or metrics exporter scripts to restore the hourly push of `latest_loss_forensics.json` to the `audit-status` branch.

## HIGH RC_02_BWRAP_APPARMOR — GPT Independent Review
GPT step fails due to bwrap/loopback failure.

Root cause: Ubuntu 24.04 (`ubuntu-latest`) enforces strict AppArmor restrictions on unprivileged user namespaces by default. This breaks `bwrap` (bubblewrap) which the Codex CLI sandbox heavily relies upon.
Fix: Add a setup step before invoking Codex to explicitly allow user namespaces: `sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`.

## LOW RC_03_GEMINI_CAPTURE — Gemini Strategy Review
Gemini recovery fallback inaccurately states 'Gemini step failed without stderr; inspect stdout/contract validation' instead of exposing the actual error.

Root cause: Extraction script failures and stdout-based API errors bypass the dedicated `gemini_error.txt` log, resulting in lost forensic data. Additionally, the bash process substitution mechanism introduces a race condition before step termination.
Fix: Capture the entire step's stdout/stderr to a single deterministic log file, and ensure the recovery step reads this combined log rather than relying exclusively on a background-teed stderr file.

## HIGH RC_04_COPILOT_ASSIGN_FILTER — Copilot Issue Assignment
Stale BLOCKED_COPILOT_AUTH ledger state despite Copilot actually picking up the assignment (PR #108).

Root cause: A hardcoded exact string match for 'copilot' in the validation filter completely fails to match the actual GitHub app handle `copilot-swe-agent[bot]`.
Fix: Update the jq filter to use `any(contains("copilot"))` or explicitly check against the full `copilot-swe-agent[bot]` handle.

## CRITICAL RC_05_GPT_MASTER_ACTION_REQUIRED — GPT Master Synthesis
GPT Master workflow abruptly halts in an 'action_required' status, hanging on interactive approval.

Root cause: The global CLI flag `--ask-for-approval never` is placed after the `exec` subcommand. The CLI ignores it, defaulting to interactive approval. In a CI environment, this hangs or triggers an `action_required` timeout block.
Fix: Move the global flag to precede the subcommand: `codex --ask-for-approval never exec ...`
