# 현재 코드 상세 리뷰 — 2026-10-08

검토 commit: **ae4bce8**. 검토 시작 시 작업 트리는 깨끗했다. 전체 소스와 최근 변경, 전략·신호 기록·체결 테스트, README를 검토했다. 이번에는 코드를 수정하지 않았으며 이 리뷰만 추가했다.

## 결론

전략 파일을 통한 선정 규칙 고정, 월말 선정/백테스트 일관성, 신호 중복 기록 방지, 다음 종가 체결, 외국 슬리브의 긴 내부 공백 중단 등은 개선됐다. 기존 테스트 **195개가 모두 통과**했고, 저장된 실제 시세로 주요 명령을 실행해 정상 경로를 확인했다.

다만 **시장 필터·현금 자산의 검증이 명령과 자산 종류에 따라 다르고**, 다운로드 정리 단계에서 원자료의 누락을 없애는 문제가 남았다. 월말 데이터가 끊겼다가 다음 달에 재개하는 경로도 잘못 처리한다. 아래 발견 사항은 실제 장애가 원자료에서 얼마나 자주 있었는지를 주장하는 것이 아니라, 구현에서 재현되는 오류와 불일치다.

## 검증 결과

| 검증 | 관측 결과 |
|---|---|
| `uv run pytest` | **195 passed, 7.87s** |
| `rank --strategy pension.json` | 실제 캐시를 공급해 CLI 실행. 2026-09-30에 139260.KS, 292150.KS, 102780.KS, 381180.KS 선정 |
| 같은 rank 재실행 | 신호 revision 1 유지, 중복 행 없음. selected/group_limit 합계 8행. code_dirty=false |
| `weekly --strategy pension.json` | 같은 캐시로 실행. 전략별 weekly_history.csv 생성 |
| same_close 백테스트 | 전략 설정 + 비용 10bp + 종료일 고정. 전체 기간 CAGR 11.72%, MDD -42.40% |
| next_close 백테스트 | 같은 규칙. 전체 기간 CAGR 11.54%, MDD -42.05% |
| 주 기준 기간 비교 | 2016-02-29~2025-02-28: same_close CAGR **11.5788%**, next_close **10.2853%**, 차이 **1.2935%p/년** |
| 포트폴리오 | 현재 pension spec 실행 성공. 주 기준 기간 S&P50070/WF30: CAGR 15.6%, MDD -24.7%, Sharpe 0.92 |
| 합성·호출 계측 | stale 필터, 월말 공백 재개, 현금 적격성, 개별 데이터 NaN 정리, 과거 조회 범위, 외부 필터 다운로드, JSON 숫자 범위 등을 재현 |

rank의 실제 월말 선정 집합은 새로 실행한 same_close 백테스트의 같은 날짜 보유 집합과 일치했다. 이는 일관성 검증이며 종목 매수 제안이 아니다.

실측은 저장된 Yahoo 가격·거래대금 캐시를 사용했다. rank/weekly의 downloader는 캐시를 요청 티커와 시작·종료일로 잘라 반환하는 함수로 대체했으며 선정·출력·기록 함수는 그대로 실행했다. backtest/portfolio는 CLI와 고정 종료일 캐시로 실행했다. 네트워크 다운로드와 시세의 외부 출처 검증을 수행한 것은 아니다.

산출물: `/private/tmp/etf-review-20261008/` 아래 same_close, next_close, portfolio, live_strategy, probes. 실측은 리뷰 파일 생성 전에 완료했으므로 해당 실행 기록의 코드 상태는 clean이다. 임시 파일은 장기 보존을 보장하지 않는다.

## 발견 사항

### 1. P1 — 시장 필터와 cash 가격의 신선도를 검증하지 않는다

위치: `src/etf_momentum/backtest.py:294`, `src/etf_momentum/cli.py:1280`, `src/etf_momentum/signals.py:189`.

이전의 '필터 티커가 아예 없는데 투자 허용'은 수정됐다. 하지만 존재 여부 검사만 있고 최신 관측 검사는 없다. 후보 ETF의 price_fresh 검사는 시장 필터·cash에는 적용되지 않는다. 무제한 ffill된 benchmark가 과거 상승 위치에 멈춰 있으면 시장 필터는 계속 risk_on이다.

재현: 650거래일의 상승 BENCH에서 마지막 60거래일 가격을 마지막 값으로 고정하고, 최신 A 가격과 eligible 순위를 주었다. risk_on_benchmark=True인데 **Selection(risk_on=True, tickers=['A'])**를 반환했다. 모든 종목이 있는 DataFrame이므로 _require_tickers도 통과한다. cash가 존재만 하고 실제 가격 갱신이 끝났을 때도 보유 수익률을 계속 0으로 간주할 수 있다.

영향: '시장 추세가 투자 가능한 상태인가'를 최신 자료로 확인하지 못해도 정상 선정처럼 출력된다. 현금 수익률은 성과·학습 Sharpe의 기준이므로 cash의 장애도 결과 해석에 영향을 준다.

수정: 후보·benchmark·시장 필터·cash에 공통 원관측 신선도 검증을 적용한다. 신호일 당시의 관측일을 검사하고, 가격이 안 변했다는 휴리스틱과 실제 관측 여부를 구분한다. 필수 시계열을 검증할 수 없으면 명확한 오류로 중단한다. 라이브러리 run_backtest의 benchmark 누락 시 0% 비교 곡선 대체도 CLI와 같은 오류 정책으로 맞추는 것이 좋다.

필요 검증: 한 필터만 stale, OR 필터 중 일부 stale, cash stale, 최신 flat 가격과 누락된 ffill 가격, 정상 휴장.

### 2. P2 — 다음 달 데이터가 있으면 불완전한 월말도 완료됐다고 판단한다

위치: `src/etf_momentum/backtest.py:322`.

최근 수정은 월중에 종료된 파일과 지난달 전체 누락을 거부한다. 그러나 `complete` 조건은 다음 달 데이터가 하나라도 있으면 True다. 월말 장애 후 다음 달에 다운로드가 재개하는 경우, 월중 날짜를 월말로 선택한다.

재현: dates=['2026-09-15', '2026-10-01'], today='2026-10-08'에서 **2026-09-15**를 반환했다. 해당 날짜는 9월말 종가가 아니다.

수정: 다음 달 존재를 월말 완전성의 증거로 쓰지 않는다. 거래소별 실제 마지막 거래일 또는 검증된 거래일 달력에 맞춰 coverage를 확인한다. 휴장일 처리는 달력으로 하고, 원자료 공백과 구분한다.

필요 검증: 월중 종료, 월중 공백 후 다음 달 재개, 월말 휴장, 다음 달 데이터가 있는 정상 완전한 월말.

### 3. P2 — rank는 퇴직연금 부적격 cash를 정상 대기 자산으로 출력·기록한다

위치: `src/etf_momentum/cli.py:264`, `src/etf_momentum/cli.py:367`, `src/etf_momentum/cli.py:1185`.

cash의 enabled·risk_asset·retirement_ok 검증은 backtest/compare/walkforward에서 _safe_tickers를 호출할 때만 수행한다. rank는 include_cash=False로 존재 검사에서도 제외하고, _safe_tickers를 호출하지 않는다. 시장 필터가 꺼지면 그 cash를 정상 결과에 포함하고 전략 신호로 기록한다.

재현: retirement 전략의 CASH를 유니버스에 **risk_asset=1, retirement_ok=0**으로 등록하고 시장 필터가 꺼지도록 했다. rank는 오류 없이 **status=cash, ticker=CASH, reason=시장 필터 꺼짐**을 출력하고 signals.csv도 생성했다. 같은 cash를 backtest의 검증 함수에 주면 **risk_asset=1 오류**로 중단한다.

영향: 동일 전략 파일이 선정 도구에서는 유효한 결과로 기록되고 검증 명령에서는 거부된다. 사용자가 계좌에서 보유할 ETF를 고르는 컨셉과 맞지 않는 명령 간 검증 차이다.

수정: 데이터 다운로드/신호 기록 이전에 공통 계좌·cash 적격성 검증을 적용한다. rank가 cash 가격을 사용하지 않더라도 그 자산의 계좌 적격성은 확인한다. 연금저축의 pension_ok와 퇴직연금의 retirement_ok 정책도 일관되게 정의한다.

필요 검증: rank/backtest의 동일한 부적격 cash, 유니버스 밖 cash, disabled cash, cash=null의 명시적인 정책, 정상 cash.

### 4. P2 — same_day의 원자료 NaN이 다운로드 단계에서 지워져 공백 검증을 통과한다

위치: `src/etf_momentum/data.py:266`, `src/etf_momentum/portfolio.py:101`, `src/etf_momentum/cli.py:919`.

same_day는 원곡선의 내부 NaN과 누락 거래일을 오류로 처리한다. 그러나 ticker 슬리브는 download_prices → _clean_prices에서 이미 ffill한 곡선을 받는다. 그 단계에서 같은 거래일의 NaN이 값으로 바뀌므로 이후 검증은 원자료 결측을 알 수 없다. 단일 티커 다운로드도 이 문제를 없애지 않는다.

재현: downloader의 원 Close=[100, NaN, 110, 111]을 공급했다. download_prices 결과는 **[100,100,110,111]**이 됐다. same_day 검사도 통과했고 수익률은 **[0,0,10%,0.9091%]**였다. 원자료에 분명한 내부 결측이 있는데 오류가 사라졌다.

수정: raw 가격과 평가용 ffill 가격을 분리하거나 관측 mask를 반환·보존한다. same_day 검사와 달력 생성은 원관측으로 수행한다. CSV 슬리브의 내부 NaN 테스트뿐 아니라 다운로드부터 시작하는 통합 검증이 필요하다.

이 문제는 이전의 previous_close stale 공백 재개 오류와 다르다. 그 경로는 이번 코드에서 정상적으로 오류를 반환하도록 수정됐다.

### 5. P2 — 과거 rank/weekly 조회의 시작일이 과거 기준일을 따르지 않는다

위치: `src/etf_momentum/cli.py:261`, `src/etf_momentum/cli.py:461`, `src/etf_momentum/cli.py:1331`.

_selection_today는 과거 --end를 반영하지만, 다운로드 start는 여전히 현재 날짜에서 lookback_years를 빼서 계산한다. 따라서 과거 조회가 추가됐어도 몇 년 전을 조회하면 start가 end보다 뒤가 될 수 있다. --start 역시 rank/weekly에서는 사용하지 않는다.

재현: 현재 환경에서 lookback_years=3의 계산 start는 **2023-10-06**이다. --end 2020-03-01의 선택 기준일은 제대로 2020-03-01로 계산되지만, 다운로드 요청은 **start=2023-10-06 > end=2020-03-01**이 된다.

수정: rank/weekly의 시작일은 실제 평가 기준일 또는 exclusive end에서 lookback을 빼서 정한다. 현재와 과거 모두 필요한 워밍업을 확보하고, start/end 순서를 다운로드 전에 확인한다. --start를 지원할지 제거할지도 명확히 한다.

필요 검증: lookback 기간보다 오래된 과거 조회, 최근 과거 조회, 오늘의 조회, 시작·종료일 역전.

### 6. P2 — weekly는 유니버스 밖의 시장 필터를 다운로드하지 않는다

위치: `src/etf_momentum/cli.py:460`, `src/etf_momentum/cli.py:465`.

전략 파일은 별도 risk_on_tickers를 지원하고 required 검사도 한다. rank/backtest는 extra 목록에 이 티커를 넣지만 weekly는 benchmark만 추가한다. 사용자가 지정한 시장 필터가 후보 유니버스 밖에 있으면 다운로드를 요청하지 않은 뒤 가격 누락 오류로 중단한다.

재현: benchmark=BENCH, risk_on_tickers=['OUTSIDE']인 유효한 전략을 주었다. downloader가 받은 티커는 **['A','CASH','BENCH']**였으며 OUTSIDE는 없었다. 이후 **No price data … market filter 1 OUTSIDE**로 중단했다. 이를 --refresh로 해결할 수는 없다.

수정: 필요한 market filter extra를 weekly에도 넣거나, weekly가 시장 필터를 쓰지 않는 관찰 명령이라면 required 정책 자체를 명확하게 분리한다. 같은 전략 파일이 명령마다 다른 데이터 요구로 실패하지 않도록 한다.

### 7. P2 — portfolio JSON의 비중·배율이 유한한 지원 범위인지 검증하지 않는다

위치: `src/etf_momentum/portfolio.py:154`, `src/etf_momentum/cli.py:974`.

portfolio는 비중 합계만 검사한다. 개별 비중, leverage, 비용, borrow_rate에 대한 finite/range 검증은 없다. JSON을 사용하므로 다른 CLI의 float validator도 적용되지 않는다.

재현: weights={'A':1}, leverage=NaN을 주면 오류 없이 **[NaN,NaN] equity**가 반환됐다. weights={'A':-1,'B':2}, leverage=1도 통과해 A가 10% 오른 날 equity가 **0.9**가 된다. 후자는 숏 포지션을 계산하지만 README에는 숏 모델·비용이 설명돼 있지 않다.

수정: 지원하는 투자 범위를 명시하고 설정 읽기와 라이브러리 진입점에서 동일하게 검사한다. 현재 양수 비중 조합 모델이라면 음수 비중은 거부한다. 숏을 지원할 계획이면 별도 명시적인 모델로 분리한다. NaN/inf는 어떤 정책에서도 거부하고, 거래비용·차입비용·배율의 허용 범위를 검증한다.

### 8. P3 — next_close positions의 hold_until이 실제 보유 종료일보다 하루 빠르다

위치: `src/etf_momentum/backtest.py:90`, `src/etf_momentum/backtest.py:123`.

수익률 계산은 기존 보유가 다음 체결일 종가까지 수익을 얻도록 구현됐다. 하지만 hold_until은 여전히 다음 신호일이다. 실제 next_close 실행의 연속 기록 **187개**에서 앞 행의 hold_until이 뒤 행의 trade_date보다 빠른 것을 확인했다. same_close에서는 두 날짜가 일치한다.

영향: 수익률 계산 자체의 이 차이를 오류로 확인한 것은 아니다. positions를 보유 이력·구간별 성과·종목별 노출 분석에 쓰면 실제 보유일과 기록이 어긋난다.

수정: next_signal_date와 hold_until/next_trade_date를 분리하거나 hold_until에 실제 마지막 holding_date를 기록한다. 데이터가 종료되는 마지막 부분 구간에서는 실제 끝 날짜를 기록한다. 체결일에 더 이상 다음 수익 관측이 없어 마지막 체결 기록을 생략하는 정책도 명시한다.

### 9. P3 — 선정 설명이 설정과 관계없이 ‘200일선’으로 고정된다

위치: `src/etf_momentum/cli.py:395`, `src/etf_momentum/cli.py:401`, `src/etf_momentum/cli.py:436`.

SMA window는 전략 파일·CLI에서 변경 가능하지만 시장 필터, 선정 지표 및 제외 이유는 항상 200일선으로 출력된다. 예를 들어 sma_window=100이면 계산은 100일 평균인데 사용자 설명은 200일선이다. 설명 강화 기능에서 계산과 표시가 다른 문제다.

수정: config.sma_window를 표시 문자열과 제외 사유 생성에 전달한다. rank_by=risk_adjusted_score일 때도 표시하는 '점수'가 weighted_momentum인지 실제 순위 점수인지 구분하면 선정 이유가 더 명확해진다.

## 수정 확인 사항과 남은 설계 한계

확인한 개선:

- 필터 ticker 누락 시 명확한 오류 반환, 부분 다운로드 경고·재시도와 캐시 정책.
- 후보의 긴 동일 가격 구간 제외. 이는 원관측 신선도를 대체하는 완전한 검증은 아니다.
- 전략 파일의 schema/옵션 충돌 검증, 유니버스 내용이 포함된 config_hash.
- 전략별 weekly 기록, 같은 규칙의 비교, 월말 append-only 신호 revision과 중복 방지.
- 학습 Sharpe에 cash 초과 수익률 적용, Calmar 초기 peak 및 첫 OOS 기준점 유지.
- previous_close의 긴 내부 공백 후 재개 중단, 고정 평가 달력, 개별 ticker refresh 전파.
- next_close의 첫 체결 이전 급등 수익을 얻지 않는 회귀 테스트 및 실제 체결 지연 비교.
- 현재 보유와 선택 목록의 keep/add/drop 비교.

현재 구현을 곧바로 잘못됐다고 단정하지 않은 한계:

- signal 기록에는 선정 지표·raw 입력 해시·가격/거래대금 스냅샷이 저장되지 않는다. code commit·config_hash·마지막 가격 날짜와 선정 결과는 보존하지만 당시 입력으로 다시 계산할 수 있는 완전한 증거는 아니다. 최신 cache와 latest CSV가 바뀌어도 과거 선정 이유를 재현하려면 별도 입력 보존이 필요하다.
- plain CLI의 weekly는 여전히 기존 공용 rank_history.csv를 사용한다. --strategy 경로는 분리됐으므로 일반 경로도 각각 output-dir/history-file을 분리하도록 문서화하거나 자동 분리할 수 있다.
- walkforward는 파라미터별 독립 수익률을 이어 붙이며 경계의 추가 거래비용은 미반영이다. README가 명시한 연구 모델의 한계다.
- 5일간 가격 불변을 freshness proxy로 쓰면 정상 flat 가격과 누락 가격을 구분할 수 없다. 현재 README에는 가격 정체 제외가 명시되어 있으므로 이를 숨겨진 규칙으로 보고하지 않았다. 원관측 정보 보존은 별개 과제다.
- next_close도 종가 기반 체결 모델이며 실제 주문·스프레드·휴장 처리까지 검증한 것은 아니다.

## 우선순위

1. 필수 자산 freshness 및 계좌·cash 검증을 공통화한다(1,3).
2. 원자료 mask를 보존하고 실제 거래일 달력으로 월말·same_day를 검증한다(2,4).
3. 과거 조회와 weekly 외부 필터의 요청 범위를 수정한다(5,6).
4. portfolio 입력 검증과 체결 이력·설명 출력의 정확성을 보완한다(7~9).

## 재현 예제

필수 데이터의 stale 상태와 월말 공백 재개는 다음으로 재현된다.

```python
import numpy as np
import pandas as pd
from etf_momentum.backtest import BacktestConfig, select_holdings, previous_month_end

days = pd.bdate_range('2020-01-01', periods=650)
prices = pd.DataFrame({'A':100*1.001**np.arange(650),
                       'BENCH':100*1.0005**np.arange(650)}, index=days)
prices.loc[days[-60]:,'BENCH'] = prices.BENCH.iloc[-61]
ranking = pd.DataFrame({'ticker':['A'], 'eligible':[True]})
print(select_holdings(ranking, prices, days[-1],
      BacktestConfig(top_n=1, benchmark='BENCH', risk_on_benchmark=True)))
print(previous_month_end(pd.to_datetime(['2026-09-15','2026-10-01']),
                         pd.Timestamp('2026-10-08')))
```

실제 데이터 실행 명령은 다음과 같다. output-dir를 분리해 기존 결과를 덮어쓰지 않는다.

```bash
uv run pytest
uv run etf-momentum backtest --strategy config/strategies/pension.json \
  --start 2010-01-01 --end 2026-10-07 --transaction-cost-bps 10 \
  --execution same_close --output-dir /private/tmp/etf-review-20261008/same_close
uv run etf-momentum backtest --strategy config/strategies/pension.json \
  --start 2010-01-01 --end 2026-10-07 --transaction-cost-bps 10 \
  --execution next_close --output-dir /private/tmp/etf-review-20261008/next_close
uv run etf-momentum portfolio --spec config/portfolio_pension.json \
  --output-dir /private/tmp/etf-review-20261008/portfolio
```

195개 통과는 기존 테스트의 결과이며, 위 모든 경계 조건을 기존 테스트가 보장한다는 뜻은 아니다. 이번 재현은 해당 함수를 직접 호출하거나 downloader를 계측했고, 수정 코드를 넣거나 테스트 기대값을 바꾸지 않았다. 전체 워크포워드 그리드, 외부 시세, 실제 계좌 매수 목록, 주문 체결은 검증 범위 밖이다.
