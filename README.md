# ETF Momentum Strategy

ETF 섹터 모멘텀을 찾고 백테스트하는 작은 Python 도구입니다.

핵심 아이디어는 단순합니다.

1. 투자할 ETF 유니버스를 정합니다.
2. 3개월, 6개월, 12개월 수익률로 상대강도 점수를 계산합니다.
3. 200일 이동평균 위에 있고 모멘텀이 양수인 ETF만 후보로 둡니다.
4. 상위 ETF에 동일비중으로 투자합니다.
5. 매월 리밸런싱하며 조건을 잃은 ETF는 교체합니다.

## 설치

```bash
cd /Users/dc_macair/Documents/Research/etf_momentum_strategy
uv sync
```

## 현재 주도 섹터 랭킹 보기

```bash
uv run etf-momentum rank \
  --universe config/universe_us_sectors.csv \
  --benchmark SPY \
  --lookback-years 3 \
  --top 10
```

결과 CSV는 `results/latest_ranking.csv`에 저장됩니다.

순위 표 아래에는 **직전 월말 종가 기준으로 실제 선택될 ETF**가 함께 나옵니다. 백테스트와 같은 규칙(시장 필터 → 그룹 제한 → 상위 N개)을 쓰며, 결과는 `results/latest_selection.csv`(`selected` / `group_limit` / `cash`)에 저장됩니다. 직전 월말은 이미 끝난 가장 최근 달의 마지막 거래일입니다(10월 7일에 실행하면 9월 말). 선택 규칙 옵션은 백테스트와 같습니다.

```bash
uv run etf-momentum rank --universe config/universe_kr_pension.csv \
  --benchmark 069500.KS --cash 153130.KS --account pension \
  --lookback-years 3 --min-avg-traded-value 1e9 \
  --top-n 4 --max-per-group 1 --risk-on-benchmark
```

`--top-n`(기본 3), `--max-per-group`(기본 1, 0이면 제한 없음), `--risk-on-benchmark`(기본 끔), `--risk-on-ticker`. 비중은 출력하지 않습니다.
`--benchmark`와 `--cash` ETF는 유니버스 파일에 있어도 랭킹 후보에서 제외됩니다. 벤치마크는 상대강도 비교용, 현금성 ETF는 대기 자산으로만 쓰입니다.
한국 유니버스에서는 `--cash 153130.KS`를 함께 지정해야 단기채 ETF가 후보에서 빠집니다.
화면에 표시되는 `return_1w`, `return_1m`, `return_3m`, `return_6m`, `return_12m`, `return_ytd`는 현재 기준 실제 기간 수익률입니다.
`weighted_momentum`은 최근 1개월을 제외한 3/6/12개월 모멘텀 점수라서 기간 수익률과 용도가 다릅니다.

한국 ETF 유니버스를 보려면 야후파이낸스용 `.KS` 티커가 들어간 파일을 사용합니다.

```bash
uv run etf-momentum rank \
  --universe config/universe_kr_etfs.csv \
  --benchmark 069500.KS \
  --cash 153130.KS \
  --lookback-years 3 \
  --top 15
```

## 주기적 실행 원칙

이 전략은 매일 확인하는 것보다 주간 관찰과 월간 실행에 더 적합합니다.

- 매주 토요일: `weekly`로 주도 섹터 변화를 관찰합니다. 금요일 장 마감 데이터까지 반영된 상태에서 확인하는 용도입니다.
- 매월 첫 주말: `rank`와 `backtest`를 확인하고, 정해둔 월간 리밸런싱 규칙에 따라 실제 교체 여부를 판단합니다.
- 분기 말: `compare`로 리밸런싱 주기, 보유 ETF 수, 비중 방식, 상대강도 필터를 다시 점검합니다.

매주 랭킹을 보더라도 매매는 월 1회 규칙으로 제한하는 편이 좋습니다. 주간 확인은 행동이 아니라 관찰을 위한 루틴입니다.

## 매주 토요일 리더십 점검

`weekly` 명령은 현재 랭킹을 이전 실행 기록과 비교해서 주도 섹터 변화표를 보여줍니다.

```bash
uv run etf-momentum weekly \
  --universe config/universe_kr_etfs.csv \
  --benchmark 069500.KS \
  --cash 153130.KS \
  --lookback-years 3 \
  --top 15
```

미국 ETF 예시:

```bash
uv run etf-momentum weekly \
  --universe config/universe_us_sectors.csv \
  --benchmark SPY \
  --lookback-years 3 \
  --top 15
```

출력 표는 네 가지 질문에 바로 대응합니다.

| 질문 | 확인 컬럼 |
| --- | --- |
| 어떤 섹터가 계속 상위권인가? | `still_top` |
| 새로 올라오는 섹터가 있는가? | `new_or_rising` |
| 기존 주도 섹터가 밀리고 있는가? | `leader_fading` |
| 1개월 수익률이 꺾였는가? | `one_month_rolling_over`, `return_1m`, `return_1w` |

주요 컬럼:

```text
rank                  현재 모멘텀 순위
previous_rank         이전 weekly 실행 당시 순위
rank_change           이전 순위 - 현재 순위. 양수면 순위 상승
still_top             현재와 이전 모두 leader-rank 안에 있음
new_or_rising         새로 watch-rank 안에 진입했거나 순위가 빠르게 상승
leader_fading         이전 주도권 후보가 watch-rank 밖으로 밀렸거나 순위가 악화
one_month_rolling_over
                      1개월 수익률이 음수이거나, 최근 1주 수익률이 음수이면서 1개월 수익률이 이전보다 둔화
status                위 신호를 요약한 상태값
```

기본값은 `watch_rank=10`, `leader_rank=5`입니다. 예를 들어 상위 15개까지 더 넓게 감시하려면:

```bash
uv run etf-momentum weekly \
  --universe config/universe_kr_etfs.csv \
  --benchmark 069500.KS \
  --cash 153130.KS \
  --watch-rank 15 \
  --leader-rank 5 \
  --top 20
```

`weekly`는 `results/rank_history.csv`에 실행 기록을 저장합니다. 첫 실행에는 비교 대상이 없으므로 기준 스냅샷을 만드는 의미가 크고, 두 번째 실행부터 변화표가 본격적으로 유용해집니다.

## 백테스트

```bash
uv run etf-momentum backtest \
  --universe config/universe_us_sectors.csv \
  --benchmark SPY \
  --start 2010-01-01 \
  --top-n 3 \
  --rebalance M \
  --cash BIL
```

한국 ETF 예시:

```bash
uv run etf-momentum backtest \
  --universe config/universe_kr_etfs.csv \
  --benchmark 069500.KS \
  --start 2015-01-01 \
  --top-n 3 \
  --rebalance M \
  --cash 153130.KS \
  --risk-on-benchmark \
  --transaction-cost-bps 5
```

결과는 `results/` 아래에 저장됩니다.

- `summary.json`: 전략 성과 요약
- `equity_curve.csv`: 전략/벤치마크 누적 수익률
- `positions.csv`: 리밸런싱별 보유 ETF
- `latest_ranking.csv`: 마지막 날짜 기준 랭킹
- `equity_curve.png`: 누적 수익률 차트

## 주요 옵션

```text
--top-n 3              매월 보유할 ETF 개수
--sma-window 200       추세 필터 이동평균 기간
--skip-days 21         최근 1개월을 모멘텀 계산에서 제외
--weights 0.3,0.3,0.4  3개월, 6개월, 12개월 모멘텀 가중치
--cash BIL             후보가 부족하거나 시장 위험 회피 시 보유할 현금성 ETF
                        (랭킹 후보에서 제외, 가격 데이터가 없으면 오류. none이면 미투자 현금)
--risk-on-benchmark    벤치마크가 200일선 아래면 현금성 ETF로 이동
--risk-on-ticker 069500.KS,360750.KS
                        시장 필터 기준 티커(기본: --benchmark). 여러 개면 하나라도 200일선 위일 때 투자
--require-relative-strength
                        벤치마크보다 모멘텀이 강한 ETF만 후보로 사용
--min-relative-momentum 0.02
                        벤치마크 대비 최소 초과 모멘텀 2% 요구
--weighting inverse_vol
                        동일비중 대신 변동성 역가중 사용
--max-position-weight 0.5
                        한 ETF 최대 비중 제한. 초과분은 다른 후보나 현금으로 이동
--rank-by risk_adjusted_score
                        순위 기준. 기본 weighted_momentum, risk_adjusted_score는 모멘텀/변동성
--max-per-group 1       유니버스 group이 같은 ETF는 최대 N개만 보유 (기본 1, 0이면 제한 없음)
--min-avg-traded-value 1e9
                        최근 63거래일 평균 일 거래대금(종가×거래량, ETF 통화 기준)이 이 값 미만인 ETF는 후보에서 제외.
                        예: 한국 1e9 = 10억 원, 미국 2e7 = 2천만 달러. 기본은 필터 없음
--target-vol 0.15      변동성 목표. 리밸런싱 때 추정 포트폴리오 변동성이 목표보다 크면 투자 비중을
                        목표/추정치만큼 줄이고 나머지는 현금으로 보유 (레버리지 없음). 기본은 끔
--accounting buy_and_hold
                        보유 기간 회계. buy_and_hold(기본)는 리밸런싱 사이 비중이 가격에 따라 변하고,
                        daily_rebalanced는 매일 목표 비중으로 되돌린다고 가정
```

## 유동성 필터

거래가 적은 ETF는 호가 차이 때문에 실제 매매 비용이 백테스트의 `--transaction-cost-bps`보다 커집니다. `--min-avg-traded-value`를 주면 리밸런싱 시점까지의 63거래일 평균 거래대금이 기준 미만인 ETF를 후보에서 뺍니다. 리밸런싱일 이후 데이터는 쓰지 않습니다.

- 거래대금은 수정주가가 아닌 실제 종가 × 거래량으로 계산해 `data/traded_value_*.csv`에 따로 캐시합니다.
- 기준은 투자 금액에 맞춰 정합니다. 한 번 주문 금액이 일 거래대금의 1% 이하가 되도록 잡는 것을 권합니다.
- 랭킹 CSV에 `avg_traded_value`, `liquid` 컬럼이 추가되고, 필터를 켜면 `rank`/`weekly` 화면에도 `liquid`가 표시됩니다.
- 거래대금 데이터의 마지막 관측일이 리밸런싱일보다 5영업일 넘게 오래된 ETF는 유동성 미달로 처리합니다. 거래대금 데이터가 가격 데이터보다 오래됐거나 일부 ETF의 거래대금이 없으면 실행 시 경고합니다.
- `positions.csv`의 `liquidity_excluded`는 다른 조건은 모두 통과했지만 유동성 때문에 빠진 ETF, `held_avg_traded_value`는 보유 ETF의 당시 평균 거래대금입니다.
- `config/universe_kr_etfs.csv`의 375770(일 거래대금 약 2천만 원)은 `enabled=0`으로 제외했습니다.

### 채권 ETF는 후보에서 제외

중·장기 채권 ETF(한국 114260·148070, 미국 TLT)는 `enabled=0`으로 모멘텀 후보에서 뺐습니다. 변동성이 낮아 위험조정 점수와 변동성 역가중에서 비중을 과도하게 차지했기 때문입니다. 방어가 필요할 때(후보 부족, 시장 필터)는 채권 대신 `--cash` ETF(BIL, 153130)로 갑니다. 다시 후보로 쓰려면 `enabled`를 1로 바꾸면 됩니다.

## 변동성 처리

- 변동성(`--volatility-window`, 기본 63일)을 계산할 수 없는 ETF는 후보에서 제외됩니다(`volatility_ok=False`). 백테스트 워밍업 기간에도 변동성 계산 기간이 포함됩니다.
- `risk_adjusted_score`와 `inverse_vol` 비중은 연 변동성 5% 미만을 5%로 간주해 계산합니다. 변동성이 아주 낮은 채권 ETF나 부동소수점 오차가 극단적인 점수·비중을 만들지 않게 하기 위한 하한입니다. `realized_volatility` 컬럼에는 원래 값이 남습니다.

## 변동성 목표

`--target-vol`을 주면 리밸런싱마다 선택된 포트폴리오의 연환산 변동성을 리밸런싱일까지 최근 `--volatility-window`(기본 63)거래일 수익률로 추정하고, 투자 비중을 `min(1, 목표 / 추정치)`배로 줄입니다. 줄어든 만큼은 `--cash` ETF로 갑니다. 변동성이 큰 구간에 덜 투자해 급락 손실을 줄이려는 장치이며, 시장 필터(`--risk-on-benchmark`)와 함께 쓸 수 있습니다.

`positions.csv`에는 추정 변동성(`est_portfolio_vol`)과 적용 배율(`vol_scale`)이 기록됩니다. 목표를 끈 상태에서도 추정 변동성은 기록됩니다.

## 성과 지표

`summary.json`과 `comparison.csv`에 들어가는 주요 지표입니다. 모든 지표는 첫 리밸런싱일부터 측정합니다.

```text
<curve>_cagr              연평균 수익률
<curve>_volatility        연환산 변동성
<curve>_sharpe            (일간 수익률 - 무위험 수익률)의 평균 / 표준편차 × √252.
                          무위험 수익률은 --cash ETF의 일간 수익률(없으면 0)
<curve>_sharpe_like       CAGR / 변동성. 이전 결과와 비교용으로 남긴 값이며 일반적인 Sharpe가 아님
<curve>_max_drawdown      최대 낙폭
<curve>_calmar            CAGR / |최대 낙폭|
<curve>_ending_value      기간 전체 성장 배수
excess_cagr               전략 CAGR - 벤치마크 CAGR
total_transaction_cost    리밸런싱마다 낸 거래비용(포트폴리오 대비 비율)의 합
annual_transaction_cost   위 합계 / 기간(년)
annual_turnover           리밸런싱 회전율의 연간 합계 (2.0 = 1년에 포트폴리오를 두 번 갈아치움)
```

`positions.csv`에는 리밸런싱별 `transaction_cost`가 기록됩니다. `walkforward`는 표에 Sharpe를 함께 보여주지만, 구간마다 파라미터가 바뀌므로 거래비용 합계는 따로 계산하지 않습니다.

## 백테스트 가정

- 신호: 리밸런싱일 종가까지의 가격으로 계산합니다.
- 체결: 같은 리밸런싱일 종가에 체결된다고 가정합니다. 수익률은 다음 거래일부터 반영됩니다.
- 거래비용: `--transaction-cost-bps` × 거래금액을 보유 첫날 수익률에서 차감합니다. 매도·매수 양쪽 모두 부과됩니다.
- 성과 측정 구간: 전략과 벤치마크 모두 첫 리밸런싱일 종가를 1.0으로 두고 같은 기간을 측정합니다. 워밍업 기간은 포함하지 않습니다.
- 보유 기간: 기본은 buy-and-hold입니다. 다음 리밸런싱 때는 변한 비중에서 목표 비중으로 돌아가는 만큼 거래(회전율)가 생깁니다.

종가 체결은 실제보다 약간 유리한 가정입니다. 신호일 다음 거래일로 체결을 늦춰 본 비교에서는 결과 차이가 작았습니다.

## 실행 기록

모든 명령은 결과 폴더에 `<명령>_run_config.json`을 저장합니다. 실행 인자, 실행 시각, 유니버스 파일 해시, 가격 데이터 시작·종료일, 유동성 필터 사용 시 거래대금 데이터 기간·대상·누락 종목(`traded_value_data`), 체결 가정이 들어 있어 결과 파일을 어떤 설정으로 만들었는지 추적할 수 있습니다.

후보가 `top-n`보다 적으면 빈 슬롯은 현금성 ETF로 채웁니다. 예를 들어 `--top-n 3`인데 조건을 통과한 ETF가 1개면 그 ETF 33.3%, 현금 66.7%입니다.

## 가격 데이터 캐시

가격은 `data/`에 캐시됩니다. `--end`를 지정하지 않은 최신 캐시는 12시간이 지나면 자동으로 다시 받고, 다운로드에 실패하면 경고와 함께 기존 캐시를 씁니다. 모든 명령은 실행 시 `Price data as of` 기준일을 출력하고, 데이터가 4일 넘게 오래되면 경고를 보여줍니다. 강제로 새로 받으려면 `--refresh`를 사용합니다.

## 리밸런싱/파라미터 비교

`compare` 명령은 여러 조합을 한 번에 백테스트하고 `results/comparison.csv`로 저장합니다.

```bash
uv run etf-momentum compare \
  --universe config/universe_us_sectors.csv \
  --benchmark SPY \
  --start 2015-01-01 \
  --cash BIL \
  --top-n-grid 2,3,4 \
  --rebalance-grid W-FRI,ME \
  --weighting-grid equal,inverse_vol,score \
  --relative-strength-grid off,on \
  --rank-by-grid weighted_momentum,risk_adjusted_score \
  --risk-on-benchmark \
  --transaction-cost-bps 5
```

한국 ETF 예시:

```bash
uv run etf-momentum compare \
  --universe config/universe_kr_etfs.csv \
  --benchmark 069500.KS \
  --start 2015-01-01 \
  --cash 153130.KS \
  --top-n-grid 2,3,4 \
  --rebalance-grid W-FRI,ME \
  --weighting-grid equal,inverse_vol \
  --relative-strength-grid off,on \
  --risk-on-benchmark \
  --transaction-cost-bps 5
```

## 워크포워드 검증

`compare`는 전체 기간을 보고 가장 좋은 조합을 고르기 때문에 과최적화된 숫자가 나옵니다. `walkforward`는 매 테스트 구간마다 **그 이전 학습 구간 성과만으로** 파라미터를 고르고, 다음 구간에 적용한 결과만 이어 붙입니다.

```bash
uv run etf-momentum walkforward \
  --universe config/universe_us_sectors.csv \
  --benchmark SPY \
  --cash BIL \
  --start 2010-01-01 \
  --train-years 5 \
  --test-months 12 \
  --metric calmar
```

기본 그리드는 `top-n 2,3,4` × 비중 `equal,inverse_vol` × 순위 기준 2종 × 그룹 제한 `0,1` × 시장 필터 `off,on` × 상대강도 `off,on`(96개)입니다. `--anchored`를 주면 학습 구간이 처음부터 누적됩니다.

출력 비교 대상:

```text
walk_forward     매 구간 사전 선택한 파라미터로 운용한 표본외 결과
benchmark        같은 기간 벤치마크
median_fixed     그리드 중 표본외 기간 성과가 중간인 고정 파라미터
hindsight_best   표본외 기간을 미리 알고 고른 최고 조합 (현실에서 불가능, 과최적화 상한선)
```

결과 파일: `walkforward_summary.json`, `walkforward_equity.csv`, `walkforward_windows.csv`(구간별 선택 파라미터), `walkforward_grid.csv`.
구간 경계에서 파라미터가 바뀔 때의 추가 매매비용은 반영하지 않습니다.

## 포트폴리오 조합 (여러 전략·ETF 묶기)

`portfolio` 명령은 여러 수익률 흐름(슬리브)을 하나의 포트폴리오로 묶어 기준 통화로 평가합니다.

```bash
uv run etf-momentum portfolio --spec config/portfolio_spy_kr.json
```

- 슬리브는 티커(`ticker`, 단순 보유) 또는 전략 수익 곡선 CSV(`equity_csv` + `column`)입니다. `config/portfolio_spy_kr.json`은 한국 모멘텀 슬리브로 `walkforward` 표본외 결과(`results/walkforward/...`)를 쓰므로, 해당 walk-forward를 먼저 실행해야 합니다.
- 모든 시나리오는 spec의 `calendar_ticker`(예: 069500.KS) 거래일로 평가합니다. 사용하지 않는 슬리브가 spec에 있어도 시나리오 결과는 바뀌지 않습니다.
- 슬리브마다 `timing`을 둡니다. `same_day`(기준 통화 슬리브 기본값)는 평가일 종가를 쓰고, 평가 달력의 날짜가 하나라도 빠졌거나 중간에 빈 값이 있으면 오류로 멈춥니다(데이터 누락과 휴장일을 구분할 수 없기 때문). `previous_close`(외화 슬리브 기본값)는 평가일보다 이른 날짜의 마지막 종가와 환율을 씁니다. 국내 상장 해외 ETF가 한국 장 마감 때 반영하는 정보와 같은 시점입니다. 5일 넘게 새 값이 없으면 그 슬리브는 끝난 것으로 봅니다.
- 기준 통화와 다른 슬리브는 `fx` 환율(예: `KRW=X`, 달러당 원화)로 환산합니다.
- 첫날과 매월 마지막 거래일에 목표 비중으로 재조정하고, 그 사이에는 비중이 가격에 따라 변합니다. 재조정 거래에는 `rebalance_cost_bps`가 붙습니다.
- `leverage`가 1보다 크면 빌린 금액에 `borrow_rate`(연, 날짜 수 기준) 이자를 내고, 1보다 작으면 나머지는 `cash_ticker` 수익률을 받습니다. 리스트로 주면 배율마다 시나리오가 만들어집니다.
- 모든 슬리브에 데이터가 있는 날부터 시작하고, 공통 날짜가 2일 미만이면 오류로 멈춥니다.
- 슬리브 중 하나라도 데이터가 끝나면 그날에서 평가를 멈춥니다(끝난 전략을 0% 수익으로 늘리지 않음). 실행할 때 슬리브별 데이터 기간을 출력합니다.
- 전략 곡선 CSV는 첫 행을 기준일(1.0)로 보고 그 종가에 배분하므로, 곡선의 첫 수익이 빠지지 않습니다. `walkforward`가 저장하는 곡선은 첫 표본외 수익 직전 날짜를 1.0으로 시작합니다.
- spec의 `end`로 다운로드 종료일을 고정할 수 있고, `periods`의 시작일을 지정하지 않으면 시나리오마다 첫 공통 날짜부터 평가합니다. 시나리오끼리 비교하려면 시작일을 같게 지정하세요.
- `portfolio_run_config.json`에 spec 내용·해시, 슬리브 입력 파일의 해시·기간·생성 실행 정보, 코드 커밋이 기록됩니다.
- 결과: `results/portfolio/portfolio_summary.csv`(시나리오×기간별 CAGR·MDD·Sharpe·변동성·해당 기간 연간 재조정 비용), `portfolio_equity.csv`.

## 연금 계좌 (연금저축 / IRP·DC)

`config/universe_kr_pension.csv`는 국내 섹터 ETF에 국내 상장 해외·원자재 ETF를 더한 연금 계좌용 유니버스입니다. 추가 컬럼:

```text
pension_ok     연금저축에서 매수 가능 (1/0)
retirement_ok  퇴직연금(IRP·DC)에서 매수 가능. 합성·선물형(파생형) ETF는 0
risk_asset     위험자산 여부. 채권·단기채는 0 (안전자산)
proxy          상장 전 기간을 채울 미국 ETF 티커 (원화 환산)
note           파생형, 환헤지, 상품명 변경 등 메모
```

```bash
# 연금저축
uv run etf-momentum walkforward --universe config/universe_kr_pension.csv \
  --benchmark 069500.KS --cash 153130.KS --start 2010-01-01 \
  --account pension --use-proxies --min-avg-traded-value 1e9 --transaction-cost-bps 10 \
  --top-n-grid 3,4 --max-per-group-grid 1

# IRP·DC: --account retirement (위험자산 합계 70% 상한이 자동 적용, 나머지는 --cash 안전자산)
```

- `--account pension|retirement`: 해당 계좌에서 살 수 있는 ETF만 후보로 씁니다. `retirement`는 `--max-risky-weight`를 따로 주지 않으면 0.7입니다. 이때 `--cash`는 단기채권 ETF 같은 안전자산이어야 합니다.
- `--use-proxies`: 국내 상장 해외 ETF는 대부분 2020년 이후 상장이라, 상장 전 기간을 `proxy`(예: SPY)의 원화 환산 가격으로 채웁니다(`--proxy-fx KRW=X`). 한국 거래일 D에는 **D보다 이른 날짜의** 미국 종가와 환율을 씁니다. 미국 D일 종가는 한국 D일 장 마감 뒤에 확정되기 때문입니다(양국 휴장일에도 같은 규칙). 상장일에 실제 가격과 이어지도록 맞추며, 보수·추적오차는 반영되지 않습니다. 거래대금도 같은 방식으로 채우지만(환산만, 비율 조정 없음), 프록시 기간에는 국내 상품의 실제 유동성을 확인할 수 없습니다.
- 계좌별 매수 가능 여부는 일반 규정 기준이며 증권사마다 다를 수 있습니다. 실제 계좌의 매수 가능 목록으로 확인하세요.
- 이 파일에 새로 추가한 국내 상장 해외·원자재 ETF는 2026-10-06 야후 조회로 티커 존재와 상품명을 확인했습니다(외부 기관 자료와는 대조하지 않음). 446720은 채권혼합형 상품이라 넣지 않았고, 314250은 'FANG플러스'에서 '미국빅테크10'으로 이름이 바뀐 상품입니다. 기존 국내 섹터 ETF의 상품명은 따로 확인하지 않았으며, 139280·139290은 이름이 뒤바뀌었을 가능성이 있어 확인이 필요합니다.
- `--account retirement`(또는 `--max-risky-weight` 1 미만)에서는 `--cash` ETF가 유니버스에 enabled로 들어 있고 `risk_asset=0`이어야 하며, retirement는 `retirement_ok=1`이어야 합니다. 유니버스에 없거나 조건이 맞지 않으면 실행을 멈춥니다. `risk_asset=0`인 다른 후보는 70% 상한에 포함되지 않습니다. 상한은 리밸런싱 시점에 적용하며, 월중 가격 상승으로 넘을 수 있습니다.

## 연구 결과 표 재생성

`docs/research_summary_*.md`의 결과 표는 저장된 결과에서 다시 만들 수 있습니다. 연구 문서의 재현 명령을 먼저 실행한 뒤:

```bash
uv run python scripts/research_tables.py --end 2025-02-28
```

모든 표에 같은 기준(워크포워드 기준일 시작, 각 시장의 현금 ETF를 무위험 수익률로 사용, 각 시장의 거래일)을 적용해 `results/research_tables.md`로 저장합니다.

## ETF 유니버스 수정

`config/universe_us_sectors.csv`를 복사해서 원하는 ETF 목록으로 바꾸면 됩니다.

CSV 형식:

```csv
ticker,name,category,enabled,group
XLK,Technology,US Sector,1,us_large_growth
QQQ,Nasdaq 100,Broad Growth,1,us_large_growth
XLF,Financials,US Sector,1,
```

`group`은 선택 컬럼입니다. 같은 지수나 산업을 사실상 중복 추종하는 ETF에 같은 값을 넣으면 `--max-per-group` 한도 안에서만 함께 편입됩니다. 비워두면 그 ETF 혼자 한 그룹입니다.
기본 유니버스의 그룹은 같은 지수·산업·테마를 추종하는 ETF끼리 묶었고, 2021~2025년 일간 수익률 상관계수(대부분 0.9 이상, 최소 0.84)로 확인했습니다. 상관이 높아도 노출이 다른 쌍(예: XLK/SMH, DIA/XLI)은 묶지 않았습니다.

한국 ETF를 쓰려면 야후파이낸스 기준 티커 형식인 `069500.KS` 같은 형식을 사용합니다.
단, 국내 ETF 티커는 상품명 변경이나 상장폐지가 있을 수 있으니 사용 전에 증권사/거래소에서 직접 확인하세요.

## 주의

이 코드는 투자 조언이 아니라 전략 연구용입니다. 실거래 전에는 ETF 유니버스, 세금, 환율, 거래비용, 추적오차, 괴리율, 유동성을 반드시 확인하세요.
