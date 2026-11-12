# Trading OS — 로드맵 (Roadmap)

> Track A = 단기 자동매매 (Phase 1–6). Track B = Investment Intelligence (B1–B6, [명세](INVESTMENT_INTELLIGENCE_SPEC.md) §9). 두 트랙은 독립적이며 실행 엔진을 공유하지 않는다.
> 각 Phase는 "동작하는 수직 슬라이스 + 테스트"로 끝난다. LIVE는 모든 Phase에서 비활성.

## Phase 1 — Foundation ✅ (Milestone 1)
- [x] 설계 문서 5종 (PRODUCT_SPEC, ARCHITECTURE, STRATEGY_SPEC, RISK_POLICY, ROADMAP)
- [x] 백엔드/프론트엔드 구조, 설정(환경변수), LIVE 하드 가드, 구조화 로깅 + 비밀 마스킹
- [x] OKX 공개 API 과거 캔들 수집 (페이지네이션, 재시도, 확정 캔들만), 상품 메타데이터
- [x] Parquet 저장소 (증분 병합), 데이터 검증 (결측/중복/정렬/OHLC)
- [x] Strategy 인터페이스 + 예제 전략(EMA 교차) + 인과성 테스트
- [x] 백테스트 엔진 (다음 봉 시가 체결, 비용, 보수적 SL/TP, 리스크 엔진 공유) + 지표
- [x] SQLite 실행 기록 (재현성 메타데이터)
- [x] FastAPI + React 최소 UI (Backtest Lab)
- [x] pytest 자동화 테스트

## Phase 2 — Strategy Research ✅
- [x] STRATEGY_SPEC 결정 사항 기록 (충돌 2건 사용자 확인: Displacement 정의, 스윙 k)
- [x] `features/`: 스윙(발생/인식 시각 분리), BOS/MSS/스윕 상태 기계, Displacement(중앙값/ATR), FVG(수명주기), Dealing range/OTE
- [x] 다중 타임프레임: 5m에서 15m/4H 생성(완전한 버킷만), 인식 시각 기준 정렬 — OKX 원본과 일치 검증
- [x] Bearish OTE Continuation v1: 변형 2개(`v1-mss-close`, `v1-fvg-retest`), 개념별 토글, 셋업 결과 상태 기록
- [x] Strategy Lab UI: 구조 오버레이, 인식 시점 마커, Knowledge replay, 이벤트 테이블
- [x] 합성 OHLC 시나리오 테스트 + 실데이터 인과성 테스트
- [ ] 전략 버전 게시(publish) 시 불변 저장 → Phase 3로 이동

## Phase 2.5 — Strategy Plugin Architecture ✅
- [x] 아키텍처 감사 (ICT 전용 코드의 공통 영역 유입: research_service, Strategy Lab UI 전체, 하드코딩 기본값, 수동 레지스트리)
- [x] Strategy Definition / Configuration / Instance 분리
- [x] 플러그인 자동 발견 (로컬 패키지만), 메타데이터(데이터 요구사항·TF·종목·컴포넌트)
- [x] 타입 파라미터 스키마 (`param()`/`group()` → ParamSpec), 컴포넌트 필수/선택·의존성·규칙 검증
- [x] 불변 설정 버전 (config_hash / params_hash, repo YAML 무결성 검사, UI에서 새 버전 저장·복제)
- [x] 범용 연구 화면 (ResearchView 도형) — ICT 차트 로직을 플러그인 안으로 이동
- [x] 스키마 기반 자동 UI (파라미터 폼, 컴포넌트 토글, 실시간 검증), Backtest Lab 실행 비교 + 조건 차이 경고
- [x] 인스턴스 (DEMO 준비 점검, 진입 ON/OFF, 이벤트 로그), 엔진 `entries_allowed`, 계좌 단위 중재 골격
- [x] MA Crossover 플러그인으로 확장성 검증 (공통 파일 수정 0개), 계약 테스트, 스키마 기반 문서 생성

## ICT v1 Validation Milestone ✅ (2026-11-11)
- [x] 기준선 커밋 (`90d8111`), 비밀정보 점검
- [x] 백엔드 설정 검증: 파라미터 의존성, 알 수 없는 키 거부, 잘못된 YAML 격리
- [x] Signal Inspector (전략 평가 경로에서 기록, funnel/조건별 통과율 분리), 실행 기록에 저장
- [x] 거래 감사 (신호 재현, prefix replay, 입력의 인식 시각) — 150일 ICT 거래 4건 모두 통과
- [x] 체결 모델 검토·문서화, 엔진 0.2.0 (결측 직후 진입 거부, 목표 초과 체결 거부, 슬리피지 폭 안의 손절 거부)
- [x] OKX 과거 데이터 제공 범위 확인 (5m: 2019-12-16부터), 확장 계획, 타임프레임 정합성 검사 도구
- [x] UI 흐름 HTTP 스모크 스크립트 + 수동 체크리스트
- [ ] **사용자 결정 대기**: 비용 포함 사이징 / 최소 손절폭, 체결가 기준 손익비 재검사 ([strategies/ict_ote.md](strategies/ict_ote.md) §11.6)
- [ ] 과거 데이터 실제 확장 수집 ([DATA_EXPANSION_PLAN.md](DATA_EXPANSION_PLAN.md))

## Phase 3 — ICT v1 검증 (진행 중, 2026-11-12)
- [x] 6.9년치 데이터 수집·검증 (5m 726,691봉), 출처 메타데이터, 중단에 견디는 수집기
- [x] IS/OOS 분할 확정 및 OOS 봉인 (모든 전략 평가 경로)
- [x] 거래량 0 연속 구간 규칙, 타임프레임 불일치 원인 조사
- [x] 엔진 0.3.0: 비용 포함 수량, 체결가·비용 기준 순손익비, 지정가 체결 모델, 펀딩 부호 모드·민감도
- [x] 워밍업 충분성 검증, 실행 기록에 분할·체결 가정 고정
- [x] IS 실행 2건 + 전 거래 look-ahead 감사 → [PHASE3_IS_RESULTS.md](PHASE3_IS_RESULTS.md)
- [ ] **사용자 결정 대기**: v1으로 OOS를 실행할지, 정의를 가설로 재검토할지
- [ ] UI·전략 문구 한국어 전환 (검증 뒤 별도 커밋으로 합의)

## Phase 3 — Backtesting 완성
- [ ] 지정가 진입 체결 모델 (`fvg_retest` 변형 백테스트), 체결가 기준 최소 RR 재검사
- [ ] 수년치 5m 데이터 수집 (현재 180일 → 신호 표본 부족)
- [ ] 실제 과거 펀딩비 반영 (OKX funding-rate-history 수집)
- [ ] IS/OOS 분할, 워크포워드 테스트
- [ ] 캔들 리플레이 (봉 단위 재생, 그 시점까지만 보이는 특징)
- [ ] 버전 간 비교 화면, 결과 내보내기(CSV/JSON) 확장
- [ ] 다중 TF 백테스트 (5m 실행 + 15m/4H 맥락), 하위 TF로 봉 내부 SL/TP 모호성 해소 옵션
- [ ] 파라미터 민감도 분석 + 데이터 스누핑 경고 (시도 횟수 기록)
- [ ] 수명주기 상태 전이 (DRAFT → BACKTESTED → OUT_OF_SAMPLE_VALIDATED) 기록 + 증거(run id) 연결 API
- [ ] 실험(시도) 기록: 같은 데이터로 비교한 설정 버전 수 → 데이터 스누핑 경고
- [ ] 전략 간 비교 리포트 (동일 조건 강제 실행 모드)

## Phase 4 — Demo Trading
- [ ] OKX 인증 클라이언트 (HMAC 서명, `x-simulated-trading: 1`), DEMO 전용 자격증명
- [ ] WebSocket 캔들/주문/포지션 스트림, 재연결, REST 보충
- [ ] 실시간 신호 루프 (동일 Strategy 코드)
- [ ] Execution Engine: clOrdId 멱등성(`risk/portfolio.client_order_id`), 대사 후 재시도, 부분 체결, 거래소 측 SL 확인
- [ ] 인스턴스 러너: `Strategy.update()` 루프 + `arbitrate()` + Risk Engine, 인스턴스별 성과 추적
- [ ] 시작 시 대사, UNCERTAIN 상태, 킬스위치 영속화
- [ ] 장애 주입 테스트 (Mock exchange: 타임아웃, 부분 체결, 단절, 재시작)

## Phase 5 — Trading Dashboard
- [ ] Dashboard, Trading Desk, Trade Journal, Settings 화면
- [ ] 알림 (경고, 사고), 시스템 헬스 모니터링
- [ ] 노드 기반 전략 편집기 (탐색)

## Phase 6 — Live Readiness (검토만, 활성화 아님)
- [ ] 포워드 테스트 리뷰, 실행 신뢰성 테스트, 보안 감사, 운영 준비도 평가
- [ ] RISK_POLICY §7 절차 문서화

## Track B — Investment Intelligence (설계 완료, 미구현)
- [x] 명세 초안 (INVESTMENT_INTELLIGENCE_SPEC.md), UI 내비게이션 자리
- [ ] §8 결정 사항 합의 (라이선스 데이터 출처, 기준 통화, LLM 사용 범위)
- [ ] B1 FRED/ALFRED point-in-time 데이터 → B2 대시보드 → B3 시나리오/랭킹 → B4 배분 시뮬레이션 → B5 추천 기록 → B6 AI 어시스턴트(선택)
- 권장 시점: Track A Phase 3 이후 병행

## ICT v1 실패 원인 분석 (2026-11-12)
- [x] OOS 격리 보완: 봉인된 OOS 봉은 워밍업으로도 읽지 않음 (관찰 구간은 내부에서 워밍업 후 거래)
- [x] [ICT_FAILURE_ANALYSIS.md](ICT_FAILURE_ANALYSIS.md): MSS 확인 지연, 4H 바이어스, 임펄스/FVG, 구현 점검, 가설 3개
- [x] 거래별 차트 [analysis/ict_v1_is_trades.html](analysis/ict_v1_is_trades.html), 실험 기록 [EXPERIMENT_LOG.md](EXPERIMENT_LOG.md) + `experiment_id`, `tradingos trials`
- [ ] **사용자 결정 대기**: 가설 H1~H3 중 무엇을 어떤 순서로 검증할지 (v1은 고정·보존, OOS 봉인 유지)
