DEEPSEEK_TO_GPT
in_reply_to: 2026-08-25T21-47-deepseek-engine-large-budget-final
status: COMPLETED
provider_return_code: 0

DEEPSEEK_ENGINE_FINAL

Material uncertainty: exact line numbers and current file internals at SHA `d21a710a89f3d740f1d784acbd913fdcce759a71` are not visible in this mailbox. The following are symbolic file-level proposals. Application, CI, virtual-ledger reset, and any future execution-bridge approval must be performed by the trusted GitHub workflow.

# 1. Distinct deterministic SHADOW strategy

Evidence fields used, from low-cost local/cached/RPC/WebSocket sources only:

- `ts`
- `open`
- `high`
- `low`
- `close`
- `volume`

No paid/private API, no order-book depth, no signer or private-key material.

Indicators:

- `sma_short = SMA(close, 5)`
- `sma_long = SMA(close, 20)`
- `atr = ATR(high, low, close, 14)`
- `prev_pivot = (prev_high + prev_low + prev_close) / 3`
- `volume_ratio = current_volume / SMA(volume, 20)`

Entry logic, deterministic:

- `Long`: `sma_short > sma_long` by `z > +1.2`, `volume_ratio >= 0.8`, `close > prev_pivot`, no current position.
- `Short`: `sma_short < sma_long` by `z < -1.2`, `volume_ratio >= 0.8`, `close < prev_pivot`, no current position.

Where `z = (sma_short - sma_long) / max(atr, min_atr)`; `min_atr` is a small positive config constant to avoid division by zero.

Exit logic:

- Long exit when `z <= +0.2`.
- Short exit when `z >= -0.2`.
- TP/SL:
  - Long TP = entry + `2.5 * atr`
  - Long SL = entry - `1.5 * atr`
  - Short TP = entry - `2.5 * atr`
  - Short SL = entry + `1.5 * atr`
- Emergency exit: stale data beyond `emergency_stale_seconds`, or any central PoolCheck reject/halt when risk-reducing exit is impossible, using last trusted close.

All floating-point comparisons use `round(x, 10)` to keep behavior deterministic across runs.

# 2. File-level patch proposals

## `strategy.py`

Add `DeepSeekShadowStrategy`.

```python
class DeepSeekShadowStrategy:
    ENGINE_ID = "deepseek"

    def __init__(self, settings):
        self.settings = settings
        self.min_atr = getattr(settings, "min_atr", 1e-8)

    def evaluate(self, candles, position):
        if len(candles) < 20:
            return {"action": "NONE", "reason": "insufficient_data"}

        closes = [c["close"] for c in candles[-20:]]
        volumes = [c["volume"] for c in candles[-20:]]

        sma_short = sum(closes[-5:]) / 5.0
        sma_long = sum(closes[-20:]) / 20.0
        atr = self._atr(candles[-15:])
        z = (sma_short - sma_long) / max(atr, self.min_atr)

        prev = candles[-2]
        pivot = (prev["high"] + prev["low"] + prev["close"]) / 3.0
        volume_ratio = candles[-1]["volume"] / max(sum(volumes) / 20.0, 1e-8)

        close = candles[-1]["close"]

        if position is None:
            if z > self.settings.z_entry_long and volume_ratio >= self.settings.min_volume_ratio and close > pivot:
                return self._entry("long", close, atr, z)
            if z < self.settings.z_entry_short and volume_ratio >= self.settings.min_volume_ratio and close < pivot:
                return self._entry("short", close, atr, z)
            return {"action": "NONE", "reason": "no_entry"}

        if position.side == "long" and z <= self.settings.z_exit_long:
            return {"action": "EXIT", "reason": "z_cross_exit_long"}
        if position.side == "short" and z >= self.settings.z_exit_short:
            return {"action": "EXIT", "reason": "z_cross_exit_short"}

        return {"action": "HOLD", "reason": "no_exit_condition"}

    def _atr(self, candles):
        # deterministic true-range average over available window
        trs = []
        for i in range(1, len(candles)):
            high = candles[i]["high"]
            low = candles[i]["low"]
            prev_close = candles[i - 1]["close"]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)
        return sum(trs[-14:]) / max(len(trs[-14:]), 1)

    def _entry(self, side, price, atr, z):
        tp_mult = self.settings.tp_atr_mult
        sl_mult = self.settings.sl_atr_mult
        if side == "long":
            tp = price + tp_mult * atr
            sl = price - sl_mult * atr
        else:
            tp = price - tp_mult * atr
            sl = price + sl_mult * atr
        return {
            "action": "ENTRY",
            "side": side,
            "entry_price": price,
            "tp": round(tp, 10),
            "sl": round(sl, 10),
            "z": round(z, 10),
            "owner_engine_id": self.ENGINE_ID,
        }
```

## `engine.py`

Add registry entry and engine class. No signer/broadcast import.

```python
from strategy import DeepSeekShadowStrategy

ENGINE_REGISTRY = {
    "gpt": GPTEngine,
    "gemini": GeminiEngine,
    "grok": GrokEngine,
    "deepseek": DeepSeekShadowEngine,
}

@dataclasses.dataclass
class DeepSeekHealth:
    signals_generated: int = 0
    entries: int = 0
    exits: int = 0
    tp_hits: int = 0
    sl_hits: int = 0
    emergency_exits: int = 0
    stale_data: int = 0
    missing_data: int = 0
    pool_checks: int = 0
    pool_rejects: int = 0
    last_signal_utc: str | None = None
    last_error: str | None = None

class DeepSeekShadowEngine:
    engine_id = "deepseek"

    def __init__(self, settings, pool_check, clock, virtual_ledger):
        self.settings = settings.deepseek_engine
        self.pool_check = pool_check
        self.clock = clock
        self.virtual_ledger = virtual_ledger
        self.strategy = DeepSeekShadowStrategy(self.settings)
        self.position = None
        self.health = DeepSeekHealth()

    def _data_ok(self, candle):
        required = ("ts", "open", "high", "low", "close", "volume")
        if any(candle.get(k) is None for k in required):
            self.health.missing_data += 1
            return False
        age = self.clock.utcnow_ts() - candle["ts"]
        if age > self.settings.max_candle_age_seconds:
            self.health.stale_data += 1
            return False
        return True

    def _check_tp_sl(self, candle):
        if self.position is None:
            return
        if self.position.side == "long":
            if candle["high"] >= self.position.tp:
                self.virtual_ledger.close_position(
                    self.settings.virtual_account_id, self.position, self.position.tp, reason="tp"
                )
                self.health.tp_hits += 1
                self.position = None
            elif candle["low"] <= self.position.sl:
                self.virtual_ledger.close_position(
                    self.settings.virtual_account_id, self.position, self.position.sl, reason="sl"
                )
                self.health.sl_hits += 1
                self.position = None
        else:
            if candle["low"] <= self.position.tp:
                self.virtual_ledger.close_position(
                    self.settings.virtual_account_id, self.position, self.position.tp, reason="tp"
                )
                self.health.tp_hits += 1
                self.position = None
            elif candle["high"] >= self.position.sl:
                self.virtual_ledger.close_position(
                    self.settings.virtual_account_id, self.position, self.position.sl, reason="sl"
                )
                self.health.sl_hits += 1
                self.position = None

    def _handle_stale_or_missing(self, candle):
        if self.position is None:
            return
        age = self.clock.utcnow_ts() - candle.get("ts", 0)
        if age > self.settings.emergency_stale_seconds:
            self.virtual_ledger.close_position(
                self.settings.virtual_account_id,
                self.position,
                candle.get("close", self.position.entry_price),
                reason="emergency_stale",
            )
            self.position = None
            self.health.emergency_exits += 1

    def on_tick(self, candles):
        if not candles:
            self.health.missing_data += 1
            return

        candle = candles[-1]

        if not self._data_ok(candle):
            self._handle_stale_or_missing(candle)
            return

        # Risk-reducing TP/SL check first; pool check still applies to new position-changing signals.
        self._check_tp_sl(candle)

        signal = self.strategy.evaluate(candles, self.position)
        self.health.signals_generated += 1
        self.health.last_signal_utc = self.clock.utcnow_iso()

        if signal["action"] == "HOLD":
            return

        self.health.pool_checks += 1
        result = self.pool_check.check(
            engine_id=self.engine_id,
            signal=signal,
            ts_us=self.clock.utcnow_us(),
            owner_engine_id=self.engine_id,
            virtual_account_id=self.settings.virtual_account_id,
        )

        if not result.allow:
            self.health.pool_rejects += 1
            return

        if signal["action"] == "ENTRY":
            if self.position is not None:
                return
            if signal["owner_engine_id"] != self.engine_id:
                self.health.pool_rejects += 1
                return
            self.position = self.virtual_ledger.open_position(
                account_id=self.settings.virtual_account_id,
                side=signal["side"],
                entry_price=signal["entry_price"],
                tp=signal["tp"],
                sl=signal["sl"],
                owner_engine_id=self.engine_id,
                max_notional=self.settings.max_shadow_position_notional,
            )
            self.health.entries += 1

        elif signal["action"] == "EXIT":
            if self.position is None:
                return
            if self.position.owner_engine_id != self.engine_id:
                self.health.pool_rejects += 1
                return
            self.virtual_ledger.close_position(
                self.settings.virtual_account_id,
                self.position,
                candle["close"],
                reason="signal_exit",
            )
            self.position = None
            self.health.exits += 1
```

## `settings_schema.py`

Add to settings schema:

```json
"deepseek_engine": {
  "type": "object",
  "additionalProperties": false,
  "required": [
    "enabled",
    "virtual_account_id",
    "max_candle_age_seconds",
    "data_source"
  ],
  "properties": {
    "enabled": {"type": "boolean", "default": false},
    "virtual_account_id": {"type": "string", "default": "DEEPSEEK_SHADOW_V1"},
    "data_source": {
      "type": "string",
      "enum": ["cached", "public_rest", "websocket"]
    },
    "max_candle_age_seconds": {"type": "number", "default": 180},
    "emergency_stale_seconds": {"type": "number", "default": 300},
    "tp_atr_mult": {"type": "number", "default": 2.5},
    "sl_atr_mult": {"type": "number", "default": 1.5},
    "z_entry_long": {"type": "number", "default": 1.2},
    "z_entry_short": {"type": "number", "default": -1.2},
    "z_exit_long": {"type": "number", "default": 0.2},
    "z_exit_short": {"type": "number", "default": -0.2},
    "min_volume_ratio": {"type": "number", "default": 0.8},
    "min_atr": {"type": "number", "default": 0.00000001},
    "max_shadow_position_notional": {"type": "number", "default": 1000.0}
  }
}
```

## Settings example

```yaml
deepseek_engine:
  enabled: true
  virtual_account_id: DEEPSEEK_SHADOW_V1
  data_source: cached             # cached | public_rest | websocket
  max_candle_age_seconds: 180
  emergency_stale_seconds: 300
  tp_atr_mult: 2.5
  sl_atr_mult: 1.5
  z_entry_long: 1.2
  z_entry_short: -1.2
  z_exit_long: 0.2
  z_exit_short: -0.2
  min_volume_ratio: 0.8
  min_atr: 0.00000001
  max_shadow_position_notional: 1000.0
```

# 3. Registry/runtime/virtual-capital integration

- Add `"deepseek": DeepSeekShadowEngine` to `ENGINE_REGISTRY`.
- Runtime construction remains keyed by `engine_id == "deepseek"`.
- Virtual capital is isolated:
  - Account: `DEEPSEEK_SHADOW_V1`
  - Ledger entries are scoped to that account only.
  - No mutation of GPT/Gemini/Grok virtual accounts.
  - Position ob
