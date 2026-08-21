# AI agent identity standard

GitHub can show several automated agents under the same repository account or workflow credentials. Human-visible AI work must therefore identify the actual provider explicitly instead of relying on the GitHub username/avatar.

## Provider labels

| Provider | Required visible label | PR title prefix |
| --- | --- | --- |
| GPT / Codex | `🟢 AGENT: GPT` | `[GPT]` |
| Gemini | `🔵 AGENT: GEMINI` | `[GEMINI]` |
| GitHub Copilot | `🟡 AGENT: COPILOT` | `[COPILOT]` |
| Claude | `🟣 AGENT: CLAUDE` | `[CLAUDE]` |
| DeepSeek | `🔴 AGENT: DEEPSEEK` | `[DEEPSEEK]` |

## Required GitHub-visible header

Every AI-created or AI-authored GitHub comment, issue body, PR body and human-readable report must begin with the provider's identity. When the information is known, include the role/task, workflow, immutable cycle and source commit directly below it.

Example:

```text
🟣 AGENT: CLAUDE
Role: Strategy Reviewer
Workflow/Task: Claude Fourth Strategy Agent
Cycle: 0239ee8cb067-2026082110-f0ea489d
Source SHA: 127196a61c62...
```

Do not claim another provider's identity. If a workflow merely orchestrates another provider, label the provider that actually produced the analysis/content, and identify an orchestrator separately if useful. A CLI/harness is not the provider: for example, DeepSeek invoked through a Claude-compatible/Claude Code harness must still identify as `DEEPSEEK`, not `CLAUDE`.

## Pull requests and commits

AI-created PR titles should start with the provider prefix, for example `[CLAUDE] Fix strategy-cycle reconciliation`. The PR body must still contain the full visible agent header.

Where the execution environment supports commit author configuration, use the actual provider identity. In all cases, AI-originated commit messages should include an `AI-Agent: PROVIDER` trailer so history remains attributable even when GitHub records the repository account as the committer.

## Reports

JSON reports retain the existing machine-readable `provider` field. Their paired Markdown/human-readable report must begin with the visible provider header. This rule applies to Strategy, Engineering, VPS analysis and MASTER/adjudication outputs.

## Telegram

AI status/notification surfaces use the same colours:

- `🟢 GPT`
- `🔵 GEMINI`
- `🟡 COPILOT`
- `🟣 CLAUDE`
- `🔴 DEEPSEEK`

A Telegram summary may show multiple providers in one message, but each provider must keep its own explicit label/status.
