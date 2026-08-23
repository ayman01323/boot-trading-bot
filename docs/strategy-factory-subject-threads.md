# Strategy Factory subject threads

Subject threads let the six Strategy Factory agents work on several topics in parallel without mixing bounded conversation memory.

## User syntax

Telegram:

```text
/aichat gemini [HOOD fraud] review the latest finding
/aichat claude [HOOD fraud] challenge Gemini's conclusion
/aichat grok [Server latency] compare p95 latency
```

CLI:

```bash
python scripts/strategy_factory_chat.py gemini 'review the latest finding' --subject 'HOOD fraud'
python scripts/ai_agent_ws_send.py --from gpt --to claude --subject 'HOOD fraud' --message 'Challenge the previous conclusion.'
```

The same normalised subject generates the same stable thread id, regardless of which agents participate.

## Persistence and compatibility

The SQLite `messages` table gains additive `thread_id` and `subject` columns. Existing rows remain intact and default to the legacy unthreaded mode. Threaded memory reads only the selected thread; legacy memory remains per-agent.

## Deployment caution

The embedded Strategy Factory runtime is part of `learnerbot.service`. Merge/testing can be completed independently, but production activation requires the normal protected deploy/restart path. Do not restart the live trading service solely for a cosmetic messaging test.
