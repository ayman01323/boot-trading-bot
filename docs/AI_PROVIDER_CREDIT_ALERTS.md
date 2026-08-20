# AI Provider Credit Alerts

This lane publishes a sanitised hourly status for OpenAI, Gemini and GitHub Copilot. The VPS Telegram bot reads that status and sends one warning per provider, billing period and ACTIVE `MASTER` account (up to the existing five-recipient safety cap) when usage reaches 80%. A second alert is sent at 100%.

Billing credentials stay in GitHub Actions. The `ai-reviews` branch contains only provider name, period, usage, allowance, percentage, state and observation time. Telegram delivery continues to use `TELEGRAM_BOT_TOKEN` on the VPS and dynamic ACTIVE MASTER IDs from `CSVbot/users.csv`; no Telegram credential or hard-coded Master ID is copied into Actions.

## GitHub Actions configuration

### OpenAI

- Secret: `OPENAI_ADMIN_KEY`
- Optional variable: `OPENAI_MONTHLY_BUDGET_USD`

The monitor reads the organisation Costs API. By default, the denominator is the organisation hard monthly spend limit. Set `OPENAI_MONTHLY_BUDGET_USD` to use a smaller operating budget. `OPENAI_API_KEY` is not an Admin API key and cannot read organisation billing.

This is monthly spend monitoring, not a prepaid-wallet balance API. Keep OpenAI's native billing safeguards enabled as well.

### GitHub Copilot

- Secret: `COPILOT_BILLING_TOKEN`
- Variable: `COPILOT_BILLING_OWNER` (defaults to the repository owner)
- Variable: `COPILOT_BILLING_SCOPE` (`user` or `organization`, default `user`)
- Variable: `COPILOT_BILLING_MODE` (`ai_credit` or legacy `premium_request`, default `ai_credit`)
- Variable: `COPILOT_MONTHLY_AI_CREDITS`
- Legacy-only variable: `COPILOT_MONTHLY_PREMIUM_REQUESTS`

For a personal account, create a fine-grained token with user `Plan: read`. For an organisation, use a fine-grained token with organisation `Administration: read`. Do not assume `COPILOT_ASSIGN_TOKEN` has billing permission. The monitor sums gross consumption because included usage can appear as a billing discount.

### Gemini

Gemini does not expose prepaid balance through `GEMINI_API_KEY`. Configure a Google Cloud monthly budget scoped to the Gemini project/service, add an 80% current-spend threshold, and publish programmatic notifications to Pub/Sub.

Configure these repository variables:

- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_ALERT_SERVICE_ACCOUNT`
- `GCP_PUBSUB_SUBSCRIPTION`
- `GEMINI_BUDGET_ID` (the exact budget ID carried in Pub/Sub message attributes)

Use a dedicated subscription for this Gemini budget and set its acknowledgement deadline to at least 600 seconds. Use GitHub OIDC/Google Workload Identity Federation. The runtime service account needs only permission to consume the configured Pub/Sub subscription. Do not store a long-lived Google service-account JSON key in GitHub.

Google budget data is estimated and delayed, so Gemini alerts are labelled monthly budget usage rather than live prepaid balance.

## Runtime behaviour

The `AI Provider Credit Alerts` workflow runs at minute 23 of every hour and can also be dispatched manually. It:

1. reads the prior sanitised status from `ai-reviews`;
2. queries OpenAI and Copilot billing usage;
3. consumes available Gemini budget notifications;
4. writes `provider-credits/latest_status.json` to `ai-reviews`;
5. acknowledges Gemini Pub/Sub messages only after publishing succeeds;
6. fails visibly if any provider monitor is still `UNKNOWN`.

The VPS watcher checks the sanitised status every five minutes. It refuses to alert from a previous billing period or a status older than three hours. Successful Telegram deliveries are recorded in `data/.ai_provider_credit_telegram_state.json` with mode `0600`, so a restart does not repeat the same alert. Newly added MASTER accounts still receive an outstanding threshold alert.

MASTERs can request the current status with `/aicredits`.

## Safe validation

Run the workflow manually after configuration. Confirm that all three rows in the Actions summary are `OK`, `ALERT` or `EXHAUSTED`, never `UNKNOWN`. A synthetic test should be performed by lowering a configured non-production budget below current usage; do not spend real credit merely to test Telegram delivery.
