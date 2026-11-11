# Trading OS — 전략 명세 색인

> v0.3 (2026-11-11): 전략 문서가 **전략별 파일**로 분리되었다. 이 파일은 색인이다.

| 문서 | 내용 |
|---|---|
| [STRATEGY_PLUGIN_SPEC.md](STRATEGY_PLUGIN_SPEC.md) | 모든 전략이 따르는 플러그인 계약 — 신호 계약, 인과성, 두 개의 시각(`ts`/`known_ts`), 다중 타임프레임, 파라미터·컴포넌트 스키마, 연구 화면 |
| [STRATEGY_DEVELOPMENT_GUIDE.md](STRATEGY_DEVELOPMENT_GUIDE.md) | 새 전략을 추가하는 실제 절차 |
| [STRATEGY_REGISTRY.md](STRATEGY_REGISTRY.md) | 플러그인 발견, 설정 버전·해시·불변성, 인스턴스, 다중 전략 중재 |
| [strategies/ict_ote.md](strategies/ict_ote.md) | ICT Bearish OTE Continuation — 개념 정의는 **기존과 같은 절 번호**(§2 개념, §3 전략 규칙, §4 결정 기록, §5 파라미터, §6 미결 판단) |
| [strategies/ma_crossover.md](strategies/ma_crossover.md) | MA Crossover (플러그인 검증 예제) |
| [strategies/ema_cross.md](strategies/ema_cross.md) | EMA Cross (Phase 1 레거시 예제) |
| [strategies/generated/](strategies/generated/) | 스키마에서 자동 생성된 파라미터 참조 (`tradingos docs`) |
| [strategies/_TEMPLATE.md](strategies/_TEMPLATE.md) | 새 전략 문서 템플릿 |

코드 주석의 `docs/STRATEGY_SPEC.md §2.x / §3.x` 참조 → `docs/strategies/ict_ote.md`의 같은 절.
