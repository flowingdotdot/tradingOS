# Strategy Registry — 발견, 설정 버전, 인스턴스

> 코드: `strategy/registry.py` (발견·해석), `strategy/configs.py` (ConfigStore), `strategy/instances.py` (InstanceStore).

## 1. 플러그인 발견
- 프로세스 시작 시 `tradingos.strategy.builtin` 패키지의 모든 모듈을 import하고, `definition`을 선언한 `Strategy` 하위 클래스를 등록한다.
- `strategy_id` 중복 → 시작 실패 (`RegistryError`).
- **로컬 저장소의 검토된 코드만** 로딩한다. 경로·URL·설정 문자열로 코드를 로딩하는 기능은 없다.
- 확인: `tradingos strategies`, API `GET /api/strategies`.

## 2. 설정 (Configuration) 버전

| 출처 | 위치 | 생성 방법 | 수정 |
|---|---|---|---|
| repo | `backend/config/strategies/<id>/<version>.yaml` | 코드 리뷰 (git) | **금지** — 새 파일로 새 버전 |
| user | SQLite `strategy_configs` | Strategy Lab "Save as new version" / `POST /api/strategies/{id}/configs` | 불가 (API에 수정/삭제 없음) |

### 두 가지 해시
- `config_hash` = 선언된 params(YAML/요청에 적힌 그대로)의 SHA-256 → **버전의 정체성**.
- `params_hash` = 해석된 params(선언 + 현재 Definition 기본값)의 SHA-256 → **실제로 실행된 값**.
- 두 버전이 같은 resolved params를 가질 수 있다 (예: 기본값을 명시했는지 여부만 다름) → `params_hash`가 같으므로 비교 시 동일 실험임을 알 수 있다.
- 플러그인에 새 파라미터가 기본값과 함께 추가되면 기존 버전의 `config_hash`는 그대로, `params_hash`는 바뀐다. 실행 기록에 `definition_version`과 resolved params 스냅샷이 남으므로 재현할 수 있다.

### 불변성 보장
- repo 설정은 처음 보였을 때 `config_hash`를 DB에 등록한다. 이후 같은 버전 이름의 파일 내용이 바뀌면 `integrity = modified`가 되고 백테스트·인스턴스·연구 화면에서 **사용이 거부**된다.
- 이름 규칙: `^[A-Za-z0-9][A-Za-z0-9._-]{0,47}$`. repo/user 전체에서 (strategy_id, version)은 고유.
- 편집·복제 = 기존 버전 params를 출발점(`parent_version`)으로 새 버전 저장.

### 수명주기 (status)
`DRAFT → BACKTESTED → OUT_OF_SAMPLE_VALIDATED → DEMO_FORWARD_TESTED → LIVE_ELIGIBLE` (PRODUCT_SPEC §6).
현재 모든 설정은 DRAFT. 단계 승격 API/증거 기록은 Phase 3 작업이다. 백테스트 성과만으로 자동 승격하지 않는다.

## 3. 실행 기록에 남는 것 (백테스트)
`payload.strategy`: strategy_id, name, definition_version, config_version, config_source, config_hash, params_hash,
declared_params, resolved_params, components(ON/OFF 상태)
`payload.conditions`: 종목, TF, 기간, 초기자본, 수수료/슬리피지/펀딩, 리스크 설정, data_fingerprint, warm-up
레코드: run_id, created_at(UTC), environment(BACKTEST), code_revision(git SHA + dirty), engine_version.
Backtest Lab 비교 화면은 실험 조건이 다르면 경고한다.

## 4. 인스턴스 (Instance)
- `POST /api/instances` {strategy_id, config_version, inst_id, timeframe, environment, capital_allocation, risk}
- 생성 시 검사: LIVE 거부(403), 설정 무결성, 지원 TF/종목. `entries_enabled = false`로 시작.
- 인스턴스의 설정은 바꿀 수 없다 → 새 버전 저장·검증 후 새 인스턴스.
- `POST /api/instances/{id}/entries {enabled}` — **ON/OFF = 신규 진입 허용 여부**. OFF는 기존 포지션의 보호 주문·위험 관리를 중단하지 않는다 (엔진의 `entries_allowed` 마스크로 백테스트에서도 같은 의미 보장).
- DEMO에서 ON은 준비 점검을 모두 통과해야 한다: 설정 무결성, 수명주기 ≥ OUT_OF_SAMPLE_VALIDATED, 데이터 요구사항, 시장 데이터 존재, DEMO 자격증명, 실행 엔진(Phase 4 — 현재 항상 ✗). → 현재는 의도적으로 **ON 불가**.
- 모든 상태 변화는 `strategy_instance_events`에 기록된다 (실행 로그).

## 5. 다중 전략 (준비)
`risk/portfolio.py` — 계좌 수준 중재 (순수 함수, Phase 4 러너가 사용할 예정):
- 같은 종목·같은 시점의 반대 방향 의도 → 모두 거부 (`conflicting_signals`)
- 다른 인스턴스가 보유 중인 종목 → 거부 (`instrument_owned_by:<id>`, 포지션 소유권)
- 종목당 포지션 1개 (`max_positions_per_instrument`), 계좌 전체 위험 한도 (`account_risk_limit`)
- 같은 인스턴스의 중복 의도 → 1개만 (`duplicate_intent`)
- `client_order_id(instance_id, decision_ts, purpose, attempt)` — 결정적 주문 ID (재시도 전 대사용)
전략별 성과 추적은 run/instance 단위 기록으로 가능하며, 멀티전략 실거래는 범위 밖이다.
