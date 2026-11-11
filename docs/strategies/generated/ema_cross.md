# EMA Cross (Phase 1 example) — generated reference

> AUTO-GENERATED from the plugin schema by `tradingos docs`. Do not edit by hand.

- strategy_id: `ema_cross`
- definition_version: `1.0.0`
- category: example · maturity: **educational**
- run timeframes: 5m, 15m, 1H, 4H
- instruments: BTC-USDT-SWAP · fields: open, high, low, close
- warm-up with default parameters: 150 bars

## Components

| id | label | role | toggle | requires |
|---|---|---|---|---|
| `crossover` | EMA crossover entry | required | always on | — |
| `opposite_exit` | Exit on opposite cross | optional | `exit_on_opposite_cross` = `true` | — |

## Parameters

| path | label | type | default | unit | allowed | depends on |
|---|---|---|---|---|---|---|
| `fast_period` | Fast EMA period | int | `20` | bars | ≥ 2; ≤ 500 | — |
| `slow_period` | Slow EMA period | int | `50` | bars | ≥ 3; ≤ 1000 | — |
| `atr_period` | ATR period | int | `14` | bars | ≥ 2; ≤ 200 | — |
| `atr_stop_mult` | Stop distance | float | `2.0` | × ATR | > 0; ≤ 20 | — |
| `reward_risk` | Target — Target = reward/risk multiple of the stop distance; empty = none | float (nullable) | `2.0` | R | > 0; ≤ 20 | — |
| `allow_long` | Allow long entries | bool | `true` | — | — | — |
| `allow_short` | Allow short entries | bool | `true` | — | — | — |
| `exit_on_opposite_cross` | Exit on opposite cross | bool | `true` | — | — | — |
