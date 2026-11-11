# Moving Average Crossover — generated reference

> AUTO-GENERATED from the plugin schema by `tradingos docs`. Do not edit by hand.

- strategy_id: `ma_crossover`
- definition_version: `1.0.0`
- category: trend_following · maturity: **educational**
- run timeframes: 5m, 15m, 1H, 4H
- instruments: BTC-USDT-SWAP · fields: open, high, low, close, volume
- warm-up with default parameters: 90 bars

## Components

| id | label | role | toggle | requires |
|---|---|---|---|---|
| `crossover` | MA crossover entry | required | always on | — |
| `atr_stop` | ATR protective stop | required | always on | — |
| `trend_filter` | Trend filter | optional | `trend_filter.enabled` = `true` | — |
| `volatility_filter` | Volatility filter | optional | `volatility_filter.enabled` = `true` | — |
| `volume_filter` | Volume filter | optional | `volume_filter.enabled` = `true` | — |
| `session_filter` | Session filter | optional | `session_filter.enabled` = `true` | — |
| `opposite_exit` | Exit on opposite cross | optional | `exit_on_opposite_cross` = `true` | — |

## Parameters

| path | label | type | default | unit | allowed | depends on |
|---|---|---|---|---|---|---|
| `ma_type` | MA type | enum | `ema` | — | one of: `sma`, `ema` | — |
| `fast_period` | Fast MA period | int | `10` | bars | ≥ 2; ≤ 500 | — |
| `slow_period` | Slow MA period | int | `30` | bars | ≥ 3; ≤ 2000 | — |
| `direction` | Direction | enum | `both` | — | one of: `both`, `long_only`, `short_only` | — |
| `exit_on_opposite_cross` | Exit on opposite cross | bool | `true` | — | — | — |
| **stop** | **Stop loss** | group | | | | |
| `stop.atr_period` | ATR period | int | `14` | bars | ≥ 2; ≤ 200 | — |
| `stop.atr_mult` | Stop distance | float | `2.0` | x ATR | > 0; ≤ 20 | — |
| **target** | **Target** | group | | | | |
| `target.reward_risk` | Target — Target as a multiple of the stop distance; empty = no target | float (nullable) | `2.0` | R | > 0; ≤ 20 | — |
| **trend_filter** | **Trend filter** | group | | | | |
| `trend_filter.enabled` | Use trend filter — Longs only above, shorts only below a long moving average | bool | `false` | — | — | — |
| `trend_filter.period` | Trend MA period | int | `200` | bars | ≥ 10; ≤ 2000 | `trend_filter.enabled` = true |
| `trend_filter.ma_type` | Trend MA type | enum | `ema` | — | one of: `sma`, `ema` | `trend_filter.enabled` = true |
| **volatility_filter** | **Volatility filter** | group | | | | |
| `volatility_filter.enabled` | Use volatility filter — Enter only when ATR's percentile rank over the lookback is inside the band | bool | `false` | — | — | — |
| `volatility_filter.lookback` | Percentile lookback | int | `100` | bars | ≥ 20; ≤ 5000 | `volatility_filter.enabled` = true |
| `volatility_filter.min_percentile` | Min ATR percentile | percent | `20.0` | % | ≥ 0; ≤ 100 | `volatility_filter.enabled` = true |
| `volatility_filter.max_percentile` | Max ATR percentile | percent | `90.0` | % | ≥ 0; ≤ 100 | `volatility_filter.enabled` = true |
| **volume_filter** | **Volume filter** | group | | | | |
| `volume_filter.enabled` | Use volume filter | bool | `false` | — | — | — |
| `volume_filter.lookback` | Volume median lookback | int | `20` | bars | ≥ 3; ≤ 1000 | `volume_filter.enabled` = true |
| `volume_filter.min_ratio` | Volume >= median x | float | `1.2` | x | > 0; ≤ 20 | `volume_filter.enabled` = true |
| **session_filter** | **Session filter** | group | | | | |
| `session_filter.enabled` | Use session filter | bool | `false` | — | — | — |
| `session_filter.session` | Session (UTC) — Entries only from signal bars that OPEN inside this UTC window | session | `12:00-20:00` | UTC HH:MM-HH:MM | format HH:MM-HH:MM (UTC) | `session_filter.enabled` = true |
