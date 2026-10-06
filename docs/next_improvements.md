# ETF Momentum Strategy 다음 개선안

작성일: 2026-09-30

## 요약

현재 코드는 ETF 모멘텀 전략의 연구, 관찰, 백테스트 용도로는 꽤 탄탄해졌다. 후보군 분리, 캐시 신선도, 현금 배분, 그룹 제한, walk-forward, buy-and-hold 회계가 반영되었고 테스트도 충분히 늘었다.

남은 작업은 큰 구조 결함보다는 백테스트 정밀도, 결과 해석 편의성, 실전 운용 가정 보강에 가깝다.

## 우선순위

### 1. All-zero 전략 수익률 구간 처리

중요도: 높음  
난이도: 낮음

현재 `run_backtest()`는 equity curve 시작점을 다음 방식으로 정한다.

```python
first_active = strategy_returns.ne(0).idxmax()
equity = equity.loc[first_active:]
```

전략 수익률이 전 기간 0이면 `idxmax()`는 첫 번째 날짜를 반환한다. 이 경우 전략은 실제로 warm-up 이후부터 평가되어야 하는데, benchmark는 전체 가격 데이터 시작일부터 측정될 수 있다.

개선 방향:

- `strategy_returns.ne(0).any()`를 먼저 확인한다.
- 비영(非 0) 수익률이 없으면 첫 holding date 또는 첫 position date를 기준으로 equity를 자른다.
- 전 기간 무투자/현금 상태인 경우 benchmark 비교 기간이 의도와 맞는지 테스트로 고정한다.

예상 수정 위치:

- `src/etf_momentum/backtest.py`
- `tests/test_accounting.py` 또는 `tests/test_fixes.py`

### 2. 결과 테이블에 `accounting` 컬럼 추가

중요도: 중간  
난이도: 낮음

`backtest_run_config.json`에는 `accounting`이 기록되지만, `comparison.csv`와 `walkforward_grid.csv`만 보면 어떤 회계 방식으로 계산했는지 바로 알기 어렵다.

개선 방향:

- `compare` 결과 row에 `accounting` 추가
- `walkforward_grid.csv`의 params에 `accounting` 추가
- 화면 출력 컬럼에도 필요하면 `accounting` 표시

예상 수정 위치:

- `src/etf_momentum/cli.py`
- 관련 CLI 테스트가 없다면 간단한 단위 테스트 또는 스모크 테스트 추가

### 3. 체결가 모델 보수화

중요도: 중간  
난이도: 중간

현재 가정은 다음과 같다.

- 리밸런싱일 종가까지의 가격으로 신호 계산
- 같은 리밸런싱일 종가에 체결
- 다음 거래일부터 수익률 반영

이 가정은 신호와 체결이 같은 종가에 이루어진다고 보기 때문에 실제 운용보다 다소 유리할 수 있다.

개선 방향:

- `execution` 옵션 추가
  - `same_close`: 현재 방식
  - `next_close`: 신호일 다음 거래일 종가 체결
  - 향후 OHLC 데이터 사용 시 `next_open`
- 결과 메타데이터에 체결 방식 기록
- 체결 방식별 성과 비교 가능하게 `compare`에도 옵션 추가

예상 수정 위치:

- `src/etf_momentum/backtest.py`
- `src/etf_momentum/cli.py`
- `src/etf_momentum/reporting.py`
- `README.md`

### 4. 유니버스 그룹 태깅 보강

중요도: 중간  
난이도: 낮음

현재 `group` 컬럼으로 중복 노출을 제한할 수 있다. 다만 `TAN/ICLN`처럼 성격이 가까운 ETF가 아직 별도 그룹으로 남아 있을 수 있다.

개선 방향:

- 유사 테마 ETF를 다시 검토한다.
- 예시:
  - `TAN`, `ICLN`: clean_energy
  - 필요 시 `XLK`, `IGV`, `CIBR`: technology/software/cyber 범주를 별도로 둘지 검토
- 너무 넓게 묶으면 분산 후보가 줄어들 수 있으므로, 상관계수와 구성종목을 함께 확인한다.

예상 수정 위치:

- `config/universe_us_sectors.csv`
- `config/universe_kr_etfs.csv`
- README의 유니버스 설명

### 5. 상장일/유효기간 기반 후보 필터

중요도: 중간  
난이도: 중간

현재 유니버스는 현재 존재하는 ETF 목록 중심이다. 장기 백테스트에서는 상장폐지, 상품 변경, 상장 초기 유동성 문제를 반영하기 어렵다.

개선 방향:

- 유니버스 CSV에 선택 컬럼 추가
  - `start_date`
  - `end_date`
  - `min_history_days`
- 리밸런싱 날짜 기준으로 투자 가능했던 ETF만 후보에 포함
- 상장 초기 일정 기간은 제외

예상 수정 위치:

- `config/*.csv`
- `src/etf_momentum/data.py` 또는 후보 필터 전용 helper
- `src/etf_momentum/backtest.py`
- `src/etf_momentum/signals.py`

### 6. 성과지표 개선

중요도: 낮음~중간  
난이도: 낮음~중간

현재 `sharpe_like`는 `CAGR / volatility`에 가깝다. 일반적으로 말하는 Sharpe ratio와 다르므로 해석에 주의가 필요하다.

개선 방향:

- 기존 `sharpe_like`는 유지하되 이름을 명확히 하거나 일반 Sharpe를 별도로 추가
- 추가 후보:
  - annualized Sharpe
  - Sortino ratio
  - hit rate
  - average monthly return
  - turnover 합계/평균
  - 거래비용 합계
  - benchmark 대비 excess CAGR

예상 수정 위치:

- `src/etf_momentum/backtest.py`
- `src/etf_momentum/reporting.py`
- README의 성과지표 설명

## 추천 작업 순서

1. All-zero 수익률 시작점 처리
2. 결과 테이블에 `accounting` 기록
3. 체결가 모델 옵션화
4. 유니버스 그룹 태깅 보강
5. 상장일/유효기간 필터
6. 성과지표 확장

## 현재 판단

가장 먼저 할 일은 작고 확실한 1~2번이다. 이 둘은 구현 부담이 낮고 결과 해석의 혼선을 줄인다. 이후 실전성 관점에서는 체결가 모델과 상장일/유효기간 필터가 중요하다. 그룹 태깅과 성과지표는 전략 해석을 더 정교하게 만드는 작업으로 보면 된다.
