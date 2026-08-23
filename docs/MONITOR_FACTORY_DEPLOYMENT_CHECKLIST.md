# Monitor / Strategy Factory deployment verification

A deployment is complete only when all of the following are evidenced:

- focused Monitor Factory CI compiles both new Python modules and passes their safety/contract tests;
- the PR is merged without dropping newer `main` work;
- the restricted direct deployment workflow deploys the exact merged `main` SHA;
- `learnerbot` remains active after deployment;
- the seven-agent local WebSocket bus reports GPT, Claude, Gemini, DeepSeek, Grok, Kimi and Copilot workers;
- a deterministic Engineering observation writes/updates the production monitor status without changing repository files or trading state;
- a deterministic Strategy observation writes/updates the production monitor status and creates only evidence-backed packages;
- the Factory queue is readable and no package has LIVE, capital, wallet/signing, safety-bypass, merge or deploy authority;
- scheduled operations remain read-only against the repository;
- Strategy SHADOW drafting continues through the existing >=3-review allow-listed draft-PR bridge only;
- CANARY and FULL LIVE still require the existing explicit MASTER approvals.

A failed provider, missing evidence or failed monitor cycle must be reported as unavailable/incomplete. It must not be converted into invented evidence or a permission to trade.
