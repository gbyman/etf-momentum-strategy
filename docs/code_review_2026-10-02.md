# ETF Momentum Strategy Code Review - 2026-10-02

## 결론

현재 소스는 ETF 모멘텀 전략의 연구 및 백테스트 목적에는 전반적으로 적절하다. 이전에 문제가 될 수 있었던 후보 유니버스 분리, 현금 배분, 보유기간 비중 드리프트, 첫 리밸런싱 기준 수익률 측정, 유동성 필터의 미래 데이터 사용 방지 등이 상당히 개선되었다.

다만 실전 운용 전 단계의 연구 도구로 더 신뢰도를 높이려면 아래 세 가지를 추가로 보완하는 것이 좋다. 가장 중요한 남은 리스크는 `risk_adjusted_score`를 순위 기준으로 쓸 때 점수가 `NaN`이어도 후보가 선택될 수 있는 부분이다.

## 발견 사항

### 1. Medium - `risk_adjusted_score`가 `NaN`이어도 후보가 선택될 수 있음

관련 위치:

- `src/etf_momentum/signals.py:123`
- `src/etf_momentum/signals.py:156`
- `src/etf_momentum/backtest.py:53`

`risk_adjusted_score`는 `weighted_momentum / realized_volatility`로 계산된다. 그런데 변동성 계산에 필요한 데이터가 부족하면 `realized_volatility`와 `risk_adjusted_score`가 `NaN`이 된다. 현재 eligible 판정은 양의 모멘텀, SMA 추세, 상대강도, 유동성만 확인하고, 선택된 `rank_by` 점수가 유효한지는 확인하지 않는다.

따라서 `--rank-by risk_adjusted_score`를 사용하면서 `--volatility-window`를 너무 길게 잡으면 모든 후보의 risk score가 `NaN`인데도 순위가 붙을 수 있다. 이 경우 실제 위험조정 점수로 선별한 것이 아니라 입력 순서에 가까운 결과가 될 수 있다.

권장 수정:

- `rank_by`로 사용하는 점수가 finite가 아닐 경우 `eligible=False` 처리한다.
- 또는 백테스트 warm-up 기간에 `config.momentum.volatility_window`를 포함해 변동성 계산이 가능한 시점부터 리밸런싱한다.
- 두 방식을 함께 적용하면 CLI 실험과 라이브러리 호출 모두에서 더 안전하다.

예상 수정 방향:

```python
score_value = risk_adjusted_score if config.rank_by == "risk_adjusted_score" else weighted_momentum
score_ok = bool(np.isfinite(score_value))
eligible = bool(score_ok and positive_momentum and above_sma and relative_ok and liquid)
```

그리고 백테스트의 `min_history`에는 `volatility_window`를 포함한다.

```python
min_history = max(
    max(config.momentum.lookbacks) + config.momentum.skip_days + 2,
    config.momentum.sma_window,
    config.momentum.volatility_window,
)
```

### 2. Low/Medium - 유동성 필터 사용 시 실행 메타데이터가 부족함

관련 위치:

- `src/etf_momentum/cli.py:227`
- `src/etf_momentum/reporting.py:28`
- `src/etf_momentum/reporting.py:39`

`--min-avg-traded-value`를 켜면 백테스트와 랭킹 결과는 가격 데이터뿐 아니라 `traded_value` 데이터에도 의존한다. 하지만 현재 `*_run_config.json`에는 `price_data`만 기록되고, 거래대금 데이터의 기간, 티커, 캐시 상태는 기록되지 않는다.

이 상태에서는 나중에 같은 실행을 재현하거나 결과를 감사할 때 "가격 데이터는 언제까지였는지"는 확인할 수 있지만, "유동성 판단에 사용한 거래대금 데이터는 언제까지였는지"는 확인하기 어렵다.

권장 수정:

- `save_run_metadata()`가 선택적으로 `traded_value`를 받아 `traded_value_data` 섹션을 기록하게 한다.
- `rank`, `weekly`, `backtest`, `compare`, `walkforward` 명령에서 유동성 필터를 쓸 때 해당 데이터를 함께 넘긴다.

예상 메타데이터 형태:

```json
"traded_value_data": {
  "first_date": "2018-01-02",
  "last_date": "2026-10-01",
  "tickers": ["BIL", "SPY", "..."]
}
```

### 3. Low - 콘솔 랭킹 출력에서 유동성 진단 컬럼이 보이지 않음

관련 위치:

- `src/etf_momentum/cli.py:144`
- `src/etf_momentum/signals.py:154`
- `src/etf_momentum/signals.py:155`

랭킹 CSV에는 `avg_traded_value`, `liquid` 컬럼이 저장된다. 하지만 `rank` 명령의 콘솔 출력에는 이 컬럼들이 포함되지 않는다. 사용자가 `--min-avg-traded-value`를 켰을 때 콘솔만 보면 어떤 ETF가 모멘텀 때문에 탈락했는지, 유동성 때문에 탈락했는지 바로 알기 어렵다.

권장 수정:

- `args.min_avg_traded_value is not None`일 때만 콘솔 출력 컬럼에 `avg_traded_value`, `liquid`를 추가한다.
- 또는 항상 `liquid`만 출력하고, 상세 수치는 CSV에서 확인하도록 한다.

## 긍정적으로 확인한 부분

- 후보 유니버스가 벤치마크 및 현금성 ETF와 분리되어 있다.
- `buy_and_hold` 회계가 추가되어 리밸런싱 사이 비중 드리프트를 반영한다.
- `daily_rebalanced` 회계도 선택지로 남아 있어 이전 방식과 비교할 수 있다.
- 전략 및 벤치마크 성과가 첫 리밸런싱 시점부터 1.0으로 재기준화된다.
- 후보가 부족할 때 남는 비중은 현금성 ETF 또는 미투자 현금으로 이동한다.
- 유동성 필터는 리밸런싱일 이후 데이터를 사용하지 않는다.
- 거래대금은 수정주가가 아닌 실제 종가 x 거래량 기준으로 별도 캐시된다.
- 유동성 데이터의 결측 거래량은 forward-fill하지 않아 가짜 유동성을 만들지 않는다.
- walk-forward 선택은 훈련 구간만 사용하고, zero drawdown 케이스도 선택 가능하게 처리되어 있다.

## 검증 결과

실행한 검증:

```bash
uv run pytest
```

결과:

```text
46 passed
```

추가 CLI 스모크 테스트:

```bash
uv run etf-momentum rank \
  --universe config/universe_us_sectors.csv \
  --benchmark SPY \
  --start 2018-01-01 \
  --top 5 \
  --cash BIL \
  --min-avg-traded-value 20000000 \
  --output-dir /tmp/etf_momentum_review4_rank
```

```bash
uv run etf-momentum backtest \
  --universe config/universe_us_sectors.csv \
  --benchmark SPY \
  --start 2018-01-01 \
  --top-n 3 \
  --rebalance ME \
  --cash BIL \
  --risk-on-benchmark \
  --transaction-cost-bps 5 \
  --accounting buy_and_hold \
  --min-avg-traded-value 20000000 \
  --output-dir /tmp/etf_momentum_review4_backtest_liq
```

두 명령 모두 정상 실행되었다. `rank` 결과 CSV에는 `avg_traded_value`, `liquid` 컬럼이 포함되는 것을 확인했다.

## 우선순위

1. `risk_adjusted_score`의 `NaN` 후보 선택 방지
2. 유동성 데이터 메타데이터 기록
3. 유동성 필터 사용 시 콘솔 출력 개선

위 세 가지를 보완하면 현재 코드는 ETF 모멘텀 전략의 반복 연구, 파라미터 비교, walk-forward 검증 용도로 더 안정적으로 사용할 수 있다.
