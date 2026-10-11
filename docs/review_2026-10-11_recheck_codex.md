# 전체 코드 재검토·계산 및 경계 조건 재현 — 2026-10-11

## 판단과 확인된 문제

**이전 N01~N06은 기존 재현 조건에서 모두 수정되었다.** 현재 HEAD의 전체 테스트는 **318개 통과**했다. 다만 새 네이버 보완과 현금 검사의 인접 경계에서 **새 P2 3건**을 확인했다. 테스트 성공과 아래 경계 오류는 별개다.

| ID | 등급 | 확인된 문제 | 재현 결과 |
|---|---|---|---|
| C01 | P2 | 원종가 수익률을 수정주가에 곱해 분배락을 다시 손실로 반영 | 기대 [90,90,90], 실제 [90,81,90]; 가짜 −10%/+11.111% |
| C02 | P2 | 빈 정상 형식 네이버 응답을 보완 성공으로 캐시 | 실제 거래일 1개 누락, 성공 sidecar 생성, 다음 고정 end 조회에서 재시도 0회 |
| C03 | P2 | 현금 검사가 실제 조합 평가 구간 밖까지 검사 | 2024년 시작 조합을 2023년 현금 공백 때문에 거부 |

ETF를 선정하는 컨셉에 맞는 설정·후보·신호·선정·기록 구조가 유지되고, 주요 계산식은 독립 검산과 일치한다. 정상 캐시의 현재 v3 월말 선택도 rank/weekly/backtest가 일치했다. 그러나 **네이버 보완 자료가 수정주가와 같은 수익률 의미를 유지하는지 먼저 해결해야 한다.** C01은 잘못 보완한 가격을 실제 관측으로 취급해 신호·성과에 전달한다. C02의 성공 판정과 재시도 정책, C03의 검사 기간도 함께 고치는 것이 좋다.

이번 재검토는 제품 코드를 수정하지 않았다. 이전 리뷰 문서는 보존하고 현재 결과를 이 문서에 별도로 기록했다.

## 기준·환경·범위

- 시작·실행 HEAD: `107d2ec23169c1c4e1849664eff2aca91d1b299c`, 시작 작업 트리 clean. 검토 중 HEAD 변화 없음.
- 비교는 이전 실행 HEAD `b1917cf5750ce20056a09e50a2bc471bcdb075a7`과 현재 HEAD의 두 스냅샷이다. 변경분만 검토한 것이 아니라 전체 호출 경로를 다시 확인했다. 제품 미커밋 변경은 시작 시 없었다.
- 이전 이후 변경: backtest 체결/월말/벤치마크/워밍업, CLI 거래대금/현금, 유니버스 저장 rollback/hash, 새 naver.py 및 캐시 연결, 관련 테스트·문서·연구 수치.
- Python 3.14.3, pandas 3.0.3, NumPy 2.4.6, pytest 9.0.3, exchange-calendars 4.13.2, yfinance 1.3.0. 기존 `.venv` 사용, 설치 없음.
- 적용: `.agents/skills/etf-momentum-code-review/SKILL.md`와 checklist/report-format. 해당 스킬의 절차에 따라 독립 리뷰어 1명을 사용했다. 세션 이력·예상 버그를 전달하지 않았고 다른 리뷰어를 생성하지 않았다. 독립 지적 2개는 주 리뷰어가 직접 재현했고, C03은 주 리뷰어의 별도 경계 검토다.
- 확인한 구현 영역: cli, strategy, data, naver, signals, backtest, walkforward, portfolio, records, weekly, reporting, universe_builder, 패키지 초기화와 두 scripts. 설정/후보→원자료/캐시/관측표→신호/선정→회계/성과→기록의 경계를 대조했다.
- 확인한 문서/설정: README, CLAUDE, pyproject, 전략/조합 JSON, 유니버스/meta, 관련 설계/계획, 연구 요약, 이전 리뷰와 새 테스트. 적용되는 AGENTS.md는 작업 영역에서 발견하지 못했다.
- 현재 v3 두 전략: 788개 목록, top4, 그룹 제한 없음, 63/126/252·skip21·0.3/0.3/0.4, SMA200, vol63, 거래대금10억, KODEX200 필터, 프록시 사용. 그룹 제한 없음은 사용자 결정이다. 퇴직연금의 허용 표시 728개이며 실제 선정 후보는 벤치마크/현금을 별도 제외한다.
- 계좌 매수 가능 표시의 이름 근사와 증권사 목록 미대조는 현재 사용자 정책이다. 추가 주문·통합 배분·증권사 목록 기능을 필수 개선사항으로 요구하지 않았다.
- 네트워크 요청 없이 합성 자료와 로컬 캐시만 사용. 실험 파일은 `/private/tmp/etf-review-20261011-recheck`에 분리하고 운영 이력에 쓰지 않았다. CLI 다운로드만 reader/mock으로 대체했다.
- 리뷰 CLI 11개 run_config 모두 현재 HEAD/dirty=false로 기록됐다(보고서 생성 전 실행). clean이라는 이유만으로 공식 연구 결과로 취급하지 않는다. 다운로드를 대체한 **리뷰 실험**이다. 완료 후 새 보고서만 untracked가 된다.

## 확인된 발견사항

### C01 — 분배락일의 원종가 등락을 수정주가에 중복 반영한다

위치: `src/etf_momentum/naver.py:67`~`:78`, 특히 `:73`; `src/etf_momentum/data.py:249`~`:261`의 auto_adjust 가격/네이버 보완 경로. 호출: download_prices → _cached_download(auto_adjust=True) → fill_gaps → raw.notna 관측표 → 신호/회계.

야후 가격은 수정주가인데 보완일은 직전 수정주가 × 네이버 **원종가** 비율로 계산한다. 조정계수가 일정한 평상시에는 맞지만, 결측일에 현금분배 등의 조정계수가 바뀌면 분배락을 가격 손실로 다시 넣는다. 이후 정상 야후 수정주가로 복귀하는 날에는 가짜 반등이 생긴다. README의 '직전 종가 × 네이버 등락률' 설명은 구현을 설명하지만 수정주가 수익률 계약을 해결하지 않는다.

최소 합성 입력: 2024-01-02~04 세 거래일. 분배락일에 현금10 지급, 원종가 [100,90,90], 올바른 과거 조정 가격 [90,90,90]. 야후 가운데 값만 NaN. 네이버 기준 달력은 세 날짜 모두 포함한다.

| 계산 | 기대 | 실제 |
|---|---|---|
| 보완 수정주가 | 90 | **90×90/100=81** |
| 전체 가격 | [90,90,90] | **[90,81,90]** |
| 일간 수익 | [0%,0%] | **[−10%,+11.111111%]** |
| MDD | 0% | **−10%** |

기대 수정주가를 입력으로 직접 정한 검산이며 실제 특정 ETF의 분배일 장애를 외부 시세로 확정한 것은 아니다. 독립 리뷰어도 다른 날짜/5행 입력으로 같은 결과를 재현했다.

영향: 월말 모멘텀·SMA·변동성·상대강도·그룹 상관·보유 수익 및 체결 비용 기준이 달라질 수 있다. 잘못 보완한 값도 notna=True이며 캐시된다. 이 예의 양 끝 가치/CAGR은 회복되어 같아도 MDD와 일간 수익은 틀린다. 실제 저장 연구 성과가 얼마나 바뀌는지는 이번 합성 재현으로 측정하지 않았다.

수정 방향: 기업행사를 반영한 조정 수익률/조정계수로 연결한다. 양쪽 야후 관측의 조정계수와 네이버 원종가의 관계가 바뀐 구간은 보완을 보류하거나 불확실 자료로 표시한다. 단순히 모든 원종가를 상수 배율로 바꾸거나 양 끝만 맞추는 보간은 분배금 수익을 해결하지 못한다. 정상 일정배율·분배락·분할·여러 연속 결측·월말 결측·재개일을 회귀 검증한다.

### C02 — 빈 기준 응답을 성공으로 기록하고 고정 캐시에 남긴다

위치: `src/etf_momentum/naver.py:43`~`:47`, `:56`~`:65`; `src/etf_momentum/data.py:235`~`:236`, `:265`, `:273`~`:287`.

헤더만 있는 정상 형식 응답은 parser를 통과해 빈 Series가 된다. fill_gaps는 빈 기준 거래일을 사용하여 결측을 찾지 못한 채 성공한다. 다운로드는 `.naver_fill.json`을 생성하고, 재사용은 그 파일의 **존재**만 검사한다. end가 고정이면 새 네이버 응답이 정상이어도 캐시를 다시 보완하지 않는다.

재현: 위 세 날짜 가운데 1일이 없는 야후 입력, 첫 네이버 응답을 헤더만 있는 바이트로 parser 검증한 뒤 빈 Series로 mock. 첫 호출 refresh=True로 tmp에 캐시를 만든다. 두 번째 호출은 정상 제공자가 돌아왔다는 상황으로, 재호출이 있으면 확인되도록 Yahoo/Naver mock을 설정한다.

실제: 요청 거래일로 reindex하면 누락 **1개** 유지. 성공 기록은 `filled={}, filled_days=0, reference=069500.KS`. 두 번째 호출의 네트워크 호출은 **0회**. 한 티커만 있는 원자료의 전부 NaN 행은 clean 과정에서 행 자체가 없어질 수 있으므로 검증은 요청 날짜에 reindex해서 수행했다.

기대: 알려진 국내 평가 기간인데 기준 거래일 응답이 비거나 충분히 덮지 않으면 보완 실패/미완료로 처리하고 재시도 가능성을 남긴다. 실제 종목이 거래하지 않은 날까지 강제로 보완하라는 지적은 아니다. 빈 요청 기간과 정상 휴장만 있는 요청도 구분해야 한다.

영향: 공백이 남은 자료가 보완 완료로 고정되어 재사용된다. 긴/월말 공백은 뒤 검증에서 멈출 수 있지만 짧은 공백은 계산용 ffill로 넘어갈 수 있다. '예외 발생 시 캐시하지 않음' 테스트는 통과해도 정상 형식의 빈/부분 응답은 이 방어를 우회한다.

수정 방향: 기준 응답의 빈 값·기간 범위·세션 완전성을 검사하고 완료/미완료 상태를 명시한다. 성공 표시에는 검사 기간·보완 실패/잔존 날짜와 입력 파일 해시 등을 연결해 재사용 여부를 판단한다. 헤더만 응답, 중간 날짜가 잘린 응답, 정상 재개, 특정 종목 데이터 없음, 실제 휴장 요청을 회귀 검증한다.

### C03 — 현금을 보유하지 않은 과거 기간까지 현금 공백을 검사한다

위치: `src/etf_momentum/cli.py:974`~`:987`, `:1073`~`:1076`; `src/etf_momentum/portfolio.py`의 run_portfolio 공통 구간 정렬보다 먼저 검사한다.

새 검사는 N05의 실제 보유 현금 공백을 막는다. 그러나 전체 calendar를 검사하므로, 모든 사용 슬리브가 시작하기 전이나 종료한 뒤의 현금 공백도 실행을 막는다. 포트폴리오는 모든 사용 슬리브의 공통 유효 구간에서만 평가하므로 검사는 실제 사용 구간에 맞아야 한다.

재현: CAL/CASH는 2023-01-02~2024-04-30, cash 원자료만 2023-02-01~03-15 결측(31영업일). 사용 equity_csv는 2024-01-02 시작, KRW same_day, weight=1/leverage=.5. 실제 조합 평가 구간에는 cash 결측이 전혀 없다.

실제: `Cash ETF CASH has no close for 31 evaluation days up to 2023-03-15` 오류로 종료. 기대 정상 실행. 검사 함수만 bypass한 별도 측정에서는 기존 실제 회계 함수의 첫 기준일이 **2024-01-02**, 그 곡선 날짜의 cash 누락은 **0개**임을 확인했다. bypass 결과는 구현 승인이나 공식 성과로 쓰지 않고 기대 평가 기간 확인에만 사용했다.

영향: 정상 조합을 불필요하게 거부한다. 성과를 잘못 계산하기보다 지원 입력의 실행 가능성을 막는 P2다. 이전 N05를 다시 허용하라는 지적이 아니다.

수정 방향: 실제 사용 슬리브의 공통 구간/시나리오별 유효 날짜를 먼저 구하고, 양의 reserve가 있는 평가 구간에서 cash 원관측을 검사한다. 최초 기준값을 위한 직전 관측도 적절히 포함한다. 슬리브 전/후 cash 공백, 사용 중 공백, 0비중 슬리브, leverage=1/차입 및 여러 leverage 시나리오를 회귀 검증한다.

## 이전 지적의 현재 재현

| 항목 | 현재 판정 | 기대·실제 |
|---|---|---|
| N01 휴장일 next_close | 해결 | 12/30 신호 → **2026-01-02** 체결 |
| N02 월중 벤치마크 11행 공백 | 해결 | SPY StaleDataError로 중단 |
| N02 월말 실제 세션 누락 | CLI 경로 해결 | sessions 전달시 3/31 오류, rank도 동일 오류 |
| N03 프록시 거래대금만 중단 | 해결 | 평균 NaN, price_fresh=True지만 liquid=False/eligible=False |
| N04 정확히 6행 readiness | 해결 | rank eligible=True, backtest 첫 신호/기준일 **1/31** |
| N05 사용 중 현금 32행 공백 | 해결 | leverage=.5에서 CASH 오류로 중단; leverage=1은 실행 |
| N06 두 번째 replace 실패 | 예외 경로 해결 | 이전 CSV/meta 보존, 성공 생성 CSV SHA-256 일치 |
| R01 FX만 stale | 해결 유지 | 나이 5=True, 6/10=False |
| R02 잘린 기록 | 해결 유지 | 15필드/기대16 오류, bytes 불변 |
| R03 미사용 USD 슬리브 | 해결 유지 | 0비중 파일/FX를 읽지 않고 동일 곡선 |
| R04 중복 티커 | 해결 유지 | 로더 오류, 반복 후보 라이브러리 결과 1행 |

N02는 라이브러리에서 sessions를 생략하면 과거 관측일 규칙이 남는다. 현재 docstring이 이 옵션 차이를 명시하고, 실제 backtest/compare/walkforward CLI는 세션을 전달하므로 기존 CLI 문제의 해결로 판정했다. N06은 프로세스 강제 종료까지 두 파일을 원자 교체한 것이 아니라, 예외 rollback과 CSV 해시를 추가한 것이다. crash 사이의 불일치 감지는 해시 대조가 필요하고 load_universe가 자동으로 meta 해시를 검증하는 구조는 아니다. 이번 판정은 직접 재현한 예외 경로에 한정한다.

초기 F01/F04 혼합 시장 오류, F02 중단 가격 프록시 stale, F03 필수 현금 누락, F05 보유 자산 장기 공백, F06 미사용 슬리브 불변, F07 최초 비용, F08 NaN 비중 exit2, F09 선택된 test 수익 64행 누락 오류, F10 사용자 임계값 설명도 현재 코드로 다시 확인했다. 세부 입력/기대값은 부록 normal_and_prior.py에 보존했다.

## 검증 실행·계산 실측

### 전체 테스트

```bash
env MPLCONFIGDIR=/private/tmp/etf-review-20261011-recheck/mpl \
    XDG_CACHE_HOME=/private/tmp/etf-review-20261011-recheck/cache \
    .venv/bin/python -m pytest -q
```

**318 passed in 26.22s.** tests/conftest.py는 NAVER_FILL=False를 자동 적용한다. 새 naver 테스트는 fake fetch와 True 설정으로 기본 보완/실패/부분 다운로드를 검증하지만 기업행사·빈 기준 응답 경계는 포함하지 않는다. 별도 재현은 기존 테스트 개수에 포함하지 않는다.

### 독립 수식과 시점 검산

| 항목 | 입력·기대식 | 실제 |
|---|---|---|
| 기본 모멘텀 | A=100×1.002^n, 700행; Σw×(P[t−21]/P[t−lookback−21]−1) | 기대=실제 .38792207964425396 |
| 과거 신호 미래 불변 | cutoff 이후 A×100 | ranking frame 동일 |
| 보유 수량 | A/B .5씩, A +10% 뒤 −10%, B 0%; .5×1.1×.9+.5 | .9950000000000001 |
| 일간 재조정 | (1+.5×.1)(1−.5×.1) | .9974999999999999 |
| 드리프트 | A 종료 .495 / 총 .995 | .49748743718592964 |
| 상한 | [.9,.1] 상한 .6→합1; 후보 하나→합.6 | 일치 |
| CAGR | 1→1.2, 366일; 1.2^(365.25/366)−1 | .19955175204308273 |
| MDD | [1,1.1,.99,1.2] / cummax −1 | −.10000000000000009 |
| 미국 이전 종가+환율 | 100→110, 1000→1200; 1.1×1.2−1 | .32000000000000006 |
| 같은 날짜 미래 값 | US=999, FX=9999 같은 날짜에 추가 | 이전 날짜 32% 유지 |
| 최초 비용 | 수익0, 비용100bp | 곡선 [1,.99], 비용.01, ending .99 |
| 워크포워드 | 미래 2022년 −40%로 변경 | 첫 2021년 선택 불변 |

허용 오차 약 1e−12~1e−10. frame/선택값은 동일성 검사다. 독립 리뷰어는 다른 입력으로 모멘텀 .05885687592571498, buy-and-hold 1.15/daily-rebalanced 1.155, 기간 CAGR/MDD와 previous_close를 별도 확인했다.

### 실제 캐시의 현재 전략 실행

end=2026-10-07 배제, 마지막 평가 10/6. rank/weekly lookback-years=5. 로컬 788개 원자료/거래대금은 2021-10-06 시작 latest 파일이며 프록시/FX는 고정 2010 시작 파일이다. 다운로드/Naver 호출을 reader로 대체했으므로 이 실행은 현 저장 캐시의 측정이며 새 네이버 보완 전체를 적용한 입력 측정으로 주장하지 않는다.

먼저 backtest start=2023-01-01로 시도했지만 **2023-07-31 벤치마크 공백 때문에 새 세션 검사가 중단**했다. 해당 788개 캐시는 네이버 성공 sidecar가 없는 이전 자료이고, 실제 downloader라면 재조회 대상이다. 리뷰 reader가 그 재조회를 우회했으므로 실행 실패를 새 기본 백테스트 버그로 분류하지 않았다. 성공 수치로 덮어 숨기지도 않았다.

이후 start=2024-01-01의 제한 구간으로 두 계좌/두 체결 방식을 실행했다. 워밍업 후 곡선 **2025-02-28~2026-10-06, 390행, 월간 포지션20개**. 9월30일 선택은 두 계좌 모두 `442580.KS,367760.KS,395270.KS,363580.KS`다. rank/weekly ranking 전체가 같고, 같은 월말 backtest의 현금을 제외한 보유 집합도 같았다. hold_until은 다음 trade_date와 일치했다.

아래 수치는 성공 곡선을 **2025-03-31~2026-10-06**로 잘라 다시 1로 만든 리뷰 측정이다. 2016년 시작 연구 성과와 구분한다.

| 계좌 | 체결 | CAGR | MDD |
|---|---|---|---|
| pension | same_close | 49.0214% | −55.6490% |
| pension | next_close | 54.4793% | −52.7607% |
| retirement | same_close | 44.4943% | −40.4019% |
| retirement | next_close | 44.0774% | −37.8349% |

미국 compare: top_n 2/3 작은 그리드 2행. walkforward: start2024, train_years1/test_months12, 표본외 창1개/설정2개. 기본 portfolio spec: summary18행/곡선4123행. 운영 출력 대신 tmp만 사용했다.

### 유니버스와 저장 연구 표

그룹 생성 당시의 788개 `2025-04-08_latest` 원자료로 최근252행(2025-09-23~2026-10-08)을 사용해 다시 계산했다. 저장 분할 **96그룹/485ETF**와 동일하며 그룹 내 **5437쌍**, 최저 상관 **.9000376584548322**로 모두 ≥.9였다. 이는 해당 저장 입력의 일치 검산이며 새로 모든 종목에 Naver 보완을 적용한 후의 그룹과 같다는 주장은 아니다. 788티커 중복 없음, 현금만 risk_asset0, 합성/선물 이름 표시 및 retirement 허용728개 일치.

builder CLI의 합성 목록에서는 제외 후4개/미가격000005.KS 보고/raw=True 다운로드, 현금 누락 시 이전 두 파일 보존, 성공 CSV/meta 해시 일치를 확인했다. 기존 테스트의 사슬 연결·관측63일·정체·순서/대표명 조건도 검토했다.

research_tables 전체를 로컬 현금 reader로 실행해 tmp Markdown으로 출력했다. 전체 ETF 표는 현재 연구 문서와 표시값이 일치했다. 아래 세 저장 곡선의 CAGR/MDD/Sharpe는 직접 수식으로 검산해 구현과 1e−10 이내 일치했다.

CAGR=(V끝/V시작)^(365.25/경과일)−1, MDD=min(V/cummax(V)−1), Sharpe=mean(r−r_cash)/std(r−r_cash,ddof1)×√252. 현금수익을 각 날짜로 정렬하고 미대응 현금수익은 기존 구현과 동일하게0을 적용했다. 식 일치는 보완 가격의 진실성 검증을 대신하지 않는다.

| 저장 곡선 | 2016-02-29~2025-02-28 CAGR/MDD/Sharpe | ~2026-10-06 CAGR/MDD |
|---|---|---|
| 기존60개 | 11.5526% / -16.3557% / 0.7559 | 17.5448% / -43.3141% |
| 788개 그룹당1개 | 9.0979% / -18.1419% / 0.5709 | 11.7824% / -53.3791% |
| 788개 그룹 제한 없음 | 9.3241% / -36.0373% / 0.5343 | 13.8155% / -54.5617% |

세 곡선은 주기준2211행/전체2600행으로 일치한다. 이전 보고서와 비교해 저장 결과가 재생성됐고 날짜 수와 수치도 바뀌었다. 이번 측정은 현재 파일을 읽어 다시 계산한 값이다. 전체 원본 WF 그리드를 다시 생성한 것은 아니다. 저장 WF는 학습에서 top3/4 등을 선택하는 결과이며 고정 v3 top4 JSON의 결과와 구분한다.

로컬 sidecar 집계: 고정 benchmark32일, cash33일, 34ETF1200일, 60ETF1886일을 보완했다고 기록한다. 이 기록을 읽었을 뿐 각 보완 값/분배일을 외부 원자료와 대조하지 않았다. C01은 보완 전체의 감사가 필요한 이유이며 위 연구 집계가 실제 투자 성과와 같다는 보증은 아니다.

## 제한·선택적 개선

- 실제 Naver/Yahoo 서버 응답, 새로운 외부 시세·분배금·조정계수, 증권사 매수 가능 여부는 조회하지 않았다. 내부 버그는 합성 입력으로 확정하고 실제 보완 가격 중 영향받은 날짜/ETF 개수는 미확인으로 둔다.
- 788개 전체의 2010 시작 raw 캐시가 없어 긴 전체 그리드/WF 재실행은 하지 않았다. 현재 짧은 캐시 CLI와 저장된 긴 결과의 계산/문서 대조까지만 검증했다.
- 현재 상장 ETF만 있는 생존 편향, 현재 그룹의 과거 적용, WF 설정 전환 비용 미반영은 문서화된 연구 한계다. 사용자 결정인 그룹 제한 없음도 구현 버그로 재분류하지 않았다.
- 현재 검토는 모든 모듈/주요 호출 경계를 읽고 전체 테스트와 대표 계산/경계 재현을 적용했다. 체크리스트의 모든 조합을 독립 실행한 것은 아니다.
- 입력 증거 개선으로 Naver 보완 전/후값·원종가·조정계수·보완 코드 버전/출처 응답 해시를 남기면 후속 검산이 쉬워진다. 이를 추가 주문/배분 기능의 필요성으로 확대하지 않는다.
- 가격 자체가 검증되기 전에 성과 수치가 좋아지거나 나빠진 것을 새 전략의 우열로 판단하지 않는다. 분배락 검증이 이번 보완 경로의 우선 과제다.

## 재현과 입력 보존

아래 스크립트는 표시된 이름으로 `/private/tmp/etf-review-20261011-recheck`에 저장한 뒤 리포지터리 루트에서 실행한다. 출력/가짜 캐시는 tmp만 사용한다. new_checks.py에서 현금 검사를 한 번 bypass하는 부분은 C03의 실제 공통 평가 시작일을 측정하는 용도이며 제품 코드 수정이 아니다.

```bash
mkdir -p /private/tmp/etf-review-20261011-recheck
# SCRIPT를 아래 부록 파일 이름으로 바꿔 각각 실행한다.
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-review-20261011-recheck/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261011-recheck/cache .venv/bin/python /private/tmp/etf-review-20261011-recheck/SCRIPT
```

replay.py/new_boundaries.py는 수정 후 기대값을 assert한다. new_checks.py는 현재 오류가 나타나는 것을 assert하므로 실행 성공이 버그 해결을 뜻하지 않는다. 새 수정 후에는 기대 동작에 맞춰 assert도 재평가해야 한다.

### 입력 SHA-256

| 파일 | SHA-256 |
|---|---|
| `config/portfolio_pension.json` | `a5da6a8368ff533e5914466ed742f3d550267ef971813100b4f1201b22b2f13f` |
| `config/strategies/pension.json` | `f5bbf6f22f20f87596c0aab6d045a301ebc4b9f4b59b465494986627a438f95b` |
| `config/strategies/retirement.json` | `98a3cd96a425fafad92b33f022fa0e2c1a14c6d1b0e066a4460ca09f0b7604f5` |
| `config/universe_kr_all.csv` | `ee4047581bffc7095301436cef5bc5d6e88ac81de31165fa5f74b813fdc21f98` |
| `config/universe_kr_all.meta.json` | `98520bf072641611cc44fff36e09f266fc71963e4313a3e09b367e7e36e1c4fa` |
| `data/prices_raw_069500_KS_153130_KS_KRW=X_SPY_2010-01-01_2026-10-07.csv` | `fa7a0d00fc0dfd396e631691bb85dd4314eb406a48315b6376a7b036e9b5244c` |
| `data/prices_raw_069500_KS_2010-01-01_2026-10-07.csv` | `2c0e48eecfa8f8679a96820f6cb5d11d94db22fbea04409e2da349f2e020c53f` |
| `data/prices_raw_069500_KS_2010-01-01_2026-10-07.csv.naver_fill.json` | `1bb4dd18dd39697a65964511438c9ddf7e45e087278983103de9a140cadd0088` |
| `data/prices_raw_153130_KS_2010-01-01_2026-10-07.csv` | `ce1eacee835098e5c04edb4b326b607f10a081b210c574227307996b07e2e024` |
| `data/prices_raw_153130_KS_2010-01-01_2026-10-07.csv.naver_fill.json` | `c64b2dc23db0255d431e34f2a3abd3fb481e23fe6f9bc56c4f580b04c3f99ba3` |
| `data/prices_raw_34_tickers_e1eb35400758fc2b_2010-01-01_2026-10-07.csv.naver_fill.json` | `a3ab5965d8ed0afe331d1f1c1fd6d3237d1262f8e3d0acb8bf63a4e50d5ded58` |
| `data/prices_raw_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv.naver_fill.json` | `83f7f61b5be09123815240228235e2ae3260d06c1c084355725bd65dd2bb3820` |
| `data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2021-10-06_latest.csv` | `0d71d40818da9d68ece57597d48f1430bfade237f0d1e6d8f40a29e63bcba978` |
| `data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2025-04-08_latest.csv` | `e378d31a93f6c70bca3e12128ac6beb33a33cbab151881df71e9a74fb8924d8e` |
| `data/prices_raw_BIL_CIBR_DIA_GLD_IBB_ICLN_IGV_ITA_IWM_IYT_KRE_QQQ_SMH_SOXX_SPY_TAN_URA_VNQ_XBI_XLB_XLC_XLE_XLF_XLI_XLK_XLP_XLRE_XLU_XLV_XLY_XME_XRT_2010-01-01_2026-10-07.csv` | `4131f72107ab3c9f3fe3fc26f7ad4d85da497f82f17ba5b17ae4e1d5a558dd2f` |
| `data/prices_raw_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv` | `7aca8716bd62d88f2092b78f11ba7cdbcfc7d67b341f564e895142e3f86453c3` |
| `data/prices_raw_KRW=X_2010-01-01_2026-10-07.csv` | `fe32ed2511a9ad520bd920f723e9c9c1c4136b99264a8d71d002f70c625fab8a` |
| `data/prices_raw_SPY_2010-01-01_2026-10-07.csv` | `a9404c87bc09eaec42cb89140ee0c7be6883fb55ece9821372d402ae0387f83b` |
| `data/traded_value_788_tickers_1d20b3b9c5fefa8f_2021-10-06_latest.csv` | `3b1c2cd79b7b6a39f5de72d00ab49ee6d9db90df5450296c1733ea471f4c8c75` |
| `data/traded_value_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv` | `f7a1fb2eddc6c9d6ba9516b96410fe89745b9082d0fde74f924ce2d089d2f596` |
| `results/walkforward/kr_all_pension_10eok_10bp_nogroup_tvoff/walkforward_equity.csv` | `65d5e7077687a135dcf3d6ead938eb4e2002715915754799228a7802c5c17ea1` |
| `results/walkforward/kr_all_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv` | `68dad066c2b758bc1c13f1314aeb81ba6170a0d47fbf307a8c6dc69a11ff70c2` |
| `results/walkforward/kr_pension_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv` | `42e97e653cc8fa3919ca0246b3e05687a071b55fc5ccb8def0838326cd6e6c7b` |

### 부록: new_checks.py — 새 문제3건

```python
import contextlib,io,json,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum import naver,cli
from etf_momentum.backtest import summarize_performance
out=Path('/private/tmp/etf-review-20261011-recheck');results={}
# Raw ex-distribution closes 100->90->90, distribution 10 on day 2.
# Correct backward-adjusted closes are 90->90->90 (flat total-return path).
days=pd.bdate_range('2024-01-02',periods=3)
yahoo=pd.DataFrame({'A.KS':[90.,np.nan,90.]},index=days)
raw=pd.Series([100.,90.,90.],index=days)
filled,report=naver.fill_gaps(yahoo,fetch=lambda ticker,start,end: pd.Series(100.,index=days) if ticker==naver.REFERENCE else raw)
assert filled['A.KS'].tolist()==[90.,81.,90.]
metrics=summarize_performance((filled/filled.iloc[0]).rename(columns={'A.KS':'x'}))
assert np.isclose(metrics['x_max_drawdown'],-.1)
results['distribution']={'expected_adjusted':[90.,90.,90.],'actual_adjusted':filled['A.KS'].tolist(),'actual_returns':filled['A.KS'].pct_change().dropna().tolist(),'expected_mdd':0.,'actual_mdd':metrics['x_max_drawdown'],'filled':report}
# Cash data gap precedes the actual portfolio common evaluation period.
cal=pd.bdate_range('2023-01-02','2024-04-30')
p=pd.DataFrame({'CAL':100.,'CASH':100*1.0001**np.arange(len(cal))},index=cal)
p.loc['2023-02-01':'2023-03-15','CASH']=np.nan
sleeve=pd.DataFrame({'strategy':100*1.001**np.arange(len(cal[cal>='2024-01-02']))},index=cal[cal>='2024-01-02'])
sleeve.to_csv(out/'late_sleeve.csv')
spec={'base_currency':'KRW','calendar_ticker':'CAL','cash_ticker':'CASH','start':'2023-01-02','end':'2024-05-01','sleeves':{'late':{'equity_csv':str(out/'late_sleeve.csv'),'currency':'KRW'}},'scenarios':[{'name':'late','weights':{'late':1.},'leverage':.5}]}
(out/'late_spec.json').write_text(json.dumps(spec))
def local(tickers,raw=False,**kw):
 f=p[tickers].dropna(how='all');return f if raw else f.ffill()
argv=['etf-momentum','portfolio','--spec',str(out/'late_spec.json'),'--output-dir',str(out/'late_output')]
with patch.object(cli,'download_prices',local),patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()):
 try:cli.main()
 except SystemExit as e:results['cash_before_common_period']={'expected':'succeeds: actual portfolio starts 2024-01-02, cash complete there','actual':str(e)}
 else:raise AssertionError('pre-common cash gap accepted; re-evaluate suspicion')
# Confirm real portfolio period with cash-gap check bypassed solely for measurement.
with patch.object(cli,'download_prices',local),patch.object(cli,'_require_cash_closes',return_value=None),patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()):cli.main()
eq=pd.read_csv(out/'late_output/portfolio_equity.csv',index_col=0,parse_dates=True)
assert eq.index[0]>=pd.Timestamp('2024-01-02')
results['cash_before_common_period']['measured_first_date']=str(eq.index[0].date())
results['cash_before_common_period']['cash_missing_in_measured_period']=int(p.CASH.reindex(eq.index).isna().sum())
(out/'new_checks.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results,ensure_ascii=False,indent=2))
# Syntactically valid empty Naver response is accepted and freezes a missing day.
import warnings
from etf_momentum import data
from io import BytesIO
with patch.object(naver.urllib.request,'urlopen',return_value=BytesIO("[['날짜','시가','고가','저가','종가','거래량']]".encode())),patch.object(naver.time,'sleep',return_value=None):
 empty=naver.naver_closes('069500.KS',days[0],days[-1])
assert empty.empty
cache=out/'empty_naver_cache';cache.mkdir(exist_ok=True)
multi=yahoo.copy();multi.columns=pd.MultiIndex.from_tuples([('Close','A.KS')])
calls=[]
def empty_fetch(*args):calls.append(args[0]);return empty
with patch.object(data,'NAVER_FILL',True),patch.object(data,'_yf_download',return_value=multi),patch.object(naver,'naver_closes',empty_fetch):
 first=data.download_prices(['A.KS'],start='2024-01-02',end='2024-01-05',cache_dir=cache,raw=True,refresh=True)
with patch.object(data,'NAVER_FILL',True),patch.object(data,'_yf_download',side_effect=AssertionError('unexpected Yahoo retry')),patch.object(naver,'naver_closes',side_effect=AssertionError('unexpected Naver retry')):
 second=data.download_prices(['A.KS'],start='2024-01-02',end='2024-01-05',cache_dir=cache,raw=True)
assert second.reindex(days)['A.KS'].isna().sum()==1
marker=json.loads(next(cache.glob('*.naver_fill.json')).read_text())
results['empty_naver_response']={'first_missing':int(first.reindex(days)['A.KS'].isna().sum()),'second_missing':int(second.reindex(days)['A.KS'].isna().sum()),'first_naver_calls':calls,'second_network_calls':0,'success_marker':marker,'empty_response_parser_accepted':True}
(out/'new_checks.json').write_text(json.dumps(results,ensure_ascii=False,indent=2));print(json.dumps(results['empty_naver_response'],ensure_ascii=False,indent=2))
```

### 부록: replay.py — 이전 경계 수정 확인

```python
import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from etf_momentum import cli
from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.data import UniverseMember, load_universe, proxy_observations, splice_proxies
from etf_momentum.signals import MomentumConfig, rank_momentum
from etf_momentum.records import SIGNAL_COLUMNS, record_signal

OUT=Path('/private/tmp/etf-review-20261011-recheck')
OUT.mkdir(exist_ok=True)
res={}

# One-market stray row: rebalance date skips it but next_close executes on it.
idx=pd.bdate_range('2025-01-02','2026-02-27').drop(pd.Timestamp('2026-01-01'))
raw=pd.DataFrame({'A.KS':100*1.001**np.arange(len(idx)), 'BENCH.KS':100*1.0005**np.arange(len(idx)), 'CASH.KS':100.},index=idx)
raw.loc['2025-12-31',['BENCH.KS','CASH.KS']]=np.nan
mc=MomentumConfig(lookbacks=(21,42,63),skip_days=0,sma_window=50,volatility_window=20)
cfg=BacktestConfig(top_n=1,candidate_tickers=('A.KS',),benchmark='BENCH.KS',cash_ticker='CASH.KS',momentum=mc,execution='next_close')
eq,pos,_=run_backtest(raw.ffill(),cfg,observed=raw.notna())
dec=pos.loc[pos.rebalance_date==pd.Timestamp('2025-12-30')].iloc[0]
assert dec.trade_date==pd.Timestamp('2026-01-02')
res['N01_next_close']={'rebalance_date':str(dec.rebalance_date.date()),'actual_trade_date':str(dec.trade_date.date()),'expected_next_benchmark_date':'2026-01-02'}

# Historical benchmark holes: the downstream baseline checker sees finite zero returns.
idx=pd.bdate_range('2023-01-02','2023-05-31')
raw=pd.DataFrame({'A':100*np.exp(np.arange(len(idx))*.002),'SPY':100*np.exp(np.arange(len(idx))*.001),'BIL':100.},index=idx)
gap=(idx>='2023-03-06')&(idx<='2023-03-20')
raw.loc[gap,'SPY']=np.nan
cli._require_fresh(raw.notna(),{'benchmark':'SPY','cash':'BIL'})
cfg=BacktestConfig(top_n=1,candidate_tickers=('A',),momentum=MomentumConfig(lookbacks=(2,3,4),skip_days=0,sma_window=3,volatility_window=3),risk_on_benchmark=True)
try:run_backtest(raw.ffill(),cfg,observed=raw.notna())
except ValueError as e:res['N02_benchmark']={'run':'stopped','error':str(e)}
else:raise AssertionError('benchmark gap accepted')

# Proxy traded value must preserve the source's actual observation dates.
local=pd.bdate_range('2023-01-02','2023-06-30')
us=pd.bdate_range('2022-12-30','2023-06-30')
raw=pd.DataFrame({'A.KS':np.nan,'BENCH.KS':100.},index=local)
raw.loc['2023-06-26':,'A.KS']=100.
tv=pd.DataFrame({'A.KS':np.nan,'BENCH.KS':2e9},index=local)
tv.loc['2023-06-26':,'A.KS']=2e9
proxy=pd.DataFrame({'US':100*np.exp(np.arange(len(us))*.001),'KRW=X':1300.},index=us)
proxy_value=pd.DataFrame({'US':2e6},index=us)
proxy_value.loc['2023-05-01':,'US']=np.nan
args=argparse.Namespace(use_proxies=True,proxy_fx='KRW=X',end='2023-07-01',cache_dir=str(OUT),refresh=False)
with patch.object(cli,'download_prices',lambda *a,**kw:proxy),patch.object(cli,'download_traded_value',lambda *a,**kw:proxy_value),contextlib.redirect_stdout(io.StringIO()):
    prices,values=cli._apply_proxies([UniverseMember('A.KS','A','X',proxy='US')],raw,tv,None,args)
observed=cli._observations(raw,args,prices)
mc=MomentumConfig(lookbacks=(2,3,4),skip_days=0,sma_window=3,volatility_window=3,min_avg_traded_value=1e9,liquidity_window=10)
row=rank_momentum(prices,pd.Timestamp('2023-06-20'),config=mc,candidate_tickers=('A.KS',),traded_value=values,observed=observed).iloc[0]
assert not row.liquid and not row.eligible
res['N03_proxy_volume']={'source_last_date':str(proxy_value.US.last_valid_index().date()),'as_of':'2023-06-20','avg_traded_value':None if pd.isna(row.avg_traded_value) else float(row.avg_traded_value),'price_fresh':bool(row.price_fresh),'liquid':bool(row.liquid),'eligible':bool(row.eligible)}

# Warm-up exact row count: rank is ready but the backtest waits one extra row.
idx=pd.bdate_range('2023-01-24','2023-04-28')
panel=pd.DataFrame({'A':100*1.002**np.arange(len(idx)),'BENCH':100*1.001**np.arange(len(idx)),'CASH':100.},index=idx)
mc=MomentumConfig(lookbacks=(2,3,4),skip_days=0,sma_window=3,volatility_window=3)
row=rank_momentum(panel,pd.Timestamp('2023-01-31'),config=mc,candidate_tickers=('A',),observed=panel.notna()).iloc[0]
cfg=BacktestConfig(top_n=1,candidate_tickers=('A',),benchmark='BENCH',cash_ticker='CASH',momentum=mc)
eq,pos,_=run_backtest(panel,cfg,observed=panel.notna())
assert row.eligible and eq.index[0]==pd.Timestamp('2023-01-31')
res['N04_warmup']={'rows_at_Jan31':len(panel.loc[:'2023-01-31']),'required_rows':6,'rank_eligible':bool(row.eligible),'actual_first_signal':str(pos.rebalance_date.iloc[0].date()),'actual_base_date':str(eq.index[0].date()),'expected_first_signal':'2023-01-31'}

# Portfolio CLI ignores a cash price gap although the reserve is invested in cash.
cal=pd.bdate_range('2024-01-02','2024-04-30')
cash_raw=pd.DataFrame({'CAL':100.,'GOOD':100*1.001**np.arange(len(cal)),'CASH':100*1.0005**np.arange(len(cal))},index=cal)
cash_raw.loc['2024-02-01':'2024-03-15','CASH']=np.nan
def download(tickers,raw=False,**kw):
    frame=cash_raw[list(tickers)].dropna(how='all')
    return frame if raw else frame.ffill()
def run_portfolio_case(unused=False,leverage=.5):
    spec={'base_currency':'KRW','calendar_ticker':'CAL','cash_ticker':'CASH','start':'2024-01-02','end':'2024-05-01',
          'sleeves':{'good':{'ticker':'GOOD','currency':'KRW'}},'scenarios':[{'name':'g','weights':{'good':1.0},'leverage':leverage}]}
    if unused:
        spec['sleeves']['unused']={'equity_csv':str(OUT/'does_not_exist.csv'),'currency':'USD'}
        spec['scenarios'][0]['weights']['unused']=0.
    path=OUT/f'portfolio{unused}.json';path.write_text(json.dumps(spec))
    target=OUT/f'portfolio{unused}'
    with patch.object(cli,'download_prices',download),patch.object(sys,'argv',['etf-momentum','portfolio','--spec',str(path),'--output-dir',str(target)]),contextlib.redirect_stdout(io.StringIO()):
        cli.main()
    return pd.read_csv(target/'portfolio_equity.csv',index_col=0)
try:run_portfolio_case()
except SystemExit as e:res['N05_portfolio_cash']={'run':'stopped','error':str(e)}
else:raise AssertionError('cash gap accepted')
a=run_portfolio_case(leverage=1.);b=run_portfolio_case(True,leverage=1.)
pd.testing.assert_frame_equal(a,b)
res['R03_unused_sleeve']='same curve, no unused file or FX loaded'
cash_returns=cash_raw.CASH.ffill().pct_change().loc['2024-02-01':'2024-03-15']
assert len(cash_returns)==32 and cash_returns.eq(0).all()


# Recheck R01: exact 5/6/10 row boundaries with FX only halted.
local=pd.bdate_range('2020-01-02',periods=30)
us=pd.bdate_range('2020-01-01',periods=31)
proxy=pd.Series(100*1.01**np.arange(len(us)),index=us)
fx=pd.Series(1000.,index=us[:14])
raw=pd.DataFrame({'A':np.nan},index=local);raw.iloc[-1,0]=200.
prices,starts=splice_proxies(raw,{'A':proxy},fx)
seen=proxy_observations(proxy,fx,local[local<starts['A']])
obs=cli._observations(raw,argparse.Namespace(proxy_observed={'A':seen}),prices)
mc=MomentumConfig(lookbacks=(1,2,3),skip_days=0,sma_window=2,volatility_window=2)
res['R01_fx_age']={}
for age,expected in [(5,True),(6,False),(10,False)]:
    row=rank_momentum(prices,local[13+age],config=mc,observed=obs).iloc[0]
    assert bool(row.price_fresh)==expected
    res['R01_fx_age'][str(age)]=bool(row.price_fresh)

# Recheck R02: incomplete file fails without any byte mutation.
sel=pd.DataFrame([{'status':'selected','rank_in_selection':1,'ticker':'AAA','name':'','group':'','slots':1,'market_filter':'on'}])
def meta(date):
    return {'signal_date':date,'recorded_at':'2026-10-10T12:00:00','strategy_id':'s','version':1,'config_hash':'h','code_commit':'abc','code_dirty':False,'price_last_date':'2026-10-07'}
path=OUT/'records.csv'
path.write_text(','.join(SIGNAL_COLUMNS)+'\n')
record_signal(path,sel,meta('2026-08-31'))
path.write_bytes(path.read_bytes()[:-3]);before=path.read_bytes()
try:record_signal(path,sel,meta('2026-09-30'))
except ValueError as e:res['R02_truncated']={'error':str(e),'unchanged':path.read_bytes()==before}
else:raise AssertionError('truncated records accepted')
assert res['R02_truncated']['unchanged']

# Recheck R04 loader + library defense.
path=OUT/'duplicates.csv'
path.write_text('ticker,name,category,enabled\nA,Alpha,X,1\n a ,Alpha,X,1\n')
try:load_universe(path)
except ValueError as e:res['R04_duplicate_loader']=str(e)
else:raise AssertionError('duplicate accepted')
panel=pd.DataFrame({'A':100*1.002**np.arange(300)},index=pd.bdate_range('2020-01-01',periods=300))
ranking=rank_momentum(panel,candidate_tickers=('A','A'),observed=panel.notna())
assert ranking.ticker.tolist()==['A']
res['R04_duplicate_rank_rows']=len(ranking)

(OUT/'replay.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
print(json.dumps(res,ensure_ascii=False,indent=2))
```

### 부록: new_boundaries.py — 월말/두 파일 rollback

```python
from pathlib import Path
from unittest.mock import patch
import os, json
import numpy as np
import pandas as pd
from etf_momentum.universe_builder import write_universe
from etf_momentum.backtest import BacktestConfig, run_backtest, previous_month_end
from etf_momentum.signals import MomentumConfig
out=Path('/private/tmp/etf-review-20261011-recheck');out.mkdir(exist_ok=True)
csv=out/'atomic.csv';meta=out/'atomic.meta.json'
csv.write_text('OLD_CSV\n');meta.write_text('{"old":true}\n')
replace=os.replace
def fail_second(src,dst):
    if Path(dst)==meta: raise OSError('second replace failed')
    replace(src,dst)
try:
    with patch('etf_momentum.universe_builder.os.replace',side_effect=fail_second):
        write_universe(pd.DataFrame({'ticker':['NEW']}),{'new':True},csv)
except OSError: pass
else: raise AssertionError('fault not triggered')
assert csv.read_text()=='OLD_CSV\n' and json.loads(meta.read_text())=={'old':True}
idx=pd.bdate_range('2023-01-02','2023-05-31')
p=pd.DataFrame({'A':100*np.exp(.002*np.arange(len(idx))),'BENCH':100*np.exp(.001*np.arange(len(idx))),'CASH':100.},index=idx)
p.loc['2023-03-31','BENCH']=np.nan
mc=MomentumConfig(lookbacks=(2,3,4),skip_days=0,sma_window=3,volatility_window=3)
try:run_backtest(p.ffill(),BacktestConfig(top_n=1,candidate_tickers=('A',),benchmark='BENCH',cash_ticker='CASH',momentum=mc,risk_on_benchmark=True),observed=p.notna(),sessions=p.index)
except ValueError as e:print(e)
else:raise AssertionError('missing month-end accepted')
try: previous_month_end(p.index[p.BENCH.notna()],pd.Timestamp('2023-04-03'),sessions=p.index)
except ValueError as exc: print(exc)
else: raise AssertionError('missing month-end accepted')
print('N06: OLD pair preserved; N02B: backtest and rank both stop')
```

### 부록: normal_and_prior.py — 수식/이전 방어

```python
import argparse
import contextlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from etf_momentum import cli
from etf_momentum.backtest import BacktestConfig, _holding_period, _cap_weights, run_backtest, summarize_performance, previous_month_end
from etf_momentum.portfolio import PortfolioConfig, run_portfolio, sleeve_returns_on_calendar
from etf_momentum.signals import MomentumConfig, rank_momentum
from etf_momentum.walkforward import walk_forward

OUT = Path('/private/tmp/etf-review-20261011-recheck')
results = {}

days = pd.bdate_range(end='2026-10-06', periods=700)
n = np.arange(len(days))
prices = pd.DataFrame({'A':100*1.002**n, 'B':100*1.001**n,
                       'BENCH':100*1.0005**n,'CASH':100*1.0001**n}, index=days)
cfg = MomentumConfig()
rank = rank_momentum(prices, benchmark='BENCH', candidate_tickers=['A','B'], observed=prices.notna())
expected = sum(w*(prices.A.iloc[-22]/prices.A.iloc[-(l+22)]-1) for w,l in zip(cfg.weights,cfg.lookbacks))
assert np.isclose(rank.iloc[0].weighted_momentum, expected, atol=1e-12)
results['momentum_manual']={'expected':expected, 'actual':float(rank.iloc[0].weighted_momentum)}
cutoff=days[500]
before=rank_momentum(prices,cutoff,candidate_tickers=['A'],observed=prices.notna())
future=prices.copy(); future.loc[future.index>cutoff,'A']*=100
after=rank_momentum(future,cutoff,candidate_tickers=['A'],observed=prices.notna())
pd.testing.assert_frame_equal(before,after)
results['past_signal_future_invariance']='pass'

r=pd.DataFrame({'A':[.1,-.1],'B':[0,0]},index=pd.bdate_range('2020-01-01',periods=2))
w=pd.Series({'A':.5,'B':.5})
buy,drift=_holding_period(r,w,'buy_and_hold'); daily,_=_holding_period(r,w,'daily_rebalanced')
assert np.isclose((1+buy).prod(),.995)
assert np.isclose((1+daily).prod(),.9975)
assert np.isclose(drift.A,.495/.995)
results['accounting_manual']={'buy_hold':float((1+buy).prod()),'daily':float((1+daily).prod()),'end_weight_A':float(drift.A)}
assert np.isclose(_cap_weights(pd.Series({'A':.9,'B':.1}),.6).sum(),1)
assert np.isclose(_cap_weights(pd.Series({'A':1.}),.6).sum(),.6)
results['cap_redistribution']='pass'

curve=pd.DataFrame({'x':[1,1.1,.99,1.2]},index=pd.to_datetime(['2020-01-01','2020-06-01','2020-09-01','2021-01-01']))
met=summarize_performance(curve)
assert np.isclose(met['x_cagr'],1.2**(365.25/366)-1)
assert np.isclose(met['x_max_drawdown'],-.1)
results['metrics_manual']={'cagr':met['x_cagr'],'mdd':met['x_max_drawdown']}

cal=pd.to_datetime(['2020-01-02','2020-01-03'])
us=pd.Series([100.,110.,999.],index=pd.to_datetime(['2020-01-01','2020-01-02','2020-01-03']),name='US')
fx=pd.Series([1000.,1200.,9999.],index=us.index)
sr=sleeve_returns_on_calendar(us,cal,'previous_close',fx=fx)
assert np.isclose(sr.iloc[-1],.32)
results['fx_strict_previous_close']={'expected':.32,'actual':float(sr.iloc[-1])}
try:
 sleeve_returns_on_calendar(pd.Series([100,np.nan,110],index=pd.bdate_range('2020-01-01',periods=3),name='gap'),pd.bdate_range('2020-01-01',periods=3),'same_day')
except ValueError as e: results['same_day_raw_gap']=str(e)
else: raise AssertionError('gap not rejected')
try:
 previous_month_end(pd.to_datetime(['2026-09-15','2026-10-01']),pd.Timestamp('2026-10-09'),sessions=pd.to_datetime(['2026-09-15','2026-09-30']))
except ValueError as e:results['calendar_missing_monthend']=str(e)
else:raise AssertionError('missing month end accepted')

idx=pd.bdate_range('2020-01-02',periods=2)
zero=pd.DataFrame({'A':[0.,0.]},index=idx)
equity,hist=run_portfolio(zero,PortfolioConfig({'A':1},rebalance_cost_bps=100))
reported=summarize_performance((equity/equity.iloc[0]).to_frame('portfolio'))
results['portfolio_initial_cost']={'values':equity.tolist(),'cost':hist.cost.tolist(), 'reported_ending_value':reported['portfolio_ending_value'],'true_ending_value_per_initial_capital':float(equity.iloc[-1])}


from types import SimpleNamespace
from etf_momentum.data import proxy_observations, splice_proxies
from etf_momentum.backtest import StaleDataError
wfidx=pd.bdate_range('2020-01-01','2022-12-30')
wf=pd.DataFrame({'WIN':.002,'OTHER':.0001},index=wfidx)
_,windows=walk_forward(wf,train_years=1,metric='cagr')
wf2=wf.copy();wf2.loc['2022-01-01':,:]=-.4
_,win2=walk_forward(wf2,train_years=1,metric='cagr')
assert win2.selected.iloc[0]==windows.selected.iloc[0]
results['walkforward_future_selection_invariance']='pass'
wf.loc['2021-01-01':'2021-03-31','WIN']=np.nan
try:walk_forward(wf,train_years=1,metric='cagr')
except ValueError as e:results['F09_missing_test']=str(e)
else:raise AssertionError('missing test returns accepted')
assert equity.tolist()==[1.0,.99]
results['F07_first_cost']='pass'
localidx=pd.bdate_range('2018-01-01','2020-03-02')
local=pd.DataFrame({'LOCAL':np.nan},index=localidx);local.iloc[-1,0]=200.
usidx=pd.bdate_range('2017-12-29','2020-01-15')
proxy=pd.Series(100*np.exp(np.arange(len(usidx))*.001),index=usidx)
fx=pd.Series(1000.,index=usidx)
filled,starts=splice_proxies(local,{'LOCAL':proxy},fx)
seen=proxy_observations(proxy,fx,localidx[localidx<starts['LOCAL']])
observed=cli._observations(local,SimpleNamespace(proxy_observed={'LOCAL':seen}),filled)
row=rank_momentum(filled,pd.Timestamp('2020-02-28'),observed=observed).iloc[0]
assert not row.price_fresh and not row.eligible
results['F02_stopped_proxy']={k:str(row[k]) for k in ['last_observed','price_fresh','eligible']}
ix=pd.bdate_range('2024-01-01',periods=6)
r=pd.DataFrame({'A':[0,.1,.1,.1,.1,.1],'B':[np.nan,np.nan,0,0,np.nan,np.nan]},index=ix)
a,_=run_portfolio(r,PortfolioConfig({'A':1}))
b,_=run_portfolio(r,PortfolioConfig({'A':1,'B':0}))
pd.testing.assert_series_equal(a,b)
results['F06_zero_sleeve_library']='pass'
gap=prices.copy();gap.loc[days[400]:days[500],'CASH']=np.nan
cli._require_fresh(gap.notna(),{'benchmark':'BENCH','cash':'CASH'})
try:run_backtest(gap.ffill(),BacktestConfig(top_n=1,benchmark='BENCH',cash_ticker='CASH',candidate_tickers=('NOPE',)),observed=gap.notna())
except StaleDataError as e:results['F05_held_gap']=str(e)
else:raise AssertionError('held gap accepted')
u=OUT/'valid_universe.csv'
u.write_text('ticker,name,category,enabled\nA,Alpha,X,1\nBENCH,Bench,X,1\n')
calls=[]
def downloaded(tickers,**kw):
 calls.append(list(tickers));return prices[[t for t in tickers if t in prices]]
argv=['etf-momentum','rank','--universe',str(u),'--benchmark','BENCH','--cash','MISSING','--end','2026-10-07','--output-dir',str(OUT/'missing_cash')]
with patch.object(cli,'download_prices',downloaded),patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()):
 try:cli.main()
 except SystemExit as e:results['F03_missing_cash']={'requested':calls,'error':str(e)}
 else:raise AssertionError('missing cash accepted')
with patch.object(sys,'argv',['etf-momentum','rank','--weights','nan,0,1']),contextlib.redirect_stderr(io.StringIO()):
 try:cli.main()
 except SystemExit as e:assert e.code==2;results['F08_nan_weights_exit']=e.code
 else:raise AssertionError('NaN weights accepted')
try:cli._require_one_market(['069500.KS','SPY'])
except SystemExit as e:results['F01_F04_mixed_market']=str(e)
else:raise AssertionError('mixed market accepted')
custom=MomentumConfig(min_weighted_momentum=.5,min_trend_strength=.3)
row=rank_momentum(prices,config=custom,candidate_tickers=['A']).iloc[0]
reason=cli._exclusion_reason(row,custom)
assert '50.0%' in reason and '30.0%' in reason
results['F10_threshold_reason']=reason
(OUT/'normal_and_prior.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps(results,ensure_ascii=False,indent=2))
```

### 부록: cached_checks.py — 실제 캐시 CLI

```python
import contextlib
import importlib.metadata
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch
import pandas as pd
from etf_momentum import cli
from etf_momentum.backtest import summarize_performance

out=Path('/private/tmp/etf-review-20261011-recheck/cached');out.mkdir(exist_ok=True)
sources=[(p,pd.read_csv(p,index_col=0,parse_dates=True)) for p in sorted(Path('data').glob('prices_raw*.csv'))]
tv=[(p,pd.read_csv(p,index_col=0,parse_dates=True)) for p in sorted(Path('data').glob('traded_value*.csv'))]
used=[]
def read(tickers,start=None,end=None,raw=False,**kw):
 eligible=[(p,f) for p,f in sources if set(tickers)<=set(f.columns)]
 if not eligible:raise AssertionError(f'No local price input for {tickers}')
 p,f=min(eligible,key=lambda pf:len(pf[1].columns))
 used.append(str(p))
 f=f.loc[start:,[t for t in tickers]].copy()
 if end:f=f.loc[f.index<pd.Timestamp(end)]
 f=f.dropna(how='all')
 return f if raw else f.ffill()
def value(tickers,start=None,end=None,**kw):
 eligible=[(p,f) for p,f in tv if set(tickers)<=set(f.columns)]
 if not eligible:raise AssertionError(f'No local volume input for {tickers}')
 p,f=min(eligible,key=lambda pf:len(pf[1].columns));used.append(str(p))
 f=f.loc[start:,[t for t in tickers]].copy()
 if end:f=f.loc[f.index<pd.Timestamp(end)]
 return f
def run(name,*args):
 log=io.StringIO()
 argv=['etf-momentum',*args,'--output-dir',str(out/name),'--cache-dir',str(out/'unused_cache')]
 with patch.object(sys,'argv',argv),contextlib.redirect_stdout(log):cli.main()
 (out/f'{name}.log').write_text(log.getvalue())
 return out/name
res={}
with patch.object(cli,'download_prices',read),patch.object(cli,'download_traded_value',value):
 for account in ['pension','retirement']:
  s=f'config/strategies/{account}.json'
  rp=run(f'{account}_rank','rank','--strategy',s,'--lookback-years','5','--end','2026-10-07')
  wp=run(f'{account}_weekly','weekly','--strategy',s,'--lookback-years','5','--end','2026-10-07','--no-save-history')
  rr=pd.read_csv(rp/'latest_ranking.csv');wr=pd.read_csv(wp/'latest_ranking.csv')
  pd.testing.assert_frame_equal(rr,wr)
  selection=pd.read_csv(rp/'latest_selection.csv');selected=selection.loc[selection.status=='selected','ticker'].tolist()
  res[account]={'selection_date':str(selection.as_of.iloc[0]),'selected':selected,'ranking_weekly_equal':True,'executions':{}}
  for execution in ['same_close','next_close']:
   bp=run(f'{account}_{execution}','backtest','--strategy',s,'--start','2024-01-01','--end','2026-10-07','--transaction-cost-bps','10','--execution',execution)
   eq=pd.read_csv(bp/'equity_curve.csv',index_col=0,parse_dates=True);pos=pd.read_csv(bp/'positions.csv')
   held=pos.loc[pos.rebalance_date==selection.as_of.iloc[0],'tickers'].iloc[0].split(',')
   assert sorted(t for t in held if t!='153130.KS')==sorted(selected)
   assert (pos.hold_until.iloc[:-1].to_numpy()==pos.trade_date.iloc[1:].to_numpy()).all()
   main=eq.loc['2025-03-31':'2026-10-06'];met=summarize_performance(main/main.iloc[0])
   res[account]['executions'][execution]={'main_cagr':met['strategy_cagr'],'main_mdd':met['strategy_max_drawdown'],'full_rows':len(eq),'positions':len(pos),'monthend_selection_matches':True,'hold_until_matches_next_trade':True}
 # Exercise both grid commands through real cached data, with a bounded grid.
 common=['--universe','config/universe_us_sectors.csv','--benchmark','SPY','--cash','BIL','--start','2024-01-01','--end','2026-10-07','--top-n-grid','2,3','--weighting-grid','equal','--rank-by-grid','weighted_momentum','--rebalance-grid','ME','--relative-strength-grid','off']
 cp=run('us_compare','compare',*common,'--transaction-cost-bps','5')
 fp=run('us_walkforward','walkforward',*common,'--max-per-group-grid','1','--risk-on-grid','off','--train-years','1','--test-months','12')
 res['small_grid']={'comparison_rows':len(pd.read_csv(cp/'comparison.csv')),'wf_windows':len(pd.read_csv(fp/'walkforward_windows.csv')),'wf_grid_rows':len(pd.read_csv(fp/'walkforward_grid.csv'))}
 pp=run('portfolio','portfolio','--spec','config/portfolio_pension.json')
 res['portfolio']={'summary_rows':len(pd.read_csv(pp/'portfolio_summary.csv')),'curve_rows':len(pd.read_csv(pp/'portfolio_equity.csv'))}
res['source_files']=sorted(set(used))
res['versions']={k:importlib.metadata.version(k) for k in ['pandas','numpy','pytest','exchange-calendars','yfinance']}
(out/'results.json').write_text(json.dumps(res,indent=2,ensure_ascii=False))
print(json.dumps(res,indent=2,ensure_ascii=False))
```

### 부록: universe_and_research.py — 그룹/연구 수식

```python
import hashlib,json,runpy,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum.universe_builder import recent_returns,correlation_groups
from etf_momentum.backtest import summarize_performance
from etf_momentum import data
out=Path('/private/tmp/etf-review-20261011-recheck')
u=pd.read_csv('config/universe_kr_all.csv').fillna('')
m=json.loads(Path('config/universe_kr_all.meta.json').read_text())
p=Path('data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2025-04-08_latest.csv')
raw=pd.read_csv(p,index_col=0,parse_dates=True)
r=recent_returns(raw)
g=correlation_groups(r)
# Independently check all pairwise correlations in every complete-linkage group.
minimum=1.; pairs=0
for _,members in g.dropna().groupby(g.dropna()):
 c=r[list(members.index)].corr(min_periods=63).to_numpy(); z=c[np.triu_indices(len(c),1)]
 assert np.isfinite(z).all() and (z>=.9-1e-12).all()
 minimum=min(minimum,float(z.min()));pairs+=len(z)
# Membership partitions, not arbitrary numeric labels/name strings.
a={frozenset(x.index) for _,x in g.dropna().groupby(g.dropna())}
b={frozenset(x.ticker) for _,x in u[u.group!=''].groupby('group')}
assert a==b
assert len(u)==m['etfs'] and u.ticker.is_unique
assert (u.risk_asset.eq(0)==u.ticker.eq('153130.KS')).all()
assert (u.retirement_ok.eq(0)==u['name'].str.contains('합성|선물')).all()
res={'universe':{'etfs':len(u),'pension_allowed':int(u.pension_ok.sum()),'retirement_allowed':int(u.retirement_ok.sum()),'groups':len(a),'grouped_etfs':int(g.notna().sum()),'returns_rows':len(r),'returns_start':str(r.index[0].date()),'returns_end':str(r.index[-1].date()),'same_saved_partitions':True,'within_group_pairs':pairs,'minimum_within_group_corr':minimum}}
base=pd.Timestamp(pd.read_csv('results/walkforward/kr_pension_pension_10eok_10bp_robustgrid_tvoff/walkforward_windows.csv').train_end.iloc[0])
cashpath=Path('data/prices_raw_153130_KS_2010-01-01_2026-10-07.csv');cash=pd.read_csv(cashpath,index_col=0,parse_dates=True).iloc[:,0].pct_change()
res['saved_research']={}
files=[p,cashpath,Path('config/universe_kr_all.csv'),Path('config/universe_kr_all.meta.json')]
for label,run in [('60','kr_pension_pension_10eok_10bp_robustgrid_tvoff'),('788_group1','kr_all_pension_10eok_10bp_robustgrid_tvoff'),('788_group0','kr_all_pension_10eok_10bp_nogroup_tvoff')]:
 path=Path('results/walkforward')/run/'walkforward_equity.csv';files.append(path)
 curve=pd.read_csv(path,index_col=0,parse_dates=True).walk_forward.dropna()
 result={}
 for end in ['2025-02-28',None]:
  c=curve.loc[base:end];v=c.to_numpy();ret=c.pct_change().dropna();excess=ret-cash.reindex(ret.index).fillna(0)
  independent={'cagr':float((v[-1]/v[0])**(365.25/(c.index[-1]-c.index[0]).days)-1),'max_drawdown':float((v/np.maximum.accumulate(v)-1).min()),'sharpe':float(excess.mean()/excess.std(ddof=1)*np.sqrt(252))}
  actual=summarize_performance((c/c.iloc[0]).to_frame('x'),risk_free=cash)
  for k,val in independent.items():assert abs(actual['x_'+k]-val)<1e-10,(k,val,actual['x_'+k])
  result['main' if end else 'full']={'start':str(c.index[0].date()),'end':str(c.index[-1].date()),'rows':len(c),**independent}
 res['saved_research'][label]=result
# Exercise full table-generation script without network or production output writes.
def local(tickers,start=None,end=None,**kwargs):
 assert len(tickers)==1 and tickers[0] in ('153130.KS','BIL')
 f=pd.read_csv(f'data/prices_raw_{tickers[0].replace(".","_")}_2010-01-01_2026-10-07.csv',index_col=0,parse_dates=True)
 return f.loc[start:].loc[lambda x:x.index<pd.Timestamp(end)].ffill()
with patch.object(data,'download_prices',local),patch.object(sys,'argv',['research_tables','--output',str(out/'research_tables.md')]):runpy.run_path('scripts/research_tables.py',run_name='__main__')
res['sha256']={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
(out/'universe_and_research.json').write_text(json.dumps(res,indent=2,ensure_ascii=False))
print(json.dumps(res,indent=2,ensure_ascii=False))
```

### 부록: builder_cli.py — builder CLI

```python
import contextlib,importlib.util,io,json,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
out=Path('/private/tmp/etf-review-20261011-recheck')
spec=importlib.util.spec_from_file_location('builder','scripts/build_universe.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
items=[{'itemcode':t,'itemname':n,'etfTabCode':tab,'amonut':v} for t,n,tab,v in [('069500','벤치',1,10),('153130','현금',6,5),('000001','주식',2,3),('000002','합성',4,2),('000003','레버리지',4,8),('000004','채권',6,4),('000005','가격없음',2,'-')]]
idx=pd.bdate_range('2026-01-01',periods=100)
raw=pd.DataFrame({f'{t}.KS':100*np.exp(np.arange(len(idx))*.001+np.sin(np.arange(len(idx)))*.002) for t in ['069500','153130','000001','000002']},index=idx)
output=out/'builder_cli.csv'
args=['build_universe','--end','2026-10-07','--output',str(output),'--cache-dir',str(out/'unused_cache')]
with patch.object(module,'fetch_naver_list',return_value=items),patch.object(module,'download_prices',return_value=raw) as download,patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):module.main()
f=pd.read_csv(output);m=json.loads(output.with_suffix('.meta.json').read_text())
assert len(f)==4 and m['no_yahoo_prices']==['000005.KS'] and download.call_args.kwargs['raw'] is True
before=(output.read_bytes(),output.with_suffix('.meta.json').read_bytes())
try:
 with patch.object(module,'fetch_naver_list',return_value=items),patch.object(module,'download_prices',return_value=raw.drop(columns='153130.KS')),patch.object(sys,'argv',args):module.main()
except SystemExit:pass
else:raise AssertionError('missing cash accepted')
assert before==(output.read_bytes(),output.with_suffix('.meta.json').read_bytes())
(out/'builder_cli.json').write_text(json.dumps({'successful_etfs':len(f),'raw_download':True,'dropped':m['no_yahoo_prices'],'missing_cash_preserves_both_files':True},indent=2))
print((out/'builder_cli.json').read_text())
```
