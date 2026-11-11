# 새 전략 추가 가이드 (Strategy Development Guide)

> 목표: **공통 엔진·레지스트리·API·UI를 수정하지 않고** 플러그인 + 설정 + 테스트 + 문서만 추가해서 연구와 백테스트를 시작한다.
> 실제로 `ma_crossover`가 이 절차로 추가되었다 (공통 파일 변경 0개).

## 0. 시작 전에
- 전략 가설을 문장으로 적는다. 진입/청산 규칙을 **결정적으로**(같은 데이터 → 같은 결과) 정의할 수 있어야 한다.
- 모호한 개념은 수학적으로 정의하고, 정하지 못한 부분은 전략 문서의 "결정 필요"에 남긴다 (임의로 정하지 않는다).
- 수익성을 가정하지 않는다. `maturity`는 `educational` 또는 `research`로 시작한다.

## 1. 플러그인 파일 만들기 — `backend/src/tradingos/strategy/builtin/<name>.py`

```python
from typing import Literal
import numpy as np, pandas as pd
from pydantic import BaseModel
from tradingos.strategy.base import Strategy, empty_signals
from tradingos.strategy.indicators import atr
from tradingos.strategy.schema import ComponentSpec, DataRequirements, StrategyDefinition, group, param

class RsiFilter(BaseModel):
    enabled: bool = param(False, label="Use RSI filter")
    period: int = param(14, label="RSI period", unit="bars", ge=2, le=200,
                        depends_on={"path": "rsi_filter.enabled", "equals": True})

class BollingerReversionParams(BaseModel):
    period: int = param(20, label="Band period", unit="bars", ge=5, le=500, step=1)
    width: float = param(2.0, label="Band width", unit="σ", gt=0, le=6, step=0.1)
    atr_stop_mult: float = param(2.0, label="Stop", unit="× ATR", gt=0, le=20)
    rsi_filter: RsiFilter = group(RsiFilter, label="RSI filter")

class BollingerReversion(Strategy):
    definition = StrategyDefinition(
        strategy_id="bollinger_reversion", name="Bollinger Reversion", definition_version="1.0.0",
        category="mean_reversion", maturity="research", description="...",
        data=DataRequirements(base_timeframes=["15m", "1H"], instruments=["BTC-USDT-SWAP"]),
        components=[
            ComponentSpec(id="band_entry", label="Band re-entry", role="required"),
            ComponentSpec(id="rsi_filter", label="RSI filter", role="optional", toggle_path="rsi_filter.enabled"),
        ],
        docs="docs/strategies/bollinger_reversion.md",
    )
    Params = BollingerReversionParams

    def validate_config(self):            # 스키마로 표현 못 하는 규칙만
        return []

    def warmup_bars(self) -> int:
        return self.params.period * 3

    def generate_signals(self, bars: pd.DataFrame) -> pd.DataFrame:
        sig = empty_signals(bars.index)
        # ... 반드시 인과적으로 (rolling/ewm/shift(+k) 만; shift(-k) 금지) ...
        return sig
```

규칙:
- 입력은 `bars`뿐. 네트워크·파일·DB·거래소 호출 금지. 수량 계산 금지 (Risk Engine 담당).
- `warmup_bars()` 이전에는 진입 신호를 내지 않는다.
- 데이터 길이에 따라 결과가 바뀌는 로직 금지 (예: "마지막 봉이면…"). 계약 테스트가 잡아낸다.
- 상위 TF가 필요하면 `features.mtf.resample` + `known_ts` 기준 정렬 (ICT 참고).

## 2. 설정 파일 — `backend/config/strategies/<strategy_id>/v1.yaml`

```yaml
strategy_id: bollinger_reversion
version: v1
status: DRAFT
description: "첫 번째 연구 가설: 20/2σ, RSI 필터 OFF"
params:
  period: 20
  width: 2.0
```
- 생략한 값은 플러그인 기본값이 적용된다 (resolved params로 기록).
- **한 번 사용된 버전 파일은 수정하지 않는다.** 바꾸려면 `v2.yaml`을 만들거나 UI에서 "Save as new version".

## 3. 테스트 — `backend/tests/test_<name>.py`

계약 테스트(`test_plugin_contract.py`)가 **자동으로** 새 플러그인을 검사한다: 메타데이터, 문서 존재, 파라미터 스키마,
컴포넌트 토글, 설정 해석, 인과성, 증분=일괄, 연구 화면, 생성 문서 최신성.
전략 전용 테스트에는 다음을 넣는다:
1. 손으로 계산 가능한 합성 OHLC로 진입/손절/목표 값 검증 (`tests/synth.py` 참고)
2. 파라미터 검증 실패 사례 (`ConfigValidationError`의 `path`)
3. 각 선택 컴포넌트 ON/OFF가 신호에 미치는 영향
4. 신호가 실제로 나오는 설정에서의 `check_causality` (신호 0개짜리 인과성 테스트는 아무것도 증명하지 못한다)
5. API 경로: `/api/strategies/<id>/validate`, 새 버전 저장, `/api/backtests`

## 4. 문서
1. `docs/strategies/<name>.md` — [_TEMPLATE.md](strategies/_TEMPLATE.md) 복사 (개요, 진입/청산, 데이터, TF, 선택 조건, 위험, 구현·테스트 상태, 변경 기록)
2. `cd backend && .venv/Scripts/tradingos docs` → `docs/strategies/generated/<id>.md` 자동 생성 (파라미터 참조). 손으로 쓰지 않는다.

## 5. 확인
```bash
cd backend
.venv/Scripts/tradingos strategies        # 발견 확인
.venv/Scripts/python -m pytest            # 계약 + 전용 테스트
```
UI: Strategy Lab에 카드가 자동으로 나타나고, 파라미터 폼·컴포넌트 토글·연구 차트·버전 저장·DEMO 인스턴스 준비·Backtest Lab 실행/비교가 모두 동작한다.

## 6. 커스텀 연구 화면이 필요할 때
기본 화면(캔들 + 지표선 + 신호 + SL/TP)으로 부족하면 `research_view()`를 재정의해 `ResearchView`에 rect/segment/marker/table을 추가한다.
UI 코드는 쓰지 않는다. 예: `strategy/builtin/ict_ote_view.py`.

## 7. 체크리스트
- [ ] `strategy_id` 고유, `definition_version` 설정
- [ ] 필수/선택 컴포넌트 구분, 의존성·규칙 선언
- [ ] 모든 파라미터에 label/unit/범위
- [ ] YAML v1 + 전략 문서 + 생성 문서
- [ ] 전용 테스트 + 계약 테스트 통과
- [ ] 전략 문서에 "수익성 미검증" 명시
