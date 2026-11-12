# UI 스모크 테스트 체크리스트

> 자동 화면 테스트(브라우저 조작·스크린샷)는 이 개발 환경에서 실행할 수 없었다.
> 대신 ① UI가 호출하는 API 흐름을 그대로 재현하는 스크립트와 ② 아래 수동 체크리스트를 제공한다.
> **스크립트 통과는 "브라우저에 올바르게 그려진다"를 증명하지 않는다.** 아래 수동 항목을 직접 확인해야 한다.

## 자동 (API 흐름)
```bash
backend/.venv/Scripts/tradingos serve                       # 터미널 1
backend/.venv/Scripts/python backend/scripts/ui_smoke.py    # 터미널 2 → 23개 검사
```
2026-11-11 결과: 23/23 통과. 실행할 때마다 `smoke-<시각>` 설정 버전이 하나 생긴다 (버전은 불변이라 삭제 기능 없음).
그 밖에 프론트엔드 `npm run build`(타입 검사 + 빌드) 통과.

## 수동 (브라우저) — http://localhost:5173

준비: `tradingos serve` + `cd frontend && npm run dev`. 5m 데이터가 있어야 한다 (`tradingos collect --tf 5m --days 180`).

### A. 전략 선택 (Strategy Lab)
- [ ] 전략 카드가 3개 보인다 (ICT Bearish OTE, Moving Average Crossover, EMA Cross). 카테고리·성숙도·실행 타임프레임 표시.
- [ ] 카드를 바꾸면 설정 영역의 파라미터 폼과 컴포넌트 목록이 그 전략 것으로 바뀐다.
- [ ] 상단 환경 표시: BACKTEST 활성, DEMO/LIVE 비활성.

### B. 파라미터 변경
- [ ] ICT에서 `Target → Minimum planned reward/risk`를 2로 바꾸면 "changed" 태그와 "UNSAVED CHANGES"가 보이고 검증 줄이 ✓ 상태다.
- [ ] `Swings → Swing left bars`에 0을 넣으면 검증 줄이 ✗로 바뀌고 해당 필드 아래에 오류가 표시된다.
- [ ] `Displacement → Method`를 `atr`로 바꾸면 Median lookback / Body ≥ median 필드가 흐려지고(비활성), ATR 필드가 활성화된다.
- [ ] 흐려진 필드에 넣었던 값은 기본값으로 **자동 복원**된다 (백엔드가 효과 없는 비기본값을 거부하므로).
- [ ] Reset 버튼이 기준 버전 값으로 되돌린다.

### C. 필수 컴포넌트 잠금
- [ ] "Swing / BOS structure", "Bearish impulse A->B"는 체크박스 없이 "● required"로 표시된다.
- [ ] FVG zone과 OTE zone을 둘 다 OFF로 하면 검증이 ✗가 되고 규칙 메시지가 나온다.
- [ ] 5m MSS confirmation을 OFF로 한 상태에서 FVG retest limit entry를 ON 하면 "requires 5m MSS confirmation" 오류가 나온다.

### D. 새 설정 버전 저장
- [ ] 유효한 변경 후 버전 이름을 입력하고 "Save as new version" → 안내 메시지, 기준 버전 목록에 `[DRAFT · user]`로 나타난다.
- [ ] 같은 이름으로 다시 저장하면 "already exists — versions are immutable" 오류.
- [ ] 변경 사항이 있는 동안 "Backtest →"와 "Create DEMO instance" 버튼이 비활성이다.

### E. ICT 차트 오버레이
- [ ] `v1-mss-close`, 최근 14일 → Analyze. 캔들 차트와 레이어 체크박스가 나타난다.
- [ ] 스윙(SH/SL), BOS/MSS 선, 약세 FVG 박스(빨강), 4H 바이어스 배경이 보인다. 레이어를 끄면 사라진다.
- [ ] "Recognition time (✓)" 레이어를 켜면 스윙 3봉 뒤에 ✓SH+3 / ✓SL+3 표시가 보인다.
- [ ] Chart timeframe을 5m / 15m / 4H로 바꿀 수 있다.
- [ ] Knowledge replay를 켜고 캔들을 클릭하면 그 시점 이후의 캔들·구조가 사라진다. ◀ ▶ 로 한 봉씩 이동.
- [ ] 아래 표(Setups / Signals / BOS·MSS / Swings / FVGs) 행을 클릭하면 차트가 그 위치로 이동한다.

### F. Signal Inspector
- [ ] "Inspect signals" → Funnel 표와 Conditions 표가 따로 보인다. Funnel의 "Continue"가 다음 단계의 "Entered"와 같다.
- [ ] 결과별 버튼(signal, bias_not_bearish, …)으로 후보 목록이 걸러진다. 셀: P(통과) F(실패) –(해당 없음) ·(평가 안 됨).
- [ ] 후보를 클릭하면 단계별 상태·계산값·이유가 보이고, 신호 후보는 "발생 시각 vs 인식 시각" 표가 보인다.
- [ ] MA Crossover로 Inspect 하면 "이 전략은 inspection을 기록하지 않는다"는 안내가 나온다 (오류가 아님).

### G. 백테스트 실행 (Backtest Lab)
- [ ] Strategy Lab의 "Backtest v1-mss-close →"로 이동하면 전략과 타임프레임(5m, 잠김)이 선택되어 있다.
- [ ] Run backtest → 지표 12개, 가격 차트의 진입/청산 마커, 자산곡선·낙폭, 거래 목록, 월별 성과가 나타난다.
- [ ] 거래 행을 클릭하면 차트가 그 거래로 이동하고 entry/stop/target 선이 보인다.
- [ ] "Audit trades for look-ahead" → 거래별 Signal / Prefix / Evidence 열이 ✓. 행을 펼치면 입력별 발생·인식 시각이 보인다.
- [ ] 감사 표의 "Chart →"를 누르면 Strategy Lab이 열리고 그 거래의 체결·청산 마커와 손절/목표 선이 ICT 오버레이 위에 표시된다.
- [ ] "Signal Inspector — recorded with this run" 패널이 보인다 (ICT 실행에만).
- [ ] 시작일 이전 이력이 부족한 기간을 고르면 "insufficient history" 오류가 나온다 (조용히 진행하지 않음).

### H. 결과 비교
- [ ] 같은 5m·같은 기간으로 MA Crossover v1도 실행한다.
- [ ] Runs 표에서 두 실행의 Cmp 체크박스를 선택하면 비교 패널이 나타나고 "Identical data, period, costs…" 안내가 보인다.
- [ ] 수수료를 바꿔 한 번 더 실행한 뒤 함께 비교하면 다른 조건이 강조되고 경고가 나온다.

문제가 보이면 브라우저 개발자 도구(F12) Console/Network의 오류와 함께 알려 주면 된다.
