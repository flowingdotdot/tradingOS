# Trading OS — Strategy Plugin 명세 (Plugin Contract)

> 상태: v1.0 (2026-11-11). 모든 전략(ICT, 추세추종, 모멘텀, 평균회귀, 돌파, 통계적 차익 등)이 따르는 **계약**이다.
> 실제 실행 설정의 단일 진실 공급원(Source of Truth)은 **코드와 스키마**다. 이 문서는 사람이 읽는 설명이다.
> 새 전략 추가 절차: [STRATEGY_DEVELOPMENT_GUIDE.md](STRATEGY_DEVELOPMENT_GUIDE.md) · 등록/버전: [STRATEGY_REGISTRY.md](STRATEGY_REGISTRY.md)

## 1. 세 가지 개념

| 개념 | 무엇 | 어디에 | 변경 규칙 |
|---|---|---|---|
| **Strategy Definition** | 전략의 코드와 규칙 (플러그인 클래스 + `StrategyDefinition` 메타데이터) | `backend/src/tradingos/strategy/builtin/*.py` | 코드 리뷰 + `definition_version` 올림 |
| **Strategy Configuration** | 파라미터와 컴포넌트 ON/OFF 값, 버전, 해시 | `config/strategies/<id>/*.yaml` (리뷰됨) 또는 ConfigStore(UI에서 생성) | **불변.** 바꾸려면 새 버전 |
| **Strategy Instance** | 설정 버전 + 종목 + 실행 TF + 환경(BACKTEST/DEMO) + 활성 상태 + 위험 한도 | `strategy_instances` 테이블 | 설정 불변. `entries_enabled`만 운영 상태로 변경 |

하나의 Definition → 여러 Configuration → 여러 Instance.

## 2. 플러그인이 제공해야 하는 것

```python
class MyStrategy(Strategy):
    definition = StrategyDefinition(
        strategy_id="my_strategy",           # ^[a-z][a-z0-9_]{2,40}$ , 전역 고유
        name="My Strategy",
        definition_version="1.0.0",         # 신호 로직이 바뀌면 올린다 (semver)
        category="mean_reversion",          # ict|trend_following|momentum|mean_reversion|breakout|stat_arb|example
        maturity="research",                # educational|research|validated
        description="...",
        data=DataRequirements(
            base_timeframes=["15m", "1H"],  # 실행 가능한 TF (백테스트·인스턴스가 검사)
            derived_timeframes=[],          # 내부에서 생성하는 상위 TF (예: ICT의 15m/4H)
            fields=["open", "high", "low", "close", "volume"],
            instruments=["BTC-USDT-SWAP"],  # id 또는 fnmatch 패턴
        ),
        components=[ComponentSpec(...), ...],
        component_rules=[ComponentRule(...)],
        docs="docs/strategies/my_strategy.md",
    )
    Params = MyParams                       # pydantic 모델 (param()/group() 메타데이터)

    def warmup_bars(self) -> int: ...       # 신호 전 필요한 봉 수 (파라미터 의존 가능)
    def generate_signals(self, bars) -> pd.DataFrame: ...   # 인과적 신호 (§4)
    # 선택:
    def validate_config(self) -> list[dict]: ...            # 스키마로 표현 못 하는 교차 검증
    def indicator_lines(self, bars) -> list[...]: ...       # 기본 연구 화면에 그릴 지표선
    def research_view(self, bars, tf, start, end) -> ResearchView: ...  # 커스텀 연구 화면
    def create_state(self, history) / update(self, state, bar): ...     # 실시간 증분 (기본 구현 제공)
```

| 요구 항목 | 제공 위치 |
|---|---|
| Strategy ID / Name / Version / Description | `definition` (`definition_version` = 코드 버전, 설정 버전은 Configuration) |
| Supported Instruments / Required Data / Timeframes | `definition.data` |
| Parameter Schema | `Params` (pydantic) + `param()` 메타데이터 |
| Optional Component Schema | `definition.components`, `component_rules` |
| Signal Generation Logic | `generate_signals` |
| State Initialization and Update | `create_state` / `update` (기본: 전체 이력 재계산 → 일괄 계산과 정확히 동일) |
| Strategy-specific Validation | `validate_config` |

## 3. 분리 원칙 (반드시 지킬 것)

- 전략은 **매매 의도**만 반환한다: 방향(`entry`), 손절(`stop`), 목표(`target`), 청산 의도, 선택적 지정가.
- **수량·주문 가능 여부는 전략이 정하지 않는다.** Risk Engine(사이징, 일일 손실, 레버리지)과 Execution Engine(Phase 4)이 결정한다.
- 전략 안에서 거래소 API, 네트워크, 파일 쓰기, DB 접근 금지. 입력은 `bars` DataFrame뿐이다.
- 백테스트 엔진은 하나뿐이다. 전략별 엔진을 만들지 않는다.

## 4. 신호 계약

`generate_signals(bars)` — 입력과 같은 길이의 DataFrame:

| 열 | 타입 | 의미 |
|---|---|---|
| `entry` | int | +1 롱 / −1 숏 / 0 |
| `stop` | float | 진입 시 필수 (방향에 맞는 쪽) |
| `target` | float | NaN = 목표 없음 |
| `exit_long`, `exit_short` | bool | 보유 포지션 청산 의도 |
| `reason` | str | 사람이 읽는 근거 (저널/차트) |
| `limit_price`, `limit_expiry_bars` | float | 선택. NaN = 다음 봉 시가 시장가. 유한값 = 지정가 (엔진 지원은 Phase 3) |

**인과성**: 행 i는 행 0..i에만 의존해야 한다. 플랫폼 검사:
- `check_causality` — 무작위·지정 절단 지점에서 "앞부분만 준 결과의 마지막 행 == 전체 결과의 같은 행".
- `update()` 반복 결과 == `generate_signals()` 일괄 결과 (`test_plugin_contract.py`). → **백테스트와 실시간이 같은 의사결정 로직**임을 보장.
- 금지 예: `shift(-1)`, 전체 구간 정규화, "마지막 봉이면 신호 제거" 같은 데이터 길이 의존 규칙, 이웃 봉에서 추정한 값.

## 5. 시간과 데이터 규칙 (모든 전략 공통)

- 타임스탬프는 UTC epoch ms, 캔들 `ts` = 봉 **시작** 시각. 봉 i의 정보는 `ts_i + Δ`(종료)부터 사용 가능.
- 미완성 캔들은 저장되지 않는다. 결측은 채우지 않는다.
- 구조·특징 이벤트는 **발생 시각 `ts`와 인식 시각 `known_ts`를 분리**한다. 연구 화면은 `known_ts`로 "Knowledge replay"를 지원한다.
- 상위 TF가 필요하면 기준 TF에서 `features/mtf.resample`로 만들고(완전한 버킷만), `known_ts ≤ 결정 시각` 정보만 쓴다.
- 데이터 부족: 백테스트는 워밍업 이력이 부족하면 **명시적 오류** (`require_full_warmup=false`로만 완화, 기록됨). 지원하지 않는 TF/종목도 오류.

## 6. 파라미터 스키마 (`strategy/schema.py`)

`param(default, label=, description=, unit=, step=, ge/le/gt/lt=, kind=, depends_on=, advanced=)` → pydantic `Field`.
`group(Model, label=)` → 중첩 그룹. `describe_params(Params)` → 평탄화된 `ParamSpec` 목록 (API `/api/strategies` → `param_specs`).

| kind | 파이썬 타입 | UI |
|---|---|---|
| int / float | `int` / `float` | 숫자 입력 (min/max/step/unit) |
| bool | `bool` | 체크박스 |
| enum | `Literal[...]` | 선택 |
| timeframe | `Literal["5m","15m",...]` | 선택 |
| multi_enum | `list[Literal[...]]` | 체크박스 묶음 |
| percent / price / bps | `float` + `kind=` | 숫자 + 단위 |
| session | `Session` (`HH:MM-HH:MM` UTC, 자정 넘김 허용) | 텍스트 + 형식 검사 |
| nullable | `float \| None` | "none" 체크 |
| group | 중첩 `BaseModel` | 필드 묶음 (`<group>.enabled == false`면 묶음 비활성 표시) |

`depends_on={"path": "...", "in": [...]}` 또는 `{"path": "...", "equals": v}` — 조건이 거짓이면 그 파라미터는 전략에 영향을 주지 않는다.

### 6.1 백엔드 검증 (UI와 무관하게 항상 적용)
전략 객체를 만드는 모든 경로(`/validate`, 설정 저장, 연구 draft, 백테스트, 인스턴스, repo YAML 로딩)가 `Strategy.__init__`을 거치므로 UI를 우회한 API 요청도 같은 검증을 받는다.

| 순서 | 검사 | 거부 예 |
|---|---|---|
| 1 | **알 수 없는 키** — 스키마에 없는 파라미터 이름 (오타가 조용히 무시되고 기본값으로 실행되는 것을 방지) | `fast_perod: 5` |
| 2 | 타입·범위 (pydantic) | `swing.k_left: 0` |
| 3 | 컴포넌트: 필수 컴포넌트 해제, `requires`, `component_rules` | FVG와 OTE 둘 다 OFF |
| 4 | **의존성** — 의존 조건이 거짓인 파라미터가 기본값이 아니면 거부 ("효과 없는 값"이 설정에 남아 실제 실행 내용을 오해하게 하거나, 같은 동작에 다른 해시가 붙는 것을 방지) | `entry.mode: mss_close` 인데 `retest_expiry_bars: 30` |
| 5 | 전략 고유 규칙 (`validate_config`) | `fast_period ≥ slow_period` |

- 의존성에는 명시적 `depends_on`과 **그룹 규칙**이 모두 적용된다: 그룹에 `enabled` 필드가 있으면 그 그룹의 다른 필드는 `enabled = true`일 때만 의미가 있다.
- UI는 의존 조건이 깨진 필드를 기본값으로 자동 복원한다 (편의 기능; 강제는 백엔드가 한다).
- 잘못된 repo YAML 한 개는 그 버전만 `integrity: invalid`로 표시되고 다른 버전은 정상 동작한다.
- 오류 형식은 `[{path, message}]`로 통일 (`ConfigValidationError`). 테스트: `tests/test_config_validation.py`.

## 7. 컴포넌트 (조건 ON/OFF)

```python
ComponentSpec(id="trend_filter", label="Trend filter", role="optional",
              toggle_path="trend_filter.enabled", on_value=True, off_value=False, requires=[])
ComponentRule(kind="at_least_one", components=["fvg_filter", "ote_filter"], message="...")
```
- `required`: 전략의 정체성. 끌 수 없다 (토글 없음 또는 끄면 거부). 다른 논리가 필요하면 **새 전략/변형**으로 만든다.
- `optional`: 파라미터 하나(`toggle_path`)에 연결된다. 불리언이 아닌 값도 가능 (예: ICT `entry.trigger` = `mss`/`touch`).
- `requires`: 켜져 있을 때 함께 켜져 있어야 하는 컴포넌트. `component_rules`: 묶음 규칙.
- 전략마다 자기 컴포넌트 집합을 가진다. 공통 집합을 강제하지 않는다.

## 8. 연구 화면 (`strategy/view.py`)

플러그인은 범용 도형만으로 연구 화면을 기술한다 — UI는 전략을 모른다.
- `marker`, `rect`, `segment`, `band`(시간대 배경), `line`(지표 시계열), `highlight_bars`, `table`, `stat`.
- 모든 항목에 `known_ts`; 끝이 정해지지 않은 항목은 `t2=None` (cutoff까지 연장). 항목별 `tf` 제한 가능.
- 색은 의미 토큰(`bear`, `bull`, `accent`, `warn`, `zone`, `ote`, `muted`, `*_fill` …) 또는 CSS 색.
- 기본 구현: 캔들 + `indicator_lines` + 신호 마커 + SL/TP + 신호 표. 복잡한 전략은 `research_view`를 재정의 (ICT: `ict_ote_view.py`).

## 8.1 Signal Inspector (`strategy/inspect.py`) — 선택 기능
"왜 신호가 이것뿐인가"를 추적하기 위한 기록. 플러그인이 `evaluate(bars) -> (signals, Inspection)`을 재정의하면 활성화된다.

- **반드시 실제 의사결정 코드 안에서 기록한다.** 신호를 별도로 재계산하는 진단 코드를 만들지 않는다 (ICT: `_impulses`, `_record`).
- `StageDef(id, label, description, independent)` 목록 + 후보별 단계 상태:
  `PASS` / `FAIL`(이유) / `NOT_APPLICABLE`(컴포넌트 OFF 또는 정의에 없음) / `NOT_EVALUATED`(앞 단계 실패로 평가 안 됨) / `PENDING`(데이터 종료 시 진행 중).
- 집계 두 가지를 구분한다: **funnel**(순차 — 어느 단계에서 탈락하는가), **conditions**(독립 — 각 조건이 그 자체로 얼마나 엄격한가; `independent=True`인 단계만 무조건부 비율).
- 신호가 된 후보는 `evidence`(입력별 발생 시각 `ts`와 인식 시각 `known_ts`)를 남긴다.
- 사용처: `POST /api/research/inspect`, 백테스트 실행 기록의 `inspection`, Strategy Lab / Backtest Lab의 Inspector 패널.

## 8.2 거래 감사 (`services/audit_service.py`) — 모든 전략 공통
`GET /api/backtests/{id}/audit` / `tradingos audit --run <id>`: 저장된 실행의 파라미터 스냅샷으로 다시 평가해 거래마다
① 신호 재현, ② **prefix replay**(신호 봉까지만 준 데이터로 같은 신호가 나오는가 — 모든 전략에 적용), ③ evidence의 인식 시각 ≤ 결정 시각을 검사한다.

## 9. 안전 규칙

- 플러그인은 **저장소 안의 `tradingos.strategy.builtin` 패키지에서만** 발견·로딩된다. 런타임에 전달된 경로/문자열/코드를 실행하지 않는다.
- 설정 파일은 `yaml.safe_load` + pydantic 검증 (데이터일 뿐 코드가 아님).
- LIVE 인스턴스는 생성할 수 없다 (`assert_environment_allowed`).
