# Grok Known Assets Testing Bot

Isolated **PAPER/SHADOW-only by default** trading bot for an explicit allow-list of established assets.

Target deployment directory:

`/home/ayman01323/BOOT/testingbots/grok_known_assets_bot`

## Scope

- Native assets and established meme tokens only.
- No new-pair discovery or arbitrary mint/address ingestion.
- Symbol alone never authorises a trade: non-native assets require an exact configured chain + contract/mint address.
- LIVE execution is intentionally absent from this MVP. `run` refuses to start unless `--paper` is supplied.
- No private key is needed for PAPER mode.

## Grok research layer

The bot now includes a bounded Grok research/advisory layer:

- `grok_settings.py` — Grok-authored Pydantic research settings and validation.
- `grok_strategy.py` — deterministic research scoring based on Grok's earlier draft, corrected and integrated by GPT after Grok declined the final scorer rewrite.
- `research_adapter.py` — maps the existing host snapshot/risk model into the Grok research interface without changing canonical asset authority.
- `docs/GROK_FLOW.md` — Grok-authored research-flow documentation with unit/interface corrections made during integration.

The research layer outputs `QUALIFY` or `REJECT`, confidence `[0,1]`, modeled cost/net edge, feature scores and explicit reasons. It has no wallet, signer, broadcast, order-placement, discovery or live-execution capability.

`run --paper` uses the research layer as a **pre-entry gate by default** for enabled allow-listed assets. The host still owns allow-list checks, breakers, sizing, position state, PAPER fills and exits. `--no-research-gate` is provided only for controlled comparison experiments.

## Strategy hypothesis

Entry requires a fresh executable quote, reverse sell path, sufficient liquidity/volume, bounded spread/impact, positive 15-minute trend, 5-minute momentum, no sharp 1-minute reversal, and enough expected edge after conservative round-trip costs.

The Grok research gate additionally requires adequate composite quality across freshness, liquidity, volume, spread, impact, 1m/5m/15m momentum and net-edge factors before the host PAPER entry engine is allowed to continue.

Position size comes from account equity and volatility-adjusted stop distance, then is capped by gross exposure, chain exposure and liquidity participation.

Exit logic uses a hard stop, partial take-profit around +2% net executable return, full target around +4%, trailing logic after TP1, time stop, momentum reversal, liquidity/spread deterioration and emergency no-sell handling.

All numeric values are PAPER hypotheses, not proven profitability claims.

## Risk defaults

| Control | Default |
|---|---:|
| Research confidence | 0.60 |
| Risk per trade | 0.35% equity |
| Max gross position | 2.0% equity |
| Max positions | 2 |
| Max chain exposure | 3.0% equity |
| Daily realised-loss breaker | 2.0% |
| Consecutive-loss breaker | 3 |
| Max quote age | 20 s |
| Max spread | 80 bps |
| Max impact | 100 bps |
| Min liquidity | $250k |
| Min 5m volume | $25k |
| Stop | 2.5%-4.0%, volatility adjusted |
| TP1 | +2.0% |
| TP2 | +4.0% |
| Max hold | 60 min |

## Install

```bash
cd /home/ayman01323/BOOT/testingbots/grok_known_assets_bot
cp -n config.example.json config.json
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
.venv/bin/grok-known-assets-bot --config config.json check
```

## Commands

```bash
grok-known-assets-bot --config config.json check
grok-known-assets-bot --config config.json list-assets
grok-known-assets-bot --config config.json evaluate --snapshot sample_snapshots/sol_entry.json --equity 10000
grok-known-assets-bot --config config.json research --snapshot sample_snapshots/sol_entry.json --min-confidence 0.60
grok-known-assets-bot --config config.json run --paper --snapshots sample_snapshots --equity 10000 --research-min-confidence 0.60
grok-known-assets-bot --config config.json report
```

For a controlled A/B PAPER comparison only:

```bash
grok-known-assets-bot --config config.json run --paper --snapshots sample_snapshots --equity 10000 --no-research-gate
```

## Meme-token allow-list

The example configuration deliberately ships meme-token addresses as disabled placeholders. Before enabling BONK/WIF/POPCAT/BRETT or another meme asset, independently verify its canonical on-chain address and replace the placeholder. An enabled placeholder is rejected at config load.

## LIVE boundary

Adding a signer or DEX execution adapter is a separate later change. It should require explicit owner approval, separate execution credentials, pre-trade simulation, sellability checks and a hard kill switch. This testing bot must not inherit production private keys.
