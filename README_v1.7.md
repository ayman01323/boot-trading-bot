# Multi-Chain Learning Bot v1.7.0 — Telegram Operator Inputs

Multi-chain EVM bot-wallet discovery, historical profit reconstruction, behaviour learning, Top-20 arbitrage research, guarded IN/OUT recommendations and **Telegram-controlled operational inputs**.

## What changed in v1.7

Telegram is no longer report-only. Authorised chat IDs can change a restricted set of operational CSV settings through `/control` or inline buttons. All settings remain CSV-backed and hot-reloaded by the main loop.

v1.7 adds a guarded `ARMED` mode. An `IN` recommendation may be written to `CSVbot/auto/execution_queue.csv`, but the Telegram process **does not contain a private key or sign/broadcast transactions**.

See `CHANGELOG_v1.7.md` and `docs/V1.7_TELEGRAM_OPERATOR_GUIDE.md`.

## Main architecture

```text
EVM RPC blocks/receipts
        │
        ▼
wallet automation scoring
        │
        ▼
historical profit reconstruction
        │
        ▼
trade-behaviour classification
        │
        ▼
Top-20 copy-research qualification
        │
        ▼
live_opportunities.csv (fresh quote/simulation feed)
        │
        ▼
IN / OUT risk gates
        │
        ├── SHADOW → Telegram/report only
        │
        └── ARMED  → local execution_queue.csv
                           │
                           ▼
                 separate local executor
                 (not included in v1.7)
```

## Supported learning chains in the supplied CSV

- BNB Smart Chain — enabled
- Base — enabled
- Ethereum — prepared, disabled
- Arbitrum One — prepared, disabled
- Polygon — prepared, disabled

The same address is analysed independently per chain.

## Telegram setup

Copy `.env.example` to `.env` and set:

```text
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_IDS=123456789
```

Multiple authorised chat IDs can be comma-separated.

Useful commands:

```text
/menu
/control
/engine on
/engine off
/mode shadow
/mode armed
/setmax 0.25
/setprofit 0.001
/setcopy 25
/setedge 50
/setage 2
/setcanary 0.05
/setscore 65
/queue
/alerts
/chains
/wallets
/profit
/strategies
/behaviours
/rankings
/copy20
/signals
/status
/report
```

## Install

```bash
cd /root
unzip multichain-learning-bot-v1.7-telegram-inputs.zip
cd /root/multichain-learning-bot-v1.7-telegram-inputs
cp .env.example .env
python3 -m pip install -r requirements.txt
python3 -m learnerbot init-db
python3 -m learnerbot telegram-test
python3 -m learnerbot run
```

## Upgrade from v1.6.1

After extracting v1.7 under `/root`:

```bash
cd /root/multichain-learning-bot-v1.7-telegram-inputs
chmod +x upgrade_from_v161.sh
./upgrade_from_v161.sh
```

The upgrade script copies the existing `.env`, `CSVbot/` data and SQLite databases, while adding the new v1.7 operator settings if they do not already exist.

## CSV hot reload

Operational CSVs are re-read every learning cycle. Telegram operator actions modify the same files atomically.

New v1.7 file:

```text
CSVbot/operator_settings.csv
```

Default:

```csv
setting,value,description
engine_enabled,true,Master scanner/learning engine switch controlled from Telegram
telegram_write_enabled,true,Allow authorised Telegram operator controls to change approved CSV settings
execution_queue_enabled,true,Allow ARMED IN recommendations to be written to the local execution queue
```

## Telegram operator audit

Every operator change is appended to:

```text
CSVbot/auto/telegram_operator_audit.csv
```

The audit records time, chat ID, action, setting and old/new values.

## Top-20 and IN/OUT logic

Top-20 qualification remains historical research. Eligible behaviours by default are:

- triangular / multi-hop arbitrage;
- two-asset arbitrage;
- stablecoin arbitrage;
- private-routed arbitrage.

An `IN` recommendation additionally requires every configured live gate, including freshness, source verification, exact quote, simulation, liquidity, sellability, route approval, whole-route approval, input cap, minimum conservative profit and atomic-profit protection.

If there is no current live feed, `CSVbot/live_opportunities.csv` is empty and the bot does not invent an IN signal.

## Security boundary

Telegram can configure the bot, but v1.7 intentionally does not:

- store or request a private key in Telegram;
- sign transactions;
- approve tokens;
- withdraw funds;
- broadcast trades;
- treat a mined historical arbitrage transaction as a current copy signal.

`ARMED` means **queue after all gates pass**. A separate local executor must re-quote and re-simulate immediately before any real transaction.

## Tests

```bash
python3 -m pytest -q
```

Expected for the supplied build: 7 tests passing.
