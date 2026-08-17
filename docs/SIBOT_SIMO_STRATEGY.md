# SiBot Strategy / SiMo Leaders

SiBot is an independent directional spot-copy strategy. It does not replace the existing learning, arbitrage, direct-market, V2/V3 or guarded AUTO engines.

## Ranking

For every enabled EVM chain, SiBot gradually backfills candidate wallet history through Etherscan V2 and reconstructs only direct native-to-token BUY and token-to-native SELL cycles that can be matched FIFO. Gas is included in realised P&L. Transfers, LP operations, bridges, unmatched inventory and non-reproducible flows are not counted as proven SiBot spot profit.

Defaults are created at runtime in `CSVbot/sibot_settings.csv`:

- lookback: 60 days
- Top wallets: 20
- SiMo leaders per chain: 2
- allocation per copied entry: 20%
- maximum SiBot exposure: 60%
- minimum closed trades: 50
- minimum win rate: 55%
- maximum signal age: 20 seconds
- maximum entry deterioration versus leader: 1.5%
- stop loss: 10%
- take profit: 25%

A wallet must have realised profit greater than realised losses and positive net profit. Ranking is primarily by net realised profit. Histories with unmatched sells are provisional and, by default, cannot become leaders.

## SiMo execution

The live monitor watches newly confirmed blocks for current SiMo leader swaps on registered DEX routers. On a confirmed leader BUY, SiBot re-quotes the token for the user's own wallet and checks signal age, current price deterioration, round-trip sellability, product policy, gas reserve, max position count and exposure limits.

If the same token is already open because another SiMo leader bought it, the second leader is attached as a consensus leader and SiBot does not double the position by default.

SHADOW creates a hypothetical position and never signs. LIVE AUTO is separate from the existing arbitrage AUTO user switch but still requires the user's LIVE signing switch and the MASTER LIVE/AUTO platform gates.

When the primary SiMo leader sells, SiBot mirrors a profitable exit. If the leader exits while the copied position is not yet profitable and the stop loss has not fired, the position becomes EXIT PENDING and is checked repeatedly. Independent stop-loss/take-profit/time controls continue to protect capital.

## Telegram

Open `🤖 SiBot / SiMo` from the main menu.

Useful commands:

- `/sibot` — SiBot home
- `/sibot on` — enable SHADOW research/copy positions
- `/sibotstart CONFIRM` — enable SiBot LIVE AUTO after all live gates pass
- `/sibotstop` — stop new entries; existing LIVE positions remain safety-monitored
- `/sibotauto on CONFIRM` / `/sibotauto off`
- `/sibotleaders [chain]`
- `/sibottop20 [chain]`
- `/sibotpositions`
- `/sibotreport` — live capital and per-chain/overall P&L
- `/sibotsettings`
- `/sibothelp`
- `/sibotrefresh`
- `/sibotlookback DAYS`
- `/sibotleaderscount N`
- `/sibotallocation PCT`
- `/sibotmaxexposure PCT`
- `/sibotmintrades N`
- `/sibotminwin PCT`
- `/sibotsignalage SECONDS`
- `/sibotdeterioration PCT`
- `/sibotstoploss PCT`
- `/sibottakeprofit PCT`
- `/sibotmaxpositions N`
- `/sibotmaxhold HOURS`

## Data

Runtime files:

- `data/sibot.sqlite3`
- `CSVbot/sibot_settings.csv`
- `CSVbot/auto/sibot_top20.csv`
- `CSVbot/auto/simo_leaders.csv`
- `CSVbot/auto/simo_positions.csv`
- `CSVbot/auto/sibot_trade_execution.csv`

`/sibotreport` shows the active trading wallet's priced capital for each enabled chain, existing-bot realised P&L, SiBot realised P&L, SiBot open/unrealised P&L and overall totals. USD values use current dashboard prices; unpriced assets are excluded from the USD capital figure.
