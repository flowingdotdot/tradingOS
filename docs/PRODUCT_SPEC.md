# Trading OS — 제품 명세 (Product Spec)

> 상태: v0.1 (Phase 1). 이 문서는 "무엇을, 왜" 만드는지를 정의한다. "어떻게"는 [ARCHITECTURE.md](ARCHITECTURE.md)를 본다.

## 1. 제품 목표

Trading OS는 **연구 → 검증 → 모의 운용 → (먼 훗날) 실거래** 로 이어지는 전 과정을 하나의 시스템에서
재현 가능하고 감사 가능한(auditable) 방식으로 수행하기 위한 플랫폼이다.

핵심 원칙:

1. **재현성(Reproducibility)** — 모든 백테스트와 주문은 정확한 전략 버전·파라미터·코드 리비전·데이터 지문(fingerprint)을 참조한다. 같은 입력이면 같은 결과가 나와야 한다.
2. **정직성(Research integrity)** — 미래 데이터 누설(look-ahead), 미완성 캔들 사용, 비용 누락을 구조적으로 막는다. 결과가 좋아 보여도 "검증된 수익"으로 표현하지 않는다.
3. **안전 우선(Safety first)** — 실거래(LIVE)는 기본적으로 불가능하다. 불확실하면 거래하지 않는다.
4. **하나의 의사결정 로직(Single decision path)** — 백테스트와 실시간 운용이 동일한 전략 코드를 쓰고, 실행 어댑터만 다르다.

## 2. 사용자

- 1차 사용자: 프로그래밍 경험이 풍부하나 트레이딩은 학습 중인 개발자 1인.
- 따라서 UI와 문서는 **용어와 가정을 명시적으로 설명**해야 하며, 위험한 조작에는 확인 절차가 필요하다.

## 3. 워크스페이스

| 워크스페이스 | 목적 | 도입 Phase |
|---|---|---|
| Dashboard | 연결 상태, 계좌, 포지션, 리스크, 경고 요약 | 5 |
| Strategy Lab | 전략 생성/편집/버전관리, 신호 시각화, 연구 노트 | 2 (기본), 5 (편집기) |
| Backtest Lab | 과거 데이터 시뮬레이션, 성과 지표, 리플레이, 비교 | **1 (최소)**, 3 (완성) |
| Trading Desk | OKX DEMO 자동매매, 주문/포지션 모니터링, 비상 제어 | 4 |
| Trade Journal | 자동 거래 기록, 진입/청산 사유, 체결 품질, 사고 기록 | 4–5 |
| Settings | 환경, 자격증명 상태(값은 절대 표시 안 함), 리스크 한도 | 4 |
| **Investment Intelligence** | 거시경제 리서치·투자 의사결정 지원 (연구/추천만, 주문 없음) | Track B — [명세](INVESTMENT_INTELLIGENCE_SPEC.md) |

### 3.1 Strategy Lab (요구사항 요약)
- Python 모듈 기반 전략 + YAML 파라미터.
- 진입/청산 조건, 손절(SL)/익절(TP), 포지션 사이징, 리스크 제약을 전략이 **제안**하고 Risk Engine이 **승인**한다.
- 전략 버전은 게시(publish) 후 **불변(immutable)**.
- 초기 연구 대상: ICT 계열 개념(시장 구조, 스윙, BOS/MSS, Displacement, FVG, Dealing Range, Premium/Discount, OTE, Liquidity sweep). 모든 개념은 [STRATEGY_SPEC.md](STRATEGY_SPEC.md)에서 수학적으로 정의한 뒤 구현한다.
- 향후: 모멘텀, 평균회귀, 돌파, 통계적 전략. 노드 기반 비주얼 에디터.

### 3.2 Backtest Lab (요구사항 요약)
- 입력: 전략+버전, 심볼, 타임프레임, 기간, 초기자본, 수수료/슬리피지/펀딩/리스크 가정.
- 출력: 거래 목록, 차트 마커, 자산곡선(equity curve), 낙폭(drawdown), 월별 성과, 지표.
- 지표: 총 거래 수, 승률, 평균 이익/손실, 손익비, 기대값(expectancy), Profit Factor, 최대낙폭(MDD), 비용 차감 순이익, 월별 성과, 최대 연속 손실, 노출도(exposure)/포지션 크기.
- 무결성: look-ahead 방지, 미완성 캔들 배제, 캔들 내 SL/TP 동시 도달 시 **보수적으로 손절 우선**, 실제적 비용, IS/OOS 분할, 워크포워드(Phase 3).

### 3.3 Trading Desk (요구사항 요약, Phase 4)
- OKX API v5 DEMO 환경에서 완전 자동매매.
- 재시작 후 거래소 상태와 **대사(reconciliation)** 후에만 신규 진입 허용.
- 거래소 측 보호 손절(exchange-side stop) 확인 전에는 "보호되지 않은 포지션"으로 간주하고 경보.

### 3.4 Investment Intelligence (Track B)
- 단기 자동매매와 **독립**된 워크스페이스. BTC/ETH, 미국 주가지수, 금, 달러, 국채/수익률, VIX, 경제지표, 중앙은행 정책을 다룬다.
- 핵심 무결성 원칙: **point-in-time** — 과거 의사결정 시점에 공표·개정 기준으로 알 수 있었던 데이터만 사용.
- 현금/미투자도 정식 선택지. 추천 이력과 사후 결과를 영구 기록. AI 어시스턴트는 선택 기능이며 수치를 생성하지 않고 주문 권한이 없다.
- 상세: [INVESTMENT_INTELLIGENCE_SPEC.md](INVESTMENT_INTELLIGENCE_SPEC.md)

## 4. 실행 환경 (Execution Environments)

| 환경 | 설명 | 상태 |
|---|---|---|
| `BACKTEST` | 과거 데이터 시뮬레이션. 자격증명 불필요. | **구현됨 (Phase 1)** |
| `DEMO` | OKX 모의거래(`x-simulated-trading: 1`). 별도 DEMO API 키. | Phase 4 |
| `LIVE` | 실제 자금. **코드상 하드 가드로 차단.** 별도 명시적 활성화 절차가 생기기 전까지 불가. | 비활성 |

UI는 세 환경을 색과 라벨로 **항상** 구분한다 (BACKTEST=청색 "SIMULATED", DEMO=황색 "DEMO", LIVE=적색).

## 5. 초기 거래 범위

- 거래소: OKX, API v5
- 상품: `BTC-USDT-SWAP` (USDT 마진 무기한 선물, 1계약 = 0.01 BTC)
- 분석 타임프레임: 4H(맥락/바이어스), 15m(셋업), 5m(진입 확인)
- 첫 연구 가설: **Bearish OTE Continuation v1** — *검증되지 않은 가설이며 수익성을 가정하지 않는다.*

## 6. 전략 생애주기 (Strategy Lifecycle)

```
DRAFT → BACKTESTED → OUT_OF_SAMPLE_VALIDATED → DEMO_FORWARD_TESTED → LIVE_ELIGIBLE
```

- 각 단계 승격은 **사람의 명시적 결정 + 증거(실행 기록 ID)** 가 필요하다.
- 백테스트 성과만으로 자동 승격하지 않는다.
- `LIVE_ELIGIBLE`은 "실거래 자격 검토 대상"일 뿐, 실거래 활성화가 아니다.

## 7. 비목표 (Non-goals, 현 단계)

- 다중 거래소, 다중 계정, 포트폴리오 최적화
- 고빈도(HFT)/오더북 기반 전략
- 클라우드 배포, 다중 사용자 인증
- 실거래

## 8. 첫 마일스톤 수용 기준 (Milestone 1)

**OKX 과거 데이터 → 전략 → 백테스트 → 결과 UI** 가 동작한다.

- [x] OKX 공개 API에서 확정 캔들만 수집하여 Parquet에 저장, 결측 구간 보고
- [x] 예제 전략(EMA 교차, *교육용*)이 공통 Strategy 인터페이스로 신호 생성
- [x] 백테스트 엔진: 다음 봉 시가 체결, 수수료/슬리피지/펀딩, 보수적 SL/TP, 리스크 기반 사이징, 일일 손실 한도
- [x] 결과를 SQLite에 실행 메타데이터(전략 버전, 파라미터 해시, 코드 리비전, 데이터 지문)와 함께 저장
- [x] 웹 UI에서 실행·지표·자산곡선·낙폭·거래목록·차트 마커 확인
- [x] 자동화 테스트 통과
