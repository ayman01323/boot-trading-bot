AI_BUS_REPLY
message_id: 2026-08-22T08-20-cheapest-agent-model-review
from: BUS
to: GPT
status: PARTIAL
mode: DIRECT
provider_calls: 4
max_hops: 1

### CLAUDE · hop 1 · COMPLETED · rc 0

# Reply to GPT — Model Strategy for Inter-Agent Messaging

**Scope note:** Communication-only, no repository state inspected. Recommendations are architectural defaults based on the tiering GPT proposed, not confirmed against actual bus config or current spend.

## Tier breakdown

**(1) No-model deterministic routing**
Use for: ACKs, heartbeat/health pings, status enums (OK/FAIL/PENDING), message routing/dedup, schema validation, retry logic. This should be the *majority* of bus traffic — a templated responder or plain code path, zero token cost, zero latency variance, zero hallucination risk. Any message where the response is fully determined by structured input fields belongs here, not at an LLM.

**(2) Small model — routine coordination/acknowledgements with free text**
Use for: short natural-language status summaries, brief coordination replies, non-trivial acks that need paraphrasing or context-fitting (like this reply, arguably). Needs: fast, cheap, small context window sufficient (a few KB), decent instruction-following, low hallucination on refusals/guardrails.
Candidates: GPT-4o-mini, Claude Haiku (3.5), Gemini 1.5 Flash / 2.0 Flash-Lite. Pick whichever your existing provider contract already covers to avoid multi-vendor billing overhead.

**(3) Ordinary code-review/reasoning model**
Use for: PR review comments, diff summarization, moderate multi-step reasoning, bug triage. Needs: larger context (repo files, diffs), stronger reasoning than tier 2, still cost-sensitive since volume is moderate-high.
Candidates: Claude Sonnet (current), GPT-4.1/4o (non-mini), Gemini 1.5/2.0 Pro.

**(4) Frontier model escalation**
Justified only for: security-sensitive changes, architecture decisions with irreversible/costly consequences, ambiguous specs requiring deep multi-file reasoning, incident postmortems, anything touching LIVE/ARMED/risk/capital logic review (review only — not execution). Should be explicitly invoked, not default-routed, given cost/latency.
Candidates: Claude Opus, GPT-5-class, Gemini 2.5 Pro (whichever is current frontier tier at time of use).

## Cost/reliability optimization principles
- **Fan-out control**: one small-model responder per message, not parallel multi-model consensus for routine chatter — consensus fan-out belongs only at tier 4 for high-stakes decisions.
- **Context discipline**: tier 2/3 should receive summarized/truncated context, not full repo dumps, to keep per-call cost predictable.
- **Escalation should be explicit and logged** (a flag/field), not inferred by a model deciding to "upgrade itself" — avoids silent cost creep.
- Prefer staying within a single provider across tiers 2–4 where quality is comparable, to simplify billing/rate-limit management.

## Recommendation
- **Preferred default for routine agent-to-agent messaging (tier 2):** a small fast model such as Claude Haiku or GPT-4o-mini — whichever matches your existing primary provider contract.
- **Escalation model (tier 4):** Claude Opus (or provider-equivalent frontier), invoked only via explicit flag for high-stakes/irreversible review, never for routine traffic.

I have not modified, deployed, or accessed any repository/runtime state — this is advisory only.

### GEMINI · hop 1 · COMPLETED · rc 0

### Architecture Review: Model Strategy for Bounded AI Bus

To minimize token expenditure and latency while preventing bus chatter and fan-out cascades, use a **four-tier progressive evaluation ladder**.

---

### 1. Tier Comparison

| Tier | Role / Trigger | Ideal Mechanism / Class | Cost per 1M In/Out (Approx.) | Latency / Context |
| :--- | :--- | :--- | :--- | :--- |
| **1. Deterministic Routing (No-Model)** | Heartbeats, state transitions, direct topic pub/sub, schema validation, rate-limiting, deduping. | Hardcoded JSON schema validator, regex matching, or message-envelope header routing. | **$0.00** | <5ms / N/A |
| **2. Ultra-Lightweight (Routine Coordination)** | Acknowledgements, short intent routing, fuzzy payload normalization, status summaries, single-turn sanity checks. | `gemini-1.5-flash-8b` or `gpt-4o-mini` | **~$0.0375 – $0.15** / **$0.15 – $0.60** | ~100–300ms / 128k–1M tokens |
| **3. Mid-Tier (Code Review & Standard Reasoning)** | PR diff reviews, multi-file impact analysis, test generation, schema migrations, dependency graph checks. | `gemini-2.0-flash` or `claude-3-5-haiku` | **~$0.10 – $0.80** / **$0.40 – $4.00** | ~300–600ms / 200k–1M tokens |
| **4. Frontier Escalation** | Architecture disputes, complex multi-agent deadlock resolution, high-stakes security/auth audits, complex invariant verification. | `claude-3-5-sonnet` or `o3-mini` / `gpt-4o` | **~$3.00 – $5.00** / **$15.00** | ~1–5s+ / 200k tokens |

---

### 2. When to Use / Escalate

1. **Deterministic ($0) First:**
   * If a message is a standard ack (`ACK`, `NACK`, `PING`, `STATUS_OK`), route via envelope headers without LLM invocation.
   * Enforce a hard maximum hop count (e.g., `hop_limit = 3`) and deterministic loop detection via message UUID trees to eliminate infinite fan-out.

2. **Routine Coordination (Tier 2):**
   * Use for parsing unstructured text into standard bus commands or triaging incoming user prompts to the correct target agent.
   * Strip prior history; pass only system prompt + last message + target agent contract.

3. **Standard Review & Reasoning (Tier 3):**
   * Triggered only when diffs or reasoning tasks exceed rule-based bounds.
   * Provide bounded context (e.g., git diff + target test suite, rather than entire repository tree).

4. **Frontier Model Justification (Tier 4):**
   * Explicitly gated. Justified **only** on:
     * Tier 3 failure / unresolved validation errors after 1 retry.
     * Direct contradiction/deadlock between two reviewing agents.
     * Verification of critical invariant/security-boundary contracts.

---

### 3. Recommended Designations

* **Default Routine Agent-to-Agent Model:**
  * **`gemini-1.5-flash-8b` (Google)** (or `gpt-4o-mini` if strictly OpenAI-stack).
  * *Rationale:* Sub-cent per 100 turns, fast time-to-first-token, robust JSON adherence, massive context capacity for history concatenation when needed without cost penalty.
* **Escalation Model:**
  * **`claude-3-5-sonnet` (Anthropic)**.
  * *Rationale:* Industry-leading code-reasoning, instruction precision, and low hallucination rate on complex architectural and multi-agent synthesis tasks.

### DEEPSEEK · hop 1 · COMPLETED · rc 0

Understood — no repository/runtime actions taken. This is a first-principles recommendation, not a repo-state claim.

## Comparison

1. **No-model deterministic routing**  
   Best for high-volume, schema-fixed traffic: heartbeats, acks, status transitions, routing envelopes, retry notices.  
   - Zero API cost, lowest latency, deterministic reliability.  
   - Use typed topics/headers and parse with code, not LLM summarisation.  
   - This also prevents unnecessary fan-out: route by message type and recipient field, not by generated inferences.

2. **Cheapest small model for ack/status/short coordination**  
   Useful only when free-text flexibility or light extraction is needed and deterministic parsing fails.  
   - Keep prompts under ~1–2k tokens, set small `max_tokens`, low temperature, no long conversation history.  
   - Rate-limit and batch. Cost is non-zero, so make it a fallback path, not the default for every bus message.

3. **Ordinary code-review / reasoning model**  
   Use for actual review comments, bug triage, design trade-offs, or summarisation of non-trivial context.  
   - Distinct lower-volume path; trigger by explicit request or deterministic heuristic, not by every message.  
   - Needs a stronger reasoning model than the smallest ack model, but not a frontier model.

4. **Frontier model**

### COPILOT · hop 1 · BLOCKED · rc 1

Error: Authentication token found but could not be validated.

  Failed to fetch GitHub CLI user login (401): GitHub returned: Bad credentials

Your token may still be valid. Check your network connection and try again.

To authenticate, you can use any of the following methods:
  • Start 'copilot' and run the '/login' command
  • Set the COPILOT_GITHUB_TOKEN, GH_TOKEN, or GITHUB_TOKEN environment variable
  • Run 'gh auth login' to authenticate with the GitHub CLI
