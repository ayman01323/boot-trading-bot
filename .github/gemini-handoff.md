# Gemini handoff inbox

handoff_id: 2026-08-21T21-52Z-gemini-protocol
status: ACK_ONLY
scope: repository coordination

message:
- ChatGPT ↔ Gemini handoff coordination is enabled.
- This handoff does not replace the explicit bounded task supplied to Gemini.
- Read this file from current `origin/main` before repository work and acknowledge the exact handoff id.
- A newer handoff supersedes this one.

do_not_do:
- Do not infer permission to merge, deploy, trade, change LIVE/ARMED, capital/risk, wallet/signing, secrets, or safety controls.
- Do not treat this ACK-only handoff as a request to modify code.

required_acknowledgement:
`GEMINI_HANDOFF_ACK: 2026-08-21T21-52Z-gemini-protocol`

next_action:
- Acknowledge this handoff, then follow only the separately supplied bounded task.
