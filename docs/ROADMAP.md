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

## Phase 2 — Strategy Research (다음)
- [ ] STRATEGY_SPEC §4 결정 사항 합의
- [ ] `features/` 모듈: ATR, 스윙(가용 시각 포함), 구조, BOS/MSS, Displacement, FVG(상태 전이), Dealing range/OTE, Sweep — 각 단위 테스트 + 인과성 테스트
- [ ] 다중 타임프레임 정렬 유틸 (4H/15m/5m, 닫힌 봉만)
- [ ] Strategy Lab UI: 특징 오버레이 (스윙, FVG 박스, OTE 구간) 시각화
- [ ] Bearish OTE Continuation v1 구현 (DRAFT)
- [ ] 전략 버전 게시(publish) 시 불변 저장 (YAML 해시 고정)

## Phase 3 — Backtesting 완성
- [ ] 실제 과거 펀딩비 반영 (OKX funding-rate-history 수집)
- [ ] IS/OOS 분할, 워크포워드 테스트
- [ ] 캔들 리플레이 (봉 단위 재생, 그 시점까지만 보이는 특징)
- [ ] 버전 간 비교 화면, 결과 내보내기(CSV/JSON) 확장
- [ ] 다중 TF 백테스트 (5m 실행 + 15m/4H 맥락), 하위 TF로 봉 내부 SL/TP 모호성 해소 옵션
- [ ] 파라미터 민감도 분석 + 데이터 스누핑 경고 (시도 횟수 기록)
- [ ] 수명주기 상태 전이 (DRAFT → BACKTESTED → OUT_OF_SAMPLE_VALIDATED) 기록

## Phase 4 — Demo Trading
- [ ] OKX 인증 클라이언트 (HMAC 서명, `x-simulated-trading: 1`), DEMO 전용 자격증명
- [ ] WebSocket 캔들/주문/포지션 스트림, 재연결, REST 보충
- [ ] 실시간 신호 루프 (동일 Strategy 코드)
- [ ] Execution Engine: clOrdId 멱등성, 대사 후 재시도, 부분 체결, 거래소 측 SL 확인
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
