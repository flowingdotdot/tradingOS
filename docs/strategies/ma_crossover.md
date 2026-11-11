# 전략: Moving Average Crossover (`ma_crossover`)

> 상태: maturity **educational** · 수명주기 DRAFT · **수익성 미검증 — 플러그인 아키텍처 검증용 예제**
> 파라미터 전체 참조 (자동 생성): [generated/ma_crossover.md](generated/ma_crossover.md)

## 개요
빠른 이동평균이 느린 이동평균을 상향/하향 돌파하면 그 방향으로 진입하는 고전적 추세추종 규칙.
목적은 **ICT와 무관한 전략이 공통 엔진·레지스트리·API·UI 수정 없이 추가되는지** 검증하는 것이다.
단순 교차는 수수료 차감 후 손실인 경우가 흔하다 (Phase 1의 유사한 `ema_cross`: 150일 −22.4%).

## 진입 조건
1. 봉 i 종가 기준 `fast_i > slow_i` 그리고 `fast_{i−1} ≤ slow_{i−1}` → 롱 의도 (하향은 숏). 두 MA 모두 유효값이어야 함.
2. `warmup_bars()` 이후에만 (EMA 초기값 영향 제거).
3. `direction`: both / long_only / short_only.
4. 켜진 선택 필터를 모두 통과 (아래).
5. 체결은 엔진이 봉 i+1 시가에 수행.

## 청산 조건
- 손절: `close ∓ atr_mult × ATR(atr_period)` (필수 컴포넌트)
- 목표: `close ± reward_risk × 손절폭` (비우면 목표 없음)
- 반대 교차 시 청산 (선택 컴포넌트 `opposite_exit`)

## 필수 데이터 / 지원 종목 및 타임프레임
| 항목 | 값 |
|---|---|
| 데이터 필드 | open, high, low, close, volume |
| 실행 타임프레임 | 5m, 15m, 1H, 4H |
| 종목 | BTC-USDT-SWAP |

## 필수 / 선택 조건 (컴포넌트)
| 컴포넌트 | 역할 | 정의 |
|---|---|---|
| crossover | 필수 | MA 교차 진입 |
| atr_stop | 필수 | ATR 보호 손절 |
| trend_filter | 선택 | 롱은 `close > 추세MA(period)`, 숏은 `close <` 추세MA |
| volatility_filter | 선택 | 현재 ATR의 최근 `lookback`개 중 백분위 순위가 `[min, max]` 안 |
| volume_filter | 선택 | `volume ≥ min_ratio × 직전 lookback개 거래량 중앙값` |
| session_filter | 선택 | 신호 봉의 **시작 시각**(UTC)이 세션 안 (자정 넘김 허용). 이웃 봉으로 종료 시각을 추정하면 look-ahead 위험이 있어 시작 시각 사용 |
| opposite_exit | 선택 | 반대 교차 시 청산 |

## 설정 버전
| 버전 | 내용 |
|---|---|
| v1 | EMA 10/30, 2×ATR14 손절, 2R, 필터 없음 |
| v2-filtered | v1 + EMA200 추세 필터 + ATR 백분위 20–90 변동성 필터 |

## 위험 요소
- 횡보장에서 잦은 교차 → 비용 누적 (whipsaw).
- 필터 조합을 반복 비교하면 데이터 스누핑 위험 → Phase 3 IS/OOS 필요.
- 거래량은 OKX 계약 수 기준 (`volume`), 거래소 간 비교 불가.

## 구현 상태
전부 구현 (신호, 4개 필터, 기본 연구 화면 + MA 지표선). 커스텀 화면 없음 (기본 화면으로 충분).

## 테스트 상태
- `tests/test_ma_crossover.py`: 파라미터 검증 5종, 방향, 필터 ON/OFF 4종, 필터 켠 상태 인과성, API 전 과정 (발견 → 스키마 → 검증 → 새 버전 저장 → 백테스트 → ICT와 동일 조건 비교 → 연구 화면)
- `tests/test_plugin_contract.py`: 공통 계약 자동 적용

## 버전 및 변경 기록
| 날짜 | definition | 설정 버전 | 변경 |
|---|---|---|---|
| 2026-11-11 | 1.0.0 | v1, v2-filtered | 최초 추가 (공통 파일 수정 없이 등록됨을 검증) |
