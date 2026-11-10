# Trading OS — 전략 명세 (Strategy Spec)

> 상태: v0.1. **§2의 ICT 정의는 "제안(proposal)"이며 아직 구현되지 않았다.** §4의 "결정 필요" 항목에 대한 합의 후 Phase 2에서 구현한다.
> 어떤 정의도 수익성을 함의하지 않는다. 이 문서는 "측정 가능하게 만드는 것"이 목적이다.

## 0. 공통 표기와 규칙

- 봉 i의 시가/고가/저가/종가: `O_i, H_i, L_i, C_i`. 봉 시작 시각 `ts_i`, 종료 시각 `ts_i + Δ` (Δ = 봉 길이).
- **가용 시각(availability time)**: 어떤 특징(feature)이 처음 계산 가능해지는 봉 종료 시각. 모든 정의에 명시한다. 전략은 가용 시각 이후에만 그 특징을 쓸 수 있다.
- `ATR_n(i)`: Wilder 방식 n기간 Average True Range, 봉 i 종료 시 가용.
- 모든 임계값은 YAML 파라미터다. 아래 괄호 안 값은 **잠정 기본값**이지 최적값이 아니다.
- 다중 타임프레임 정렬: 상위 TF 봉은 **종료 시각 ≤ 하위 TF 의사결정 시각** 인 것만 사용한다 (`merge_asof` on close time, `allow_exact_matches=True`). 진행 중인 4H 봉은 절대 쓰지 않는다.

## 1. 전략 인터페이스 (구현됨)

```python
class Strategy(ABC):
    strategy_id: str
    def __init__(self, params: dict): ...
    def warmup_bars(self) -> int: ...                 # 지표 안정화에 필요한 최소 봉 수
    def generate_signals(self, bars: pd.DataFrame) -> pd.DataFrame: ...
```

`generate_signals` 출력 (입력과 같은 인덱스):

| 열 | 타입 | 의미 |
|---|---|---|
| `entry` | int | +1 롱 진입 의도, −1 숏 진입 의도, 0 없음 |
| `stop` | float | 진입 시 손절 **가격** (entry≠0이면 필수) |
| `target` | float | 익절 가격 (NaN 허용 = 익절 없음) |
| `exit_long` / `exit_short` | bool | 보유 포지션 청산 의도 |
| `reason` | str | 사람이 읽을 수 있는 신호 근거 (저널/차트용) |

**인과성 계약**: 행 i는 입력 행 0..i 에만 의존. `tests/test_strategy_causality.py`가 모든 등록 전략에 대해 검증한다.
신호는 봉 i 종료 시점의 의도이며, 체결은 엔진이 봉 i+1 시가에 수행한다. 수량은 전략이 아니라 Risk Engine이 정한다.

## 2. ICT 개념의 수학적 정의 (제안)

### 2.1 스윙 고점/저점 (Swing High / Low) — 프랙탈 정의
파라미터: `k_left (2)`, `k_right (2)`.
- 봉 i는 **스윙 고점**: `H_i > max(H_{i-k_left..i-1})` 그리고 `H_i ≥ max(H_{i+1..i+k_right})`.
- 봉 i는 **스윙 저점**: `L_i < min(L_{i-k_left..i-1})` 그리고 `L_i ≤ min(L_{i+1..i+k_right})`.
- 동률 규칙: 왼쪽은 엄격(>), 오른쪽은 비엄격(≥) → 같은 고점이 연속되면 **먼저 나온 봉**이 스윙.
- **가용 시각: 봉 `i + k_right` 종료 시.** ← 가장 흔한 look-ahead 실수 지점. 차트에는 봉 i에 표시하지만, 전략은 i+k_right 이후에만 안다.

### 2.2 시장 구조 (Market Structure)
확정된 스윙 시퀀스 `SH_1, SH_2, …` 와 `SL_1, SL_2, …` (가용 시각 순).
- **약세 구조(bearish)**: 가장 최근 두 확정 스윙 고점이 `SH_last < SH_prev` (LH) **그리고** 두 확정 스윙 저점이 `SL_last < SL_prev` (LL).
- 강세 구조는 대칭. 둘 다 아니면 `neutral`.

### 2.3 BOS (Break of Structure, 약세)
- 시점 t에서 가장 최근 **확정** 스윙 저점 가격 `P_sl`에 대해 `C_t < P_sl` (기본 `break_mode=close`) 또는 `L_t < P_sl` (`break_mode=wick`).
- 같은 스윙 저점에 대한 BOS는 1회만 카운트.
- 가용 시각: 봉 t 종료 시.

### 2.4 MSS (Market Structure Shift, 약세)
ICT 문헌에서 가장 모호한 개념이다. 제안 정의:
- 하위 TF(5m)에서 되돌림(상승) 중 형성된 **가장 최근 확정 내부 스윙 저점** `P_isl` (k=`k_internal (1~2)`)을, 되돌림 최고점 이후에 `C_t < P_isl` 로 깨는 첫 봉.
- 추가 조건: 깨는 구간(최고점 이후 ~ t) 안에 **Displacement 봉이 1개 이상**.
- BOS와의 차이: BOS는 추세 방향 지속의 확인, MSS는 **단기 역추세(되돌림)의 종료** 확인. 수학적으로는 같은 "스윙 저점 종가 이탈"이며, **어떤 스윙을 기준으로 하느냐(맥락)** 가 다르다.

### 2.5 Displacement (약세)
파라미터: `atr_period (14)`, `body_atr_mult (1.2)`, `min_body_ratio (0.6)`.
- 봉 t가 약세 displacement: `C_t < O_t` 그리고 `(O_t − C_t) ≥ body_atr_mult × ATR(t−1)` 그리고 `(O_t − C_t) / (H_t − L_t) ≥ min_body_ratio`.
- ATR은 **t−1까지**로 계산 (자기 자신이 ATR을 키우는 효과 배제).
- 가용 시각: 봉 t 종료 시.

### 2.6 FVG (Fair Value Gap, 약세)
3봉 패턴 (i−2, i−1, i):
- 약세 FVG 존재: `L_{i-2} > H_i`. 구간 `[bottom, top] = [H_i, L_{i-2}]`.
- 최소 크기: `top − bottom ≥ min_gap_atr (0.1) × ATR(i)`.
- 선택: 가운데 봉 i−1이 displacement여야 함 (`require_displacement_middle: true`).
- 가용 시각: 봉 i 종료 시.
- 상태 전이 (이후 봉 t > i):
  - `touched`: `H_t ≥ bottom` (가격이 갭에 진입)
  - `filled`: `H_t ≥ top` (갭 완전 메움)
  - `invalidated`: `C_t > top` (기본 무효화 규칙)

### 2.7 Dealing Range, Premium / Discount
약세 임펄스의 기준점: 임펄스 고점 `A`(스윙 고점), 임펄스 저점 `B` (A 이후 최저가).
- 범위 `R = A − B`, 균형가(Equilibrium) `EQ = B + 0.5R`.
- 가격 P의 되돌림 비율 `r(P) = (P − B) / R`. `r > 0.5` = **Premium**, `r < 0.5` = **Discount**.
- 숏은 Premium에서만 고려 (가설).

### 2.8 OTE (Optimal Trade Entry, 약세)
- OTE 구간: `r ∈ [ote_low (0.62), ote_high (0.79)]` ⇔ 가격 `[B + 0.62R, B + 0.79R]`. 참고 수준 0.705.
- 되돌림이 `r ≥ 1` (A 돌파)에 도달하면 해당 셋업은 **무효**.

### 2.9 Liquidity Sweep (매수측 유동성 스윕, 약세 맥락)
- 확정 스윙 고점 가격 `P_sh` 에 대해 봉 t: `H_t > P_sh + tol` 그리고 `C_t < P_sh`. `tol = sweep_tol_atr (0.0) × ATR`.
- 의미(가설): 직전 고점 위의 손절 주문을 소진한 뒤 되돌아옴.

### 2.10 확정된 약세 임펄스 (Confirmed Bearish Impulse)
- 확정 스윙 고점 `A` 이후,
- (a) A 이전의 확정 스윙 저점에 대한 **약세 BOS** 발생, 그리고
- (b) A~BOS 구간에 Displacement 봉 ≥ 1개,
- 임펄스 저점 `B` = A 이후 가격이 되돌림을 시작하기 전 최저가. **B의 확정**: B가 스윙 저점으로 확정된 시점(가용 시각 = B봉 + k_right).

## 3. Bearish OTE Continuation v1 (연구 가설, 미구현)

> **검증되지 않은 가설**이다. 아래 규칙은 테스트 가능하게 만들기 위한 것이며 수익성을 주장하지 않는다.

| 단계 | TF | 규칙 |
|---|---|---|
| 1. 바이어스 | 4H | §2.2 약세 구조 (확정 스윙만, 닫힌 4H 봉만) |
| 2. 임펄스 | 15m | §2.10 확정 약세 임펄스 A→B |
| 3. FVG | 15m | A~B 구간에서 생성된, 아직 무효화되지 않은 약세 FVG ≥ 1 |
| 4. OTE | 15m | A, B로 §2.8 OTE 구간 계산 |
| 5. 되돌림 진입 | 15m | 가격이 "적격 구역"에 진입. 기본: `OTE ∩ FVG` 가 비어있지 않고 `H_t` 가 그 교집합에 닿음 (`zone_rule`) |
| 6. 확인 | 5m | 터치 이후 §2.4 약세 MSS + §2.5 Displacement |
| 7. 진입 | 5m | MSS 확인 봉 종료 → 다음 5m 봉 시가 숏 |
| 8. 손절/목표/수량 | — | SL = 되돌림 최고가 + `sl_buffer_atr (0.1) × ATR_5m`; TP = `target_mode` (`impulse_low`=B, 또는 `r_multiple`=2R); 수량 = Risk Engine |

무효화/만료: 5단계 이전에 A 돌파 → 무효. 임펄스 확정 후 `setup_expiry_bars (96 × 15m = 24h)` 내 진입 없으면 만료.
동시에 하나의 셋업만 추적 (가장 최근 확정 임펄스 우선).

## 4. 결정 필요 (Open Questions) — Phase 2 구현 전에 합의

1. **스윙 k값**: 4H/15m/5m 각각 `k_left/k_right` 를 같은 값(2)으로 할지. k가 클수록 신뢰도↑, 지연↑.
2. **BOS 판정**: 종가 이탈(close) vs 꼬리 이탈(wick). 제안: close.
3. **MSS 기준 스윙**: 5m 내부 스윙 k=1 vs k=2. k=1은 노이즈에 민감.
4. **적격 구역 규칙**: `OTE ∩ FVG` (엄격, 신호 적음) vs `OTE ∪ FVG` (느슨).
5. **임펄스 저점 B 확정 시점**: 스윙 저점 확정까지 기다릴지(지연↑, 안전), 되돌림 첫 봉에서 잠정 확정할지.
6. **목표가**: 임펄스 저점(B) vs 고정 R배수 vs 다음 유동성(이전 스윙 저점).
7. **FVG 무효화**: 종가가 top 위(close) vs 고가가 top 위(wick).
8. **4H 바이어스**: 구조(LH+LL)만 vs 4H Dealing range의 Premium 조건 추가.

## 5. 예제 전략: `ema_cross` v1 (구현됨, 교육용)

**목적은 파이프라인 검증이지 수익이 아니다.** 단순 EMA 교차는 수수료 차감 후 손실인 경우가 흔하다.

- `fast = EMA(close, fast_period=20)`, `slow = EMA(close, slow_period=50)` (pandas `ewm(span, adjust=False)`, 인과적)
- 상향 교차 (`fast_{i-1} ≤ slow_{i-1}` 그리고 `fast_i > slow_i`) → 롱 의도 (`allow_long`)
- 하향 교차 → 숏 의도 (`allow_short`)
- 손절 = `C_i ∓ atr_stop_mult (2.0) × ATR_14(i)`, 목표 = `C_i ± reward_risk (2.0) × 손절폭`
- `exit_on_opposite_cross: true` 이면 반대 교차 시 청산 의도
- 처음 `warmup = slow_period × 3` 봉 동안은 신호 없음 (EMA 초기값 영향 제거)
