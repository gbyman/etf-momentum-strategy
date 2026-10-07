# 프로젝트 컨셉 대비 코드 실측 리뷰

작성일: 2026-10-07. 검토 commit: **3a8b9c2**. 시작 시 작업 트리는 깨끗했다.

## 결론

현재 도구는 **강한 ETF의 관찰·월말 선정·전략 연구**라는 핵심 컨셉을 상당 부분 구현한다. 실제 가격 캐시에서 rank의 월말 선택과 백테스트 보유 종목이 일치했고, 백테스트·compare·고정 한 조합의 walkforward·portfolio·연구 표 생성도 실행됐다.

그러나 필수 시장 데이터가 누락되거나 오래됐을 때 정상 선정 결과처럼 출력되는 문제는 선정 도구의 핵심 신뢰성에 직접 영향을 준다. 외국 슬리브의 긴 데이터 공백을 지나 재개할 때 수익률이 사라지는 오류도 확인했다. **정상 입력에서 실행된다는 사실과 누락·지연 입력에서도 올바른 판단을 한다는 것은 별개**다. 아래 1~3번을 우선 수정할 필요가 있다.

검토 목적은 ETF 선정과 연구 검증이다. 사용자가 정할 투자 비중·핵심 보조 비율·주문 기능의 부재는 현재 컨셉의 구현 오류로 분류하지 않았다.

## 검증 범위와 결과

기존 전체 테스트: **109 passed, 4.84s**.

실제 데이터: 저장된 Yahoo 가격·거래대금 캐시, 고정 다운로드 범위 2010-01-01 ~ 2026-10-06 (`--end 2026-10-07`). 새 시세 다운로드나 외부 기관 검증은 하지 않았다. 외부 자료와 대조하지 않은 2025-03 이후 가격의 진위를 이번 실행으로 보장하지 않는다.

| 검증 | 결과 및 범위 |
|---|---|
| rank | 실제 캐시를 downloader 대신 공급해 CLI 전체 실행. 최신 랭킹과 2026-09-30 선택 파일 생성 |
| weekly | 같은 실제 캐시로 CLI 실행, 첫 스냅샷 생성. 설정 혼합은 별도 합성 스냅샷으로 재현 |
| backtest | 연금저축, top4/equal/group1/risk-on, 10bp, 10억 유동성, 프록시. 전체 기간 CAGR 11.72%, MDD -42.40%, Sharpe 0.581 |
| compare | top3·4 두 조합 실행. top4의 지표는 위 backtest와 일치 |
| walkforward | 위 고정 한 조합을 5년 학습·12개월 테스트로 실행. 11개 창, OOS CAGR 16.1%, MDD -42.4%, Sharpe 0.72. 전체 32/96개 그리드를 다시 돌린 것은 아님 |
| portfolio | 현재 portfolio_pension.json 실행. 2016-02-29~2025-02-28, S&P50070/워크포워드30: CAGR 15.6%, MDD -24.7%, Sharpe 0.92 |
| research_tables.py | 실행 성공. 현재 연구 문서의 주요 표와 같은 표시 수치를 생성 |
| 합성·호출 계측 | 데이터 누락, 오래된 종목, 월말 공백, 외국 데이터 공백, refresh 전달, 혼합 weekly 기록, 기간 정의·Sharpe·입력 범위 검증 |

실제 월말 선택: **139260.KS, 292150.KS, 102780.KS, 381180.KS**. backtest의 2026-09-30 보유에서 현금 티커를 제외한 집합과 정확히 일치했다. 이 결과는 선정 일관성 검증이며 현재 매수를 권하는 목록이 아니다.

산출물은 `/private/tmp/etf-concept-review/`에 두었다. `rank.log`, `weekly.log`, 명령별 CSV·JSON·차트, `research_tables.md`, `refresh_calls.json`이 있다. 아래 재현 코드와 명령으로 다시 확인할 수 있다. 임시 파일은 장기 보존을 보장하지 않는다.

## 발견 사항

### 1. P1 — 시장 필터가 필요한데 가격이 없으면 투자 허용으로 처리한다

위치: `src/etf_momentum/backtest.py:279` (`select_holdings`), `src/etf_momentum/backtest.py:128` (benchmark 누락 처리).

시장 필터용 티커를 prices에 존재하는 것만 남긴 뒤, 필터 목록이 비면 초기값 risk_on=True를 그대로 사용한다. 누락된 시장 필터가 위험 회피가 아닌 투자 허용으로 바뀐다. 여러 기준 중 일부 누락도 알려주지 않는다. benchmark가 아예 없으면 백테스트 비교 곡선은 0% 수익률로 대체될 수 있다.

재현: A 가격과 eligible=True인 A 순위만 주고 benchmark='MISSING', risk_on_benchmark=True로 select_holdings를 호출했다. **risk_on=True, tickers=['A']**가 반환됐다. CLI 다운로드가 일부 종목에 실패해도 다른 종목이 있으면 전체 DataFrame은 비어 있지 않으므로 발생 가능한 경로다.

영향: 위험 회피 조건을 확인하지 못했는데 선정 목록을 제공하고, 잘못된 비교 대상에 대해 성과를 보고할 수 있다.

수정: 사용하는 benchmark·시장 필터의 존재, 계산에 필요한 이력, 최신 관측일을 실행 전 검증한다. 필수 데이터 누락은 명확한 오류로 중단한다. 일부 기준만으로 OR 필터를 계산하는 정책을 허용하려면 사용자가 명시하고 출력에 누락 목록을 남긴다.

필요 검증: 전부/일부 필터 누락, benchmark 누락, SMA 이력 부족과 정상 OR 조건.

### 2. P1 — 개별 종목의 오래된 가격이 forward-fill되어 정상 후보가 된다

위치: `src/etf_momentum/data.py:259`, `src/etf_momentum/backtest.py:58`, `src/etf_momentum/signals.py:111`.

전체 가격은 무제한 ffill되고, 최신성 경고는 DataFrame의 마지막 날짜만 본다. 다른 ETF가 갱신되면 한 종목이 장기간 가격을 받지 못해도 새 가격처럼 취급된다. 모멘텀·SMA·변동성 모두 채워진 값으로 계산된다.

재현: 350거래일 상승 가격에서 A의 마지막 **60거래일 원가격을 NaN**으로 만들고 BENCH만 계속 갱신했다. `_clean_prices()` 후 rank_momentum의 결과는 A의 **eligible=True**, weighted_momentum=0.129439, realized_volatility=0.003408이었다. 최근 가격을 모르는 자산을 강한 후보로 판정했다.

영향: 유동성 필터를 끈 기본 사용에서 stale 종목이 선정될 수 있다. 유동성 필터를 켜도 거래대금과 가격의 누락 상태가 독립적이면 가격 신선도를 보장하지 않는다. 변동성 역가중에서는 저변동처럼 보일 수도 있다.

수정: 원관측 mask·종목별 last_observed를 ffill 전 보존하고, 신호일에 맞는 freshness를 후보·시장 필터·cash 모두 검증한다. 거래소 휴장과 데이터 장애를 구분한다. missing/stale 상태와 관측일을 출력한다.

필요 검증: 일부 ticker만 stale, 가격은 stale지만 거래대금은 최신, 거래소 휴장일, 상장폐지/장기 거래정지.

### 3. P2 — 불완전한 달의 마지막 관측을 ‘직전 월말’로 표시한다

위치: `src/etf_momentum/backtest.py:290`, `src/etf_momentum/cli.py:248`.

previous_month_end는 해당 행이 속한 달이 현재 날짜 전에 끝났는지만 확인한다. 그 달의 실제 마지막 거래일까지 데이터를 받았는지는 확인하지 않는다. 최근 완료된 달 자체가 없으면 더 오래된 달로도 조용히 넘어간다.

재현: today=2026-10-07, 가격 index가 2026-09-01~09-15까지만 있을 때 반환일은 **2026-09-15**다. CLI는 이 날짜를 ‘직전 월말 종가’라고 출력한다. 이는 9월 중순 신호이지 9월말 신호가 아니다.

수정: 시장별 완료된 월의 마지막 거래일과 데이터 coverage를 비교한다. 과거 --end를 사용할 때는 명시적인 평가 기준일 정책을 둔다. 정확한 월말 데이터가 없으면 중단하거나 과거 신호임을 명확히 표시하고 현재 월말 선택으로 제시하지 않는다.

필요 검증: 월중에 끊긴 캐시, 지난달 전체 누락, 월말 휴장, 과거 종료일, 정상 최신 월말.

### 4. P2 — 외국 슬리브가 stale 공백 뒤 재개되면 공백 중 수익률이 사라진다

위치: `src/etf_momentum/portfolio.py:71`, `src/etf_momentum/portfolio.py:102`, `src/etf_momentum/portfolio.py:145`.

previous_close는 오래된 관측을 NaN으로 만든다. 이후 새 관측이 나오면 pct_change(fill_method=None)는 NaN 직후의 첫 가격 변화도 NaN으로 만든다. run_portfolio는 이 행들을 dropna해 버려 공백을 건너뛰고 운용을 재개한다. README의 ‘새 값이 없으면 슬리브가 끝난 것으로 본다’와도 맞지 않는다.

재현: 외국 가격이 100에서 시작하고, 긴 공백 뒤 200으로 갱신되는 series를 previous_close로 한국 달력에 정렬했다. 수익률 100%는 NaN이 됐고, 단일 슬리브 포트폴리오의 **ending value는 1.0**이었다. 공백을 넘어 보유를 계속한다면 2.0이어야 하고, 공백을 오류/종료로 처리한다면 명확히 중단해야 한다. 현재 결과는 어느 정책도 충족하지 않는다.

수정: initial warm-up/trailing 종료와 내부 공백을 구분한다. 첫 stale 종료에서 평가를 멈추거나 내부 공백에서 오류로 중단한다. 재개를 허용한다면 이전 평가 자산에서 새 평가 자산으로의 전체 변화와 경과 기간을 일관되게 반영한다. 단순히 NaN 행을 제거해서는 안 된다.

필요 검증: foreign price/FX 각각의 긴 내부 공백, 재개, 영구 종료, 정상 휴장, 다중 슬리브 공통 기간.

### 5. P2 — portfolio --refresh가 실제 평가에 쓰는 개별 티커에 전달되지 않는다

위치: `src/etf_momentum/cli.py:713`, `src/etf_momentum/cli.py:722`.

최초 합쳐 받는 가격에는 refresh=args.refresh가 전달되지만, 실제 평가 달력·외국 슬리브·FX는 own_dates의 개별 다운로드로 만든다. own_dates에는 refresh가 전달되지 않아 explicit end 캐시는 계속 재사용된다.

재현: downloader를 기존 캐시를 반환하는 계측 함수로 바꾸고 portfolio --refresh를 호출했다. 관측한 refresh 플래그는 **[True, False, False, False]**였다. 한국 달력, SPY와 FX는 새로 받으라는 요청을 받지 않았다.

영향: 데이터 수정 후 --refresh를 실행해도 실제 선정 연구 결과의 일부 입력은 이전 데이터일 수 있다. 합친 prices의 데이터 기준일과 실제 사용 시계열의 신선도가 다를 수 있다.

수정: own_dates에 refresh를 전달하고 티커별로 한 번만 내려받아 메모이즈한다. 실제 사용 시계열의 기간·해시도 메타데이터에 기록한다. cash 역시 같은 원자료 정책을 사용한다.

필요 검증: refresh=True/False가 모든 실제 입력에 전달되는지, 합친 캐시와 개별 캐시가 다른 값일 때 적용되는 데이터가 무엇인지.

### 6. P2 — weekly 기록에 서로 다른 시장과 전략이 섞인다

위치: `src/etf_momentum/cli.py:305`, `src/etf_momentum/weekly.py:34`, `src/etf_momentum/weekly.py:48`.

기본 history는 results/rank_history.csv 하나이며, 조회·저장은 날짜만 기준으로 한다. README의 미국·한국 예시를 그대로 실행하면 같은 history를 쓴다. 같은 날짜는 다른 시장 기록을 덮어쓰고, 다른 날짜는 다른 시장의 기록을 이전 snapshot으로 읽는다. 계좌·유니버스·순위 점수·필터 변경도 구분하지 않는다.

재현: 기본 구조의 같은 history 파일에 9월25일 KR 스냅샷을 저장한 뒤, 10월2일 US 실행을 위한 이전 snapshot을 조회했다. **이전 티커 목록이 ['KR']**였다. 이런 비교는 실제 주도 섹터 변화가 아니다.

수정: strategy_id 또는 유니버스·계좌·benchmark·신호 설정 hash로 history를 분리한다. 비교할 설정이 다르면 새 기준 스냅샷으로 시작하고 그 이유를 출력한다. 같은 날짜의 다른 전략은 공존해야 한다.

필요 검증: US/KR·pension/retirement·설정 변경·동일 일자 두 전략·정상 동일 전략 연속 실행.

### 7. P2 — 학습용 Sharpe와 보고용 Sharpe의 정의가 다르다

위치: `src/etf_momentum/walkforward.py:91`, `src/etf_momentum/backtest.py:230`.

walkforward의 metric='sharpe'는 무위험 수익률을 빼지 않은 일 수익률을 사용한다. 보고 지표는 cash ETF의 일 수익률을 뺀 초과 수익률을 사용한다. 같은 이름이지만 학습 선택 목표와 최종 보고 기준이 다르고, 선택 자체도 달라질 수 있다.

재현: A의 일 평균 0.0015·변동 폭 ±0.001, B의 평균 0.0006·변동 폭 ±0.0002, 무위험 수익률 0.0005를 사용했다. 학습 Sharpe는 **A 23.7645 / B 47.5289**, 보고 Sharpe는 **A 15.8430 / B 7.9215**로 선호 순서가 뒤집혔다. 이는 정의 차이를 드러내는 합성 값이며 실제 투자 성과가 아니다.

수정: 학습 함수에 risk_free를 전달해 동일 기준으로 계산하거나, 별도의 raw_sharpe라는 이름과 문서로 명확히 분리한다. 기본 연구 metric=calmar의 현재 수치가 이 문제로 직접 틀렸다는 뜻은 아니다.

### 8. P2 — ‘12-1’이라는 설명과 실제 모멘텀 기간이 다르다

위치: `src/etf_momentum/signals.py:116`, `docs/research_summary_2026-10-06.md` 전략 개요.

기본 lookback=252, skip=21이면 실제 점수는 **P[t-21]/P[t-273]-1**이다. 최근 1개월을 뺀 P[t-21]/P[t-252]-1과는 다르며, 21일 전 시점을 끝으로 하는 완전한 252거래일 수익률이다. 3·6개월 점수도 전체 기간을 뒤로 이동한다. README의 skip 설명만으로는 모호하지만 연구 문서의 ‘12-1’ 표현은 이 차이를 숨긴다.

재현: 350개 가격을 100,101,…,449로 만들었다. momentum_12m은 **1.431818**이고, P[t-21]/P[t-252]-1은 **1.172589**다.

수정: 두 정의 중 의도한 규칙을 명시적으로 선택한다. 현 구현을 유지한다면 ‘21거래일 지연된 3/6/12개월 수익률’로 이름을 정확히 바꾼다. 기간 계산을 변경한다면 후보 순위·워크포워드·연구 표를 모두 재계산한다. 단순 구현 버그로 단정해 정의를 바꾸면 안 된다.

### 9. P2 — 입력 범위 검증이 빠져 잘못된 선정과 무한 반복이 가능하다

위치: `src/etf_momentum/backtest.py:274`, `src/etf_momentum/walkforward.py:28`, CLI 숫자 인자 정의.

run_backtest는 top_n을 검사하지만 rank의 select_holdings는 검사하지 않는다. top_n=0으로 eligible A,B를 주면 **둘 다 선택하고 empty_slots=-2**를 반환한다. 또한 test_months=0은 walkforward while 루프의 test_start가 전진하지 않아 종료하지 않는다. subprocess에 test_months=0을 주고 1초 timeout을 두었고, timeout으로 종료했다. 소스에서도 진행 날짜가 그대로인 것을 확인했다.

수정: 공통 설정 검증을 CLI 및 라이브러리 진입점에 적용한다. top_n·train_years·test_months·기간 window는 양수, 비중·위험 상한·비용·목표 변동성은 지원 범위의 유한 값이어야 한다. 단순 argparse type=int/float만으로 충분하지 않다.

필요 검증: top_n=0/음수, test_months=0/음수, 빈 입력, NaN/inf, 음수 비용·비중 상한. 오류는 다운로드·결과 쓰기 이전에 반환한다.

## 컨셉 대비 구현 상태

| 컨셉/요구 | 현재 판단 |
|---|---|
| 상대적으로 강한 ETF 랭킹 | 구현 확인. 기간 정의 정리와 종목별 freshness 필요 |
| 모멘텀·SMA·유동성·변동성 후보 필터 | 정상 입력에서 구현 확인. 누락 데이터의 검증은 미흡 |
| 월말 선정과 백테스트 규칙 공유 | select_holdings 공유, 실제 9월말 선택 일치 확인. 월말 completeness 필요 |
| 같은 그룹 중복 제한과 빈 슬롯 현금 | 구현·테스트 확인 |
| 계좌별 후보 적격성 | CSV flag 기반 구현. 실제 증권사 규정 일치 여부는 외부 검증 대상 |
| buy-and-hold, 비용, 변동성 목표 | 구현·기존 회귀 테스트 확인. 체결은 same_close 연구 모델 |
| 주간 리더십 관찰 | 구현. history의 시장·전략 분리 필요 |
| 워크포워드 연구 | 훈련 구간만 선택에 사용. 첫 기준점·Calmar 초기 낙폭 수정 유지. 창 경계 switching cost 미반영은 README에 명시 |
| 포트폴리오 기준 달력·이전 외국 종가 | 정상 입력 재현. 이전 리뷰의 미사용 슬리브 달력 오염 수정 확인. 공백 재개·refresh는 위 발견 사항 |
| 공통 전략 설정 및 월말 불변 기록 | 아직 없음. CLI 메타데이터·latest CSV는 있지만 공통 preset과 누적 불변 신호 기록은 별개 |
| 목표 비중·현재 보유 대비 주문표 | 현재 선정 전용 컨셉 밖의 선택적 확장. 미구현을 코드 결함으로 보지 않음 |

## 우선 수정 순서

1. 필수 시장 데이터와 종목별 raw 관측 freshness 검증(1~2번).
2. 정확한 월말 데이터 확인 및 안전한 기본 선정 설정(3번).
3. 외국 슬리브 공백 처리와 실제 입력 refresh(4~5번).
4. 주간 기록 분리, 학습 지표·기간 정의 통일(6~8번).
5. 공통 입력 검증, 상세 선정 이유, 버전별 월말 신호 보존(9번 및 개선 문서).

기존 문서에서 지적했던 cash 등록 검증, 단일 날짜 포트폴리오, same_day 내부 NaN 검증, 고정 한국 평가 달력은 수정된 상태로 확인했다. 과거의 같은 숫자를 다시 문제로 보고하지 않았다. 이번 리뷰에서 발견한 공백 문제는 previous_close의 내부 stale 후 재개 경로다.

## 재현 명령과 핵심 예제

프로젝트 루트에서 실행한다. 아래 output-dir는 기존 연구 결과를 덮어쓰지 않는다.

```bash
uv run pytest

uv run etf-momentum backtest --universe config/universe_kr_pension.csv \
  --benchmark 069500.KS --cash 153130.KS --start 2010-01-01 --end 2026-10-07 \
  --account pension --use-proxies --min-avg-traded-value 1e9 --transaction-cost-bps 10 \
  --top-n 4 --max-per-group 1 --risk-on-benchmark --weighting equal \
  --output-dir /private/tmp/etf-concept-review/backtest

uv run etf-momentum compare --universe config/universe_kr_pension.csv \
  --benchmark 069500.KS --cash 153130.KS --start 2010-01-01 --end 2026-10-07 \
  --account pension --use-proxies --min-avg-traded-value 1e9 --transaction-cost-bps 10 \
  --top-n-grid 3,4 --rebalance-grid ME --weighting-grid equal --relative-strength-grid off \
  --risk-on-benchmark --output-dir /private/tmp/etf-concept-review/compare

uv run etf-momentum walkforward --universe config/universe_kr_pension.csv \
  --benchmark 069500.KS --cash 153130.KS --start 2010-01-01 --end 2026-10-07 \
  --account pension --use-proxies --min-avg-traded-value 1e9 --transaction-cost-bps 10 \
  --top-n-grid 4 --weighting-grid equal --rank-by-grid weighted_momentum \
  --max-per-group-grid 1 --risk-on-grid on --relative-strength-grid off \
  --output-dir /private/tmp/etf-concept-review/walkforward

uv run etf-momentum portfolio --spec config/portfolio_pension.json \
  --output-dir /private/tmp/etf-concept-review/portfolio
uv run python scripts/research_tables.py --end 2025-02-28 \
  --output /private/tmp/etf-concept-review/research_tables.md
```

rank/weekly는 실측에서 저장된 60개 한국 ETF cache를 downloader 대신 주입하고 CLI main을 실행했다. start/end로 자르고 요청 티커만 골랐으며, 계산·선정·파일 출력 함수는 교체하지 않았다. 이번에는 네트워크와 현재 시세 다운로드의 정상성을 검증한 것이 아니다.

다음 예제는 캐시·네트워크 없이 1~4번과 잘못된 top_n을 재현한다.

```python
import numpy as np
import pandas as pd
from etf_momentum.backtest import BacktestConfig, select_holdings, previous_month_end
from etf_momentum.data import _clean_prices
from etf_momentum.signals import rank_momentum
from etf_momentum.portfolio import PortfolioConfig, run_portfolio, sleeve_returns_on_calendar

days = pd.bdate_range('2024-01-01', periods=350)
prices = pd.DataFrame({'A': 100 * 1.001**np.arange(350),
                       'BENCH': 100 * 1.0005**np.arange(350)}, index=days)
eligible = pd.DataFrame({'ticker': ['A'], 'eligible': [True]})
print(select_holdings(eligible, prices, days[-1],
      BacktestConfig(top_n=1, benchmark='MISSING', risk_on_benchmark=True)))

prices.loc[days[-60]:, 'A'] = np.nan
print(rank_momentum(_clean_prices(prices), benchmark='BENCH',
      candidate_tickers=('A',))[['ticker', 'eligible']])
print(previous_month_end(pd.bdate_range('2026-09-01', '2026-09-15'),
                         pd.Timestamp('2026-10-07')))
print(select_holdings(eligible, prices, days[-1], BacktestConfig(top_n=0)))

calendar = pd.bdate_range('2021-01-01', periods=20)
foreign = pd.Series([100., 100., 200., 200., 200.],
                    index=calendar[[0, 1, 12, 13, 19]])
returns = sleeve_returns_on_calendar(foreign, calendar, 'previous_close')
equity, _ = run_portfolio(returns.to_frame('A'), PortfolioConfig(weights={'A': 1.}))
print(returns, equity.iloc[-1])
```

## 한계와 변경 범위

이번 검토는 저장된 실제 시계열의 실행과 합성 경계 조건 재현을 결합했다. ETF 상품명·계좌 규정·2025년 이후 원자료는 독립 검증하지 않았고, 체결 지연 모델을 새로 구현하거나 전체 32/96개 워크포워드를 재계산하지 않았다. 실제 과거 시계열에서 이번 경계 문제가 얼마나 자주 발생했는지와 전체 성과 영향은 별도 감사가 필요하다.

전략 코드·설정·기존 결과 파일은 수정하지 않았다. 개선 제안과 이 리뷰 문서만 추가했다. 메타데이터가 깨끗한 검토 commit을 기록하도록 실측 실행을 문서 생성 전에 완료했다.
