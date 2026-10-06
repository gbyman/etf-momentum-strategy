# ETF Momentum Strategy 수정 후 코드 리뷰

작성일: 2026-09-30

## 요약

최근 수정으로 기존 리뷰에서 지적했던 핵심 문제 상당수가 개선되었다. 특히 후보 유니버스와 벤치마크/현금성 ETF 분리, stale 캐시 경고, 안정적인 캐시 키, 현금 티커 검증, 빈 슬롯 현금 배분, 그룹별 중복 제한, `rank_by` 옵션, walk-forward 테스트가 추가된 점은 긍정적이다.

현재 상태는 ETF 모멘텀 전략의 연구, 관찰, 파라미터 비교 용도로는 꽤 적절하다. 다만 실전 의사결정에 더 가깝게 쓰려면 빈 후보군 처리, walk-forward 점수 계산, 월간 보유 회계/체결 가정을 보완하는 것이 좋다.

검증 결과:

- `uv run pytest`: 25개 테스트 모두 통과
- `rank`, `backtest`, `compare`, `walkforward` CLI 스모크 테스트 정상 동작

## 개선된 점

### 1. 후보군 분리

`rank_momentum()`에 `candidate_tickers`가 추가되어 벤치마크와 현금성 ETF가 일반 투자 후보에 섞이는 문제가 크게 개선되었다.

관련 파일:

- `src/etf_momentum/signals.py`
- `src/etf_momentum/backtest.py`
- `src/etf_momentum/cli.py`

### 2. 캐시 안정성 및 신선도

긴 티커 목록 캐시 키가 Python 내장 `hash()` 대신 `sha256` 기반으로 바뀌었다. 또한 `latest` 캐시가 오래된 경우 재다운로드를 시도하고, 가격 데이터 기준일이 오래되면 경고를 출력한다.

관련 파일:

- `src/etf_momentum/data.py`

### 3. 현금성 ETF 검증

`cash_ticker`가 지정되어 있는데 가격 데이터에 없으면 `ValueError`를 발생시킨다. 설정 오류가 조용히 0% 비중으로 처리되는 문제가 개선되었다.

관련 파일:

- `src/etf_momentum/backtest.py`

### 4. 빈 슬롯 현금 배분

`top_n`보다 선택 가능한 ETF가 적으면 빈 슬롯을 현금성 ETF로 채운다. 예를 들어 `top_n=3`이고 후보가 1개면 후보 1/3, 현금 2/3 구조가 된다.

관련 파일:

- `src/etf_momentum/backtest.py`

### 5. 그룹별 중복 제한

유니버스 CSV에 `group` 컬럼이 추가되었고, `max_per_group` 옵션으로 같은 그룹의 ETF가 과도하게 중복 편입되는 것을 제한할 수 있다.

관련 파일:

- `config/universe_us_sectors.csv`
- `config/universe_kr_etfs.csv`
- `src/etf_momentum/backtest.py`
- `src/etf_momentum/cli.py`

### 6. 랭킹 기준 선택

`rank_by` 옵션이 추가되어 단순 모멘텀(`weighted_momentum`)과 위험조정 모멘텀(`risk_adjusted_score`)을 비교할 수 있다.

관련 파일:

- `src/etf_momentum/signals.py`
- `src/etf_momentum/cli.py`

### 7. Walk-forward 분석 추가

파라미터 조합을 과거 전체 성과로만 고르는 위험을 줄이기 위해 walk-forward 명령이 추가되었다. out-of-sample 관점에서 조합 선택을 검토할 수 있게 된 점은 큰 개선이다.

관련 파일:

- `src/etf_momentum/walkforward.py`
- `src/etf_momentum/cli.py`

## 남은 주요 이슈

### 1. 빈 랭킹 DataFrame에서 `KeyError` 발생

중요도: 높음

`candidate_tickers`가 모두 가격 데이터에 없거나 특정 시점에 모든 후보가 탈락하면 `rank_momentum()`이 빈 DataFrame을 반환한다. 이때 `_pick_tickers()`가 `ranking["eligible"]`에 접근하면서 `KeyError`가 발생한다.

관련 위치:

- `src/etf_momentum/backtest.py`
- `_select_weights()`
- `_pick_tickers()`

재현 결과:

```text
KeyError 'eligible'
```

개선 제안:

- `_select_weights()`에서 `ranking.empty`이면 즉시 현금으로 이동한다.
- 또는 `rank_momentum()`이 빈 결과를 반환할 때도 최소 컬럼 구조를 유지한다.
- 테스트 추가:
  - 후보 티커가 모두 데이터에 없는 경우
  - 후보 티커가 존재하지만 모든 후보가 NaN/불충분 데이터로 탈락하는 경우

### 2. Walk-forward Calmar 점수가 무손실 후보를 제거할 수 있음

중요도: 중간

`walkforward.score()`에서 `max_drawdown == 0`이면 `NaN`을 반환한다. 이후 `dropna()`로 해당 후보가 제거된다. 현금성 전략이나 매우 방어적인 조합처럼 훈련 구간에서 손실이 없는 후보가 선택 대상에서 빠질 수 있다.

관련 위치:

- `src/etf_momentum/walkforward.py`
- `score()`

개선 제안:

- `max_drawdown == 0`이고 `cagr > 0`이면 `np.inf` 또는 큰 양수 반환
- `max_drawdown == 0`이고 `cagr == 0`이면 `0.0` 반환
- `max_drawdown == 0`이고 `cagr < 0`이면 음수 반환
- 위 정책을 명시하고 테스트로 고정

### 3. 월간 리밸런싱 전략인데 보유 기간 수익률은 고정비중 일간 계산

중요도: 중간

현재 보유 기간 수익률은 리밸런싱 시점의 비중을 매일 그대로 곱하는 방식이다. 이는 월간 buy-and-hold라기보다 매일 목표 비중으로 유지되는 포트폴리오에 가깝다.

관련 위치:

- `src/etf_momentum/backtest.py`
- `period_returns = returns.loc[holding_dates, weights.index].mul(weights, axis=1).sum(axis=1)`

영향:

- ETF 수준에서는 차이가 작을 수 있지만, 고변동 테마 ETF가 포함된 전략에서는 CAGR/MDD가 실제 월간 리밸런싱과 달라질 수 있다.

개선 제안:

- 리밸런싱 시점에만 목표 비중을 설정하고, 다음 리밸런싱 전까지 각 자산의 누적 수익률로 포트폴리오 가치를 계산한다.
- 옵션으로 `portfolio_accounting=daily_rebalanced|buy_and_hold`를 둘 수도 있다.

### 4. 그룹 태깅 품질에 성과가 크게 의존

중요도: 중간

`SMH/SOXX`, `XBI/IBB`, `VNQ/XLRE` 등 일부 중복 노출은 그룹으로 묶였지만, `TAN/ICLN`처럼 성격이 가까운 ETF가 아직 같은 그룹으로 묶이지 않았다.

관련 위치:

- `config/universe_us_sectors.csv`
- `config/universe_kr_etfs.csv`

개선 제안:

- 유사 테마 ETF 그룹을 더 촘촘하게 정리한다.
- `group`과 별개로 `risk_bucket` 또는 `theme_bucket` 컬럼을 추가해 실험한다.
- 그룹 제한을 기본값으로 둘지, 명령별 명시 옵션으로 둘지 README에 설명한다.

## 추가로 검토하면 좋은 부분

### 체결 타이밍 명시

현재는 리밸런싱일 종가 기준 신호를 만들고 다음 거래일부터 수익률을 반영한다. 심한 당일 룩어헤드는 피하고 있지만, 실제 체결을 다음날 시가로 보는지 다음날 종가로 보는지 명확하지 않다.

추천:

- README와 결과 파일에 체결 가정을 명시한다.
- OHLC 데이터를 쓰는 다음 단계에서는 다음 거래일 시가 체결 옵션을 추가한다.

### 결과 파일 메타데이터

`summary.json`에 실행 명령, 유니버스 파일, 기준일, 파라미터, 코드 버전 또는 실행 시각이 저장되면 나중에 결과를 해석하기 쉬워진다.

추천:

- `run_config.json` 또는 `metadata.json` 저장
- `summary.json`에 핵심 파라미터 포함

### 생존편향과 상장일

현재 유니버스는 현재 존재하는 ETF 목록 중심이다. 장기 백테스트에서는 상장폐지, 상품 변경, 상장 초기 유동성 문제가 반영되지 않을 수 있다.

추천:

- 유니버스 CSV에 `start_date`, `end_date` 컬럼 추가
- 리밸런싱 시점에 실제 투자 가능했던 ETF만 후보로 사용

## 우선순위

1. 빈 랭킹 DataFrame 처리
2. walk-forward Calmar의 `max_drawdown == 0` 정책 수정
3. 월간 buy-and-hold 포트폴리오 회계 추가 또는 현재 가정 명시
4. 유니버스 그룹 태깅 정리
5. 결과 메타데이터 저장
6. 체결 타이밍 옵션화
7. 상장일/유효기간 기반 유니버스 관리

## 현재 판단

수정 후 코드는 ETF 모멘텀 전략 연구용으로 충분히 좋아졌다. 기존의 가장 큰 구조적 문제였던 후보군 오염, 캐시 불안정성, 현금 배분 문제는 상당 부분 해결되었다.

다만 실전 투자 의사결정에 쓰기 전에는 빈 후보군 예외와 walk-forward 점수 계산 문제를 먼저 고치는 것이 좋다. 그 다음 백테스트 회계와 체결 가정을 명확히 하면 전략 결과의 신뢰도가 더 높아질 것이다.

이 문서는 투자 조언이 아니라 코드와 전략 연구 프로세스에 대한 리뷰 기록이다.
