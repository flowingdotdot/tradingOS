# ICT Bearish OTE Continuation — generated reference

> AUTO-GENERATED from the plugin schema by `tradingos docs`. Do not edit by hand.

- strategy_id: `ict_bearish_ote`
- definition_version: `1.0.0`
- category: ict · maturity: **research**
- run timeframes: 5m · derived internally: 15m, 4H
- instruments: BTC-USDT-SWAP · fields: open, high, low, close
- warm-up with default parameters: 2880 bars

## Components

| id | label | role | toggle | requires |
|---|---|---|---|---|
| `structure` | Swing / BOS structure | required | always on | — |
| `impulse` | Bearish impulse A->B | required | always on | — |
| `bias_filter` | HTF bias filter | optional | `bias.enabled` = `true` | — |
| `fvg_filter` | FVG zone | optional | `fvg.enabled` = `true` | — |
| `ote_filter` | OTE zone | optional | `ote.enabled` = `true` | — |
| `displacement` | Displacement requirement | optional | `displacement.enabled` = `true` | — |
| `mss_confirmation` | 5m MSS confirmation | optional | `entry.trigger` = `mss` | — |
| `fvg_retest_entry` | FVG retest limit entry | optional | `entry.mode` = `fvg_retest` | mss_confirmation |

Rule (at_least_one): fvg_filter, ote_filter — an entry zone needs the FVG zone, the OTE zone, or both

## Parameters

| path | label | type | default | unit | allowed | depends on |
|---|---|---|---|---|---|---|
| `base_timeframe` | Execution timeframe | timeframe | `5m` | — | one of: `5m` | — |
| **swing** | **Swings** | group | | | | |
| `swing.k_left` | Swing left bars — A swing high must be strictly higher than this many bars on the left | int | `3` | bars | ≥ 1; ≤ 20 | — |
| `swing.k_right` | Swing right bars (confirmation delay) — Bars that must CLOSE on the right before a swing is known | int | `3` | bars | ≥ 1; ≤ 20 | — |
| `swing.internal_k` | 5m internal swing bars — Left=right bars for the 5m internal structure used by the MSS trigger | int | `3` | bars | ≥ 1; ≤ 20 | — |
| `swing.require_contiguous` | Reject swings across data gaps | bool | `true` | — | — | — |
| **displacement** | **Displacement** | group | | | | |
| `displacement.enabled` | Use displacement — OFF removes every displacement requirement (impulse, MSS, FVG middle candle) | bool | `true` | — | — | — |
| `displacement.method` | Method — median_body: body vs median of prior bodies; atr: body vs ATR | enum | `median_body` | — | one of: `median_body`, `atr` | — |
| `displacement.lookback` | Median lookback | int | `20` | bars | ≥ 3; ≤ 500 | `displacement.method` ∈ median_body |
| `displacement.mult` | Body >= median x | float | `1.5` | x | > 0; ≤ 20 | `displacement.method` ∈ median_body |
| `displacement.atr_period` | ATR period | int | `14` | bars | ≥ 2; ≤ 200 | `displacement.method` ∈ atr |
| `displacement.atr_mult` | Body >= ATR x | float | `1.2` | x | > 0; ≤ 20 | `displacement.method` ∈ atr |
| `displacement.min_body_ratio` | Min body / range | float (nullable) | `—` | — | > 0; ≤ 1 | — |
| **bias** | **Higher-timeframe bias** | group | | | | |
| `bias.enabled` | Use higher-timeframe bias filter | bool | `true` | — | — | — |
| `bias.timeframe` | Bias timeframe | timeframe | `4H` | — | one of: `1H`, `4H` | — |
| `bias.mode` | Bias definition — swing_sequence: last two confirmed highs lower AND lows lower; last_break: last structure break | enum | `swing_sequence` | — | one of: `swing_sequence`, `last_break` | — |
| `bias.recheck_at_trigger` | Re-check bias at trigger | bool | `true` | — | — | — |
| **impulse** | **Impulse** | group | | | | |
| `impulse.timeframe` | Setup (impulse) timeframe | timeframe | `15m` | — | one of: `5m`, `15m`, `1H` | — |
| `impulse.break_kinds` | Breaks that start an impulse | multi_enum | `BOS, MSS` | — | one of: `BOS`, `MSS` | — |
| `impulse.require_displacement` | Impulse needs a displacement candle | bool | `true` | — | — | `displacement.enabled` = true |
| **fvg** | **Fair value gap** | group | | | | |
| `fvg.enabled` | Use FVG in the entry zone | bool | `true` | — | — | — |
| `fvg.min_size_mode` | Minimum size rule | enum | `atr` | — | one of: `atr`, `none` | — |
| `fvg.min_size_atr` | Min size | float | `0.1` | x ATR | ≥ 0; ≤ 5 | `fvg.min_size_mode` ∈ atr |
| `fvg.atr_period` | ATR period | int | `14` | bars | ≥ 2; ≤ 200 | — |
| `fvg.require_displacement_middle` | Middle candle must be displacement | bool | `false` | — | — | — |
| `fvg.invalidation` | Invalidation rule | enum | `close` | — | one of: `close`, `wick` | — |
| **ote** | **OTE** | group | | | | |
| `ote.enabled` | Use OTE retracement band | bool | `true` | — | — | — |
| `ote.low` | OTE low | float | `0.62` | ratio | > 0; < 1 | — |
| `ote.high` | OTE high | float | `0.79` | ratio | > 0; < 1 | — |
| `ote.ref` | Reference level | float | `0.705` | ratio | > 0; < 1 | — |
| **entry** | **Entry** | group | | | | |
| `entry.trigger` | Trigger — mss: 5m bearish MSS after the zone touch; touch: the touch itself | enum | `mss` | — | one of: `mss`, `touch` | — |
| `entry.mode` | Entry method — mss_close: market at next 5m open; fvg_retest: limit at the MSS-leg 5m FVG (Phase-3 engine) | enum | `mss_close` | — | one of: `mss_close`, `fvg_retest` | — |
| `entry.require_displacement_in_mss` | MSS needs displacement | bool | `true` | — | — | `entry.trigger` ∈ mss |
| `entry.setup_expiry_bars` | Setup expiry (to touch) | int | `96` | impulse-TF bars | ≥ 1; ≤ 10000 | — |
| `entry.trigger_expiry_bars` | Trigger expiry (after touch) | int | `36` | 5m bars | ≥ 1; ≤ 10000 | — |
| `entry.retest_expiry_bars` | Retest limit expiry | int | `12` | 5m bars | ≥ 1; ≤ 10000 | `entry.mode` ∈ fvg_retest |
| **stop** | **Stop loss** | group | | | | |
| `stop.mode` | Stop anchor | enum | `retracement_high` | — | one of: `retracement_high`, `impulse_high` | — |
| `stop.buffer_bps` | Stop buffer | bps | `5.0` | bps | ≥ 0; ≤ 500 | — |
| **target** | **Target** | group | | | | |
| `target.mode` | Target — Previous confirmed low (B) | enum | `impulse_low` | — | one of: `impulse_low` | — |
| `target.min_rr` | Minimum planned reward/risk | float | `1.5` | R | > 0; ≤ 20 | — |
