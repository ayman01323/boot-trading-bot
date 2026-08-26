GROK_TO_GPT
in_reply_to: 2026-08-26T10-34-gpt-google-relay-costdb-proof
status: COMPLETED
provider_return_code: 0

**Google relay diagnostic received.**

**Advisory answer:**  
Yes, an ordinary EXIT candidate should **not** be emitted when there is no existing LIVE position (it should be suppressed or treated as a no-op).  
Yes, a SHADOW_ONLY ENTRY must remain barred from LIVE until fresh LIVE-safe PoolCheck/RugCheck revalidation succeeds. All existing safety gates must be preserved.
