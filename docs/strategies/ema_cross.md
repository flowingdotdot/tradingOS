# 전략: EMA Cross — Phase 1 예제 (`ema_cross`)

> 상태: maturity **educational** · **레거시 예제**. 새 작업은 [ma_crossover](ma_crossover.md)를 사용한다.
> 파라미터 전체 참조 (자동 생성): [generated/ema_cross.md](generated/ema_cross.md)

## 개요
Phase 1에서 "OKX 데이터 → 전략 → 백테스트 → UI" 파이프라인을 검증하기 위해 만든 EMA 20/50 교차 전략.
기존 백테스트 기록(run)이 이 전략과 설정을 참조하므로 **재현성을 위해 유지**한다. 신호 로직은 Phase 1과 동일하다.

## 진입 / 청산
- 진입: EMA(fast) 상향 교차 → 롱, 하향 교차 → 숏 (`allow_long` / `allow_short`)
- 손절: `close ∓ atr_stop_mult × ATR14`; 목표: `reward_risk` R; 반대 교차 청산 (선택)
- 워밍업: `max(slow×3, atr×3)` 봉

## 데이터 / 타임프레임
OHLC · 5m, 15m, 1H, 4H · BTC-USDT-SWAP

## 컴포넌트
| 컴포넌트 | 역할 |
|---|---|
| crossover | 필수 |
| opposite_exit | 선택 (`exit_on_opposite_cross`) |

## 위험 / 결과
Phase 1 실측 (15m, 150일): −22.4%, PF 0.54 — 비용(수수료·슬리피지)이 손실 대부분. 수익성 없음.

## 테스트 상태
`tests/test_backtest_engine.py`, `test_strategy_causality.py`, `test_plugin_contract.py`, `test_api.py`

## 버전 및 변경 기록
| 날짜 | definition | 설정 버전 | 변경 |
|---|---|---|---|
| 2026-11-10 | 1.0.0 | v1, v2-short-only | 최초 (Phase 1) |
| 2026-11-11 | 1.0.0 | — | 플러그인 메타데이터 추가, fast<slow 검증을 `validate_config`로 이동. 신호 로직 변경 없음 |
