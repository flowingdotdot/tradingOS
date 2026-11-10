# Trading OS — 아키텍처 (Architecture)

> 상태: v0.1 (Phase 1). 장기 구조를 정의하되, 구현은 단계적으로 한다. **[구현됨]** / **[예정]** 표기로 현황을 구분한다.

## 1. 전체 구조

```
┌─────────────────────────── Frontend (React + TS + Vite) ────────────────────────────┐
│ Dashboard │ Strategy Lab │ Backtest Lab │ Trading Desk │ Trade Journal │ Settings    │
│               (lightweight-charts: 캔들/마커/자산곡선/낙폭)                           │
└───────────────────────────────────────┬──────────────────────────────────────────────┘
                                        │ HTTP/JSON (REST)   [예정: WebSocket push]
┌───────────────────────────────────────▼──────────────────────────────────────────────┐
│                         API layer (FastAPI) — 비밀값 절대 반환 안 함                  │
├──────────────┬──────────────┬───────────────┬─────────────┬──────────────┬───────────┤
│ Market Data  │  Strategy    │  Backtesting  │    Risk     │  Execution   │ Monitoring│
│   Engine     │   Engine     │    Engine     │   Engine    │   Engine     │  Engine   │
│ [구현: REST] │ [구현: 기본] │ [구현: 기본]  │[구현: 사이징│  [예정 P4]   │ [예정 P4] │
│              │              │               │ +일일한도]  │              │           │
├──────────────┴──────┬───────┴───────────────┴─────────────┴──────────────┴───────────┤
│  Storage: Parquet (시장 데이터)   │   SQLite (실행 기록/메타데이터, Repository 추상화)  │
└─────────────────────┴─────────────────────────────────────────────────────────────────┘
          ▲
          │ OKX API v5 (공개: 캔들/상품정보 — Phase 1, 비공개: 주문 — Phase 4 DEMO 전용)
```

## 2. 디렉터리 구조

```
tradingOS/
├── docs/                      # 명세 문서 (본 문서 포함)
├── backend/
│   ├── pyproject.toml
│   ├── .env.example           # 환경변수 템플릿 (실제 .env는 git 제외)
│   ├── config/
│   │   └── strategies/        # 전략 버전별 YAML (불변 대상)
│   │       └── ema_cross/v1.yaml
│   ├── src/tradingos/
│   │   ├── config.py          # Settings, ExecutionEnvironment, LIVE 하드 가드
│   │   ├── logging_setup.py   # 구조화(JSON) 로깅 + 비밀값 마스킹 필터
│   │   ├── domain/            # 순수 도메인 모델 (I/O 없음)
│   │   │   ├── timeframes.py
│   │   │   └── instruments.py
│   │   ├── marketdata/        # Market Data Engine
│   │   │   ├── okx_public.py  # OKX 공개 REST 클라이언트
│   │   │   ├── collector.py   # 페이지네이션 수집 + 증분 동기화
│   │   │   ├── validation.py  # 결측/중복/정렬/미완성 캔들 검사
│   │   │   └── store.py       # Parquet 저장소
│   │   ├── strategy/          # Strategy Engine
│   │   │   ├── base.py        # Strategy 인터페이스, Signal 스키마
│   │   │   ├── registry.py    # 전략 클래스 + YAML 버전 로딩
│   │   │   ├── causality.py   # look-ahead 검사기 (모든 전략 필수 통과)
│   │   │   ├── indicators.py  # 인과적 지표 (EMA, ATR)
│   │   │   └── builtin/ema_cross.py
│   │   ├── risk/              # Risk Engine (백테스트·실거래 공용)
│   │   │   └── engine.py
│   │   ├── backtest/          # Backtesting Engine
│   │   │   ├── models.py      # BacktestConfig, CostModel, Trade
│   │   │   ├── engine.py      # 봉 단위 이벤트 루프 시뮬레이터
│   │   │   └── metrics.py     # 성과 지표
│   │   ├── storage/
│   │   │   └── runs.py        # 백테스트 실행 Repository (SQLite)
│   │   ├── services/          # 유스케이스 조립 (API/CLI가 공유)
│   │   ├── api/app.py         # FastAPI
│   │   └── cli.py             # `tradingos` 명령줄
│   └── tests/
├── frontend/
│   └── src/ (App, pages/, components/, api.ts)
└── data/                      # 런타임 데이터 (git 제외)
    ├── market/okx/<instId>/<bar>.parquet
    └── tradingos.sqlite
```

[예정] `execution/` (OKX 주문 어댑터, 저널), `monitoring/`, `live/` (실시간 신호 루프).

## 3. 핵심 설계 결정

### 3.1 시간 규약 (Time convention) — 가장 중요한 규약
- 모든 타임스탬프는 **UTC, 밀리초 정수(epoch ms)**. 지역 시간은 UI 표시용으로만.
- 캔들의 `ts`는 OKX와 동일하게 **봉 시작 시각(open time)**. 봉 종료 시각 = `ts + 봉길이`.
- **봉 i의 정보는 봉 i가 닫힌 시점(`ts_i + 봉길이`)에만 사용 가능하다.** 이 시점의 의사결정은 가장 빨라도 봉 i+1 시가에 체결된다.
- OKX의 `confirm="0"` (미완성) 캔들은 **저장 단계에서 버린다.** 따라서 저장소에는 확정 캔들만 존재한다.

### 3.2 전략 인터페이스와 look-ahead 방지
```python
class Strategy:
    def generate_signals(self, bars: pd.DataFrame) -> pd.DataFrame: ...
```
- 계약(contract): **출력 행 i는 입력 행 0..i 에만 의존해야 한다** (인과성, causality).
- 백테스트는 벡터화로 한 번에 계산하고, 실시간(Phase 4)은 최근 윈도우로 호출해 **마지막 행만** 사용한다.
  두 경로가 같은 결과를 내는지는 아래 테스트로 보장한다.
- **인과성 테스트(prefix-invariance test, `strategy/causality.py`)**: 무작위 80개 절단 지점 k에서 `generate_signals(bars[:k])`의 마지막 행이 전체 계산의 k−1행과 같아야 한다 (일부 지점은 전체 구간 비교). 누설은 흔히 *잘린 구간의 마지막 행에서만*, *미래가 달라질 때만* 드러나므로 소수 지점 검사로는 놓친다 (실제로 초기 구현에서 2개 지점 검사가 `shift(-1)` 누설을 놓쳤다). 미래 데이터를 참조하는 전략(예: `shift(-1)`, 중심 이동평균, 전체 구간 정규화)은 이 테스트에서 실패한다. **모든 전략은 이 테스트를 통과해야 등록된다.**
- 전략은 "진입 의도(side, stop, target)"만 낸다. 수량 결정·승인은 Risk Engine의 책임이다.

### 3.3 백테스트 체결 모델 (보수적 가정)
| 상황 | 처리 |
|---|---|
| 신호 (봉 i 종가 시점) | 봉 i+1 **시가**에 시장가 체결 + 불리한 방향 슬리피지 |
| 진입 시가가 이미 손절가를 넘어선 경우(갭) | 진입 거부, 사유 기록 |
| 같은 봉에서 SL·TP 모두 도달 | **SL 먼저** 체결로 간주 (봉 내부 경로를 모르므로) |
| 시가가 SL을 갭으로 통과 | SL가가 아니라 **시가**로 체결 (더 나쁜 가격) |
| TP 도달 | TP 가격에 체결 (지정가 가정, 슬리피지 없음, 수수료는 보수적으로 taker) |
| 데이터 끝까지 보유 | 마지막 종가로 강제 청산, `end_of_data`로 표시 |
| 펀딩비 | 00/08/16 UTC 펀딩 시점에 보유 중이면 명목가치×가정 요율을 **방향 무관 비용**으로 차감 (보수적) |

### 3.4 Risk Engine을 백테스트와 공유
- 리스크 기반 수량: `qty = equity × risk_pct / |entry − stop|`, 계약 단위(lotSz×ctVal)로 **내림**.
- 최소 수량 미달, 레버리지 한도 초과, 일일 손실 한도 도달, 동시 포지션 한도 초과 시 거부하고 **거부 사유를 기록**한다.
- 같은 모듈이 Phase 4에서 주문 승인(order authorization)에 사용된다 → 백테스트와 실거래의 리스크 규칙이 어긋나지 않는다.

### 3.5 재현성 메타데이터
모든 백테스트 실행 기록은 다음을 포함한다:
`strategy_id, strategy_version, params, params_hash, code_revision(git SHA + dirty 여부), engine_version, data_fingerprint(사용한 캔들의 SHA-256), cost/risk 설정 전체`.
같은 fingerprint + 같은 설정 + 같은 코드 리비전 → 동일 결과 (결정론 테스트로 확인).

### 3.6 저장소
- **Parquet**: 시장 데이터. 열 지향, 압축, pandas와 직결. `(instId, bar)` 별 파일 1개. 증분 병합 시 `ts` 기준 중복 제거.
- **SQLite**: 실행 기록 등 애플리케이션 데이터. `Repository` 클래스 뒤에 숨겨 SQL을 표준 문법으로 유지 → PostgreSQL 이전 시 Repository만 교체.

### 3.7 실행 환경 격리와 LIVE 하드 가드
- `ExecutionEnvironment = BACKTEST | DEMO | LIVE`.
- `assert_environment_allowed(env)` 는 `LIVE`에 대해 **설정값과 무관하게 예외**를 던진다. LIVE 활성화는 코드 변경 + 별도 절차(RISK_POLICY §7)로만 가능하게 설계한다.
- DEMO와 LIVE 자격증명은 서로 다른 환경변수 이름 공간을 쓰고, LIVE 자격증명은 현재 코드에서 **읽지도 않는다**.

### 3.8 프론트엔드 ↔ API
- 개발 모드: 브라우저가 `http://127.0.0.1:8000` 을 직접 호출 (CORS 허용 목록: Vite 개발/프리뷰 origin). Vite 개발 프록시는 0.6MB 이상 응답에서 멈추는 현상이 확인되어 사용하지 않는다.
- 빌드/프리뷰: same-origin `/api` (프록시 또는 리버스 프록시). `VITE_API_BASE` 로 재정의 가능.
- 응답은 GZip 압축 (백테스트 상세 ~0.9MB → ~0.17MB).

### 3.9 네트워크/TLS
- OKX 공개 API 클라이언트는 `truststore`로 **OS 인증서 저장소**를 사용한다 (백신의 HTTPS 검사 환경에서도 검증을 끄지 않고 동작).
- 재시도: 429/5xx/네트워크 오류에 대해 지수 백오프. **시세 조회는 멱등(idempotent)이므로 재시도 가능.** 주문 제출은 멱등이 아니므로 Phase 4에서 "재시도 전 대사" 규칙을 따로 적용한다.

## 4. 데이터 흐름 (Milestone 1)

```
OKX /api/v5/market/history-candles (최신→과거 페이지네이션, 100개/요청)
   → 파싱, confirm=1만 채택, 정렬·중복 제거
   → validation (결측 구간, 시각 정렬, OHLC 일관성)
   → Parquet (data/market/okx/BTC-USDT-SWAP/15m.parquet)
   → Strategy.generate_signals(bars)          # 인과적 신호
   → BacktestEngine.run(bars, signals, config) # 봉 단위 루프 + RiskEngine
   → metrics.compute(...)                     # 지표
   → RunRepository.save(...)                  # SQLite
   → FastAPI → React UI
```

## 5. [예정] Track B — Investment Intelligence 경계
```
backend/src/tradingos/
  macro/            # FRED/ALFRED 클라이언트, point-in-time 저장소 (as_of 필수 쿼리)
  research/         # 레짐 지표, 시장 간 관계, 시나리오, 설명형 랭킹, 배분 시뮬레이션
  recommendations/  # 불변 추천 레코드 + 사후 평가
  assistant/        # (선택) LLM — 읽기 전용 도구만, 인용 강제
```
- `research/`·`recommendations/`·`assistant/` 는 `execution/` 를 **import할 수 없다** (테스트로 import 경계 검사 예정).
- 거시 데이터 저장: `(series_id, observation_date, realtime_start, realtime_end, value, source, retrieved_at)` — 개정 빈티지 보존. Parquet(시계열) + SQLite(메타데이터·추천 기록).

## 6. [예정] Phase 4 실시간 구조 미리보기
- WebSocket 캔들 스트림 → 확정 캔들 이벤트만 Strategy로 전달 → Signal → RiskEngine 승인 → Execution(OKX DEMO, `clOrdId`로 중복 방지) → 거래소 측 SL 첨부 확인 → Journal.
- 시작 시: 거래소 포지션/주문 조회 → 로컬 상태와 대사 → 불일치 시 `UNCERTAIN` 상태로 **신규 진입 차단** + 경보.
- 데이터 신선도 감시: 마지막 확정 캔들이 `봉길이 + 허용지연` 보다 오래되면 신규 진입 차단.
