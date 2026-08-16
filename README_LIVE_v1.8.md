# Multi-Chain Learning Bot v1.8 — Live Buy/Sell

v1.8 preserves the v1.7 learning, behaviour research, Top-20 ranking, SHADOW/ARMED recommendation and execution-queue functions. It adds explicit manual live trading from authorised Telegram chats.

## Supported live execution in v1.8
- BNB Smart Chain (chain 56): PancakeSwap V2 router `0x10ED43C718714eb63d5aA57B78B54704E256024E`.
- Base (chain 8453): PancakeSwap V2 router `0x8cFe327CEc66d1C090Dd72bd0FF11d690C33a2Eb`.
- Direct wrapped-native/token routes only in this first live release.
- BUY uses the router's fee-on-transfer-compatible native-to-token function.
- SELL uses exact-amount approval then the fee-on-transfer-compatible token-to-native function.

## Install over v1.7
Extract the folder as `/root/multichain-learning-bot-v1.8-live-trading`, then:

```bash
cd /root/multichain-learning-bot-v1.8-live-trading
chmod +x upgrade_from_v17.sh
./upgrade_from_v17.sh
```

The upgrade copies the v1.7 `.env`, `CSVbot/` and `data/` folders before installing v1.8 dependencies and restarting systemd under Python 3.11.

## Configure the execution wallet
Use a dedicated low-capital wallet. Edit:

```bash
nano /root/multichain-learning-bot-v1.8-live-trading/.env
```

Set:

```text
LIVE_WALLET_PRIVATE_KEY=0xYOUR_DEDICATED_TRADING_WALLET_PRIVATE_KEY
```

Never send the key to Telegram or put it in a CSV file. Then:

```bash
chmod 600 .env
systemctl restart learnerbot
```

## Telegram sequence
Keep live trading OFF while testing connectivity:

```text
/trading
/balance bsc
/quote bsc 0xTOKEN 0.001
```

If the quote is correct and the dedicated wallet is funded, enable live trading:

```text
/live on CONFIRM
```

A small live BUY:

```text
/buy bsc 0xTOKEN 0.001 CONFIRM
```

Sell half of the wallet's token balance:

```text
/sell bsc 0xTOKEN 50% CONFIRM
```

Disable live trading immediately when finished:

```text
/live off
```

Check a transaction:

```text
/tx bsc 0xTRANSACTION_HASH
```

## Limits
Edit `CSVbot/live_trading_settings.csv`. Defaults are deliberately conservative and live trading is OFF by default.

## Risk limitations
A DEX quote is not proof that a token is safe. Transfer-tax, honeypot, blacklist, anti-bot, max-wallet and malicious-token logic can make a bought token difficult or impossible to sell. Always test with a very small amount first.
