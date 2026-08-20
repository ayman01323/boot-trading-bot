# GPT hourly pipeline failure audit

The pipeline failed through multiple independent faults. No audit-status branch existed, so the :17 workflow correctly substituted architecture-only evidence; this restricted runtime conclusions but did not prevent repository review. GPT then failed because Codex's bubblewrap sandbox could not configure loopback and could neither inspect nor write its report. Gemini also became INCOMPLETE, but its recovery logic preserved only stderr warnings and discarded the stdout/extraction/validation evidence needed to identify the actual failure. Copilot assignment was initially blocked because COPILOT_ASSIGN_TOKEN was absent, yet an external assignment subsequently succeeded and Copilot produced draft PR #108 with exactly two report files. The ai-reviews ledger was never reconciled and remained BLOCKED_COPILOT_AUTH. GPT Master was triggered by PR #108 but GitHub concluded the run action_required before jobs ran; moreover, approval alone would not complete this cycle because the workflow explicitly rejects INCOMPLETE GPT/Gemini reports. Telegram consumes ai-reviews, so it inherited the stale status and its transition logic supplied no dedicated blocked/provider-failure notification. Provider credentials were reported present for GPT and Gemini, and prior smoke success is recorded; missing API keys are not supported as causes.

## HIGH RC-01 — VPS runtime forensics and audit-status publication
Fresh VPS runtime evidence was unavailable; the cycle used architecture-only evidence with evidence_fresh=false.

Root cause: PROVEN: the audit-status branch did not exist at cycle time. HYPOTHESES requiring fresh VPS logs: the worker was not deployed/running, no completed audit ZIP existed, Git origin authentication lacked branch-write access, or the best-effort push failed. The supplied evidence cannot distinguish these possibilities.
Fix: Add observable, retried publication with a durable success/failure record and alert, while retaining the safe architecture-only fallback. Verify the deployed service actually starts the audit worker and can create/update only audit-status.

## CRITICAL RC-02 — GPT independent report
GPT was published as INCOMPLETE without inspecting the repository or creating its requested artifacts.

Root cause: Codex's bubblewrap sandbox failed during network-namespace loopback configuration before useful tool execution. This is a runner/sandbox compatibility failure, not evidence of a missing OpenAI credential or a repository defect.
Fix: Pin and qualify a known-compatible Codex CLI/runner combination. Add a preflight that performs a minimal read and writes only a disposable report artifact through the same sandbox. Fail early with complete sandbox diagnostics if it cannot do both. Do not bypass or weaken the sandbox.

## HIGH RC-03 — Gemini independent report and failure capture
Gemini was INCOMPLETE, but the published explanation contains only terminal color and ripgrep fallback warnings.

Root cause: The exact Gemini execution failure is UNKNOWN. The proven pipeline defect is inadequate failure capture: stdout, exit code, failing phase and extraction/validation output are not preserved, while unrelated stderr warnings are promoted as the reason.
Fix: Capture CLI exit code, stdout tail, stderr tail and separate extraction/validation diagnostics in a sanitized structured failure record. Preserve them as cycle artifacts and identify the failing phase.

## HIGH RC-04 — Copilot authentication and assignment
The kickoff workflow recorded BLOCKED_AUTH, although Copilot was subsequently assigned and produced PR #108.

Root cause: The workflow lacked its dedicated Copilot assignment credential and correctly declared the automated assignment blocked. A later external assignment recovered the Copilot task, proving Copilot service authorization itself was available through another actor/path.
Fix: Provision and preflight a least-privilege assignment credential or establish a documented GitHub-native assignment mechanism. Treat assignment as an asynchronously reconcilable state, not a permanent cycle result.

## CRITICAL RC-05 — Copilot PR reconciliation and ai-reviews ledger
ai-reviews latest_status remained BLOCKED_COPILOT_AUTH after Copilot completed PR #108.

Root cause: The state machine has no durable PR-arrival reconciliation stage. Copilot success can only be reflected after the entire GPT Master path succeeds, so assignment recovery and report delivery are conflated with master adjudication.
Fix: On matching Copilot PR opened/synchronize/reopened events, first validate identity and report shape, then atomically publish copilot=DONE and the PR URL before attempting master adjudication. Preserve GPT/Gemini INCOMPLETE independently.

## CRITICAL RC-06 — GPT Master workflow execution
GPT Master run 32328146903 was triggered but concluded action_required immediately, before useful adjudication.

Root cause: PROVEN: GitHub held the workflow for an action-required approval before jobs executed. HYPOTHESIS: repository/organization Actions policy required approval for a bot-authored or first-time-contributor workflow. No settings export or job log is supplied, so the exact policy cannot be proven.
Fix: Inspect the repository/organization Actions approval policy and explicitly authorize the trusted same-repository Copilot bot workflow path, while retaining the workflow's same-repository and two-file validation gates. Add monitoring for action_required runs.

## HIGH RC-07 — GPT Master completeness gate
Even if run 32328146903 were approved, the cycle could not reach GPT synthesis.

Root cause: The master workflow correctly enforces completeness, but there is no retry/recovery orchestration for failed independent providers before adjudication.
Fix: Keep the completeness safeguard. Add bounded provider-only retries with immutable cycle/source/evidence identifiers and publish an explicit REPORTS_INCOMPLETE terminal state when retries are exhausted.

## HIGH RC-08 — Telegram/operator visibility
Telegram could show only the stale ai-reviews state and did not provide a dedicated notification for Copilot blockage/recovery or individual strategy provider failures.

Root cause: Telegram is downstream of the stale ledger and its strategy transition model omits provider-failure, assignment-blocked, recovered-assignment and action_required states.
Fix: Reconcile ai-reviews first, then add deduplicated alerts for INCOMPLETE providers, BLOCKED_AUTH, Copilot report arrival and MASTER_ACTION_REQUIRED. Preserve MASTER-only authorization and sanitized content.

## MEDIUM RC-09 — Workflow and CLI reliability
The workflows install mutable latest CLIs and use inconsistent Codex argument ordering; current source tests also contain a stale expectation.

Root cause: Provider tool versions are unpinned, allowing hourly behavior to change without source changes. Codex invocation conventions are inconsistent, and workflow contract tests do not cover the master invocations or accurately reflect the assignment implementation.
Fix: Pin qualified CLI versions, standardize supported argument ordering across workflows, and update contract tests to validate behavior rather than obsolete literals.
