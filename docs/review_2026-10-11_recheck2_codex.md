# 전체 코드 재검토 2·계산 및 경계 조건 재현 — 2026-10-11

## 확인된 문제와 판단

**현재 코드에서 새 P2 2건을 확인했다.** 앞선 C01~C03과 N01~N06은 기존 재현 조건에서 수정/해결 유지다. 전체 테스트 **327개 통과**와 별도로 아래 경계에서는 오류가 재현된다.

| ID | 등급 | 문제 | 기대·실제 |
|---|---|---|---|
| D01 | P2 | 무효 판정한 옛 Naver 보완 캐시가 다운로드 실패 시 fallback으로 반환 | 버전1의 잘못된 [90,81,90]을 거부/수리해야 하지만 그대로 반환 |
| D02 | P2 | 벤치마크의 마지막 관측이 늦으면 마지막 보유 수익이 0으로 남음 | 같은 평가일 A +10.11% 또는 −9.91%, 실제 전략 0%; 종료일은 그대로 출력 |

ETF 선정 도구라는 컨셉의 구현은 유지된다. 주요 모멘텀·수량 회계·환율·비용·성과식 검산이 일치하고, 정상 캐시의 월말 선택도 명령 간 같다. 그러나 **데이터 오류로 무효화한 캐시의 재사용**과 **평가 종료일의 수익 귀속**은 현재 결과의 정확성을 깨는 경계다. 먼저 D01/D02를 수정하고, 네이버 보완의 근사값 및 기준 달력 검증 한계를 연구 판단과 구분해야 한다.

이번 보고서는 제품 코드를 수정하지 않았다. 이전 문서·코드·설정·테스트·운영 기록·캐시·공식 결과를 보존하고 새 리뷰 문서만 작성했다.

## 기준과 확인 범위

- 시작·실행 HEAD: `03c9b882eb42a1e26057b3c8e44d77f5dde0f737`. 시작 작업 트리 clean, 검토 중 HEAD 변화 없음.
- 전체 검토 + 재검토. 이전 보고서의 실행 HEAD `107d2ec23169c1c4e1849664eff2aca91d1b299c`와 현재 HEAD의 두 스냅샷을 직접 비교했다. 조상/분기점 비교라고 가정하지 않았다. 시작 시 제품 미커밋 변경 없음.
- 이전 이후 변경: naver의 조정계수 보완/응답 검증·approximate/unfilled 기록, data의 보완 기록 version2 검증, CLI 현금 평가 날짜 선정, 해당 테스트/README/CLAUDE/연구 수치. 변경 밖의 전체 호출 경계도 대조했다.
- 환경: Python3.14.3, pandas3.0.3, NumPy2.4.6, pytest9.0.3, exchange-calendars4.13.2, yfinance1.3.0. 기존 .venv 사용, 의존성 설치 없음.
- 스킬: `.agents/skills/etf-momentum-code-review/SKILL.md`와 checklist/report-format. 스킬 지시에 따라 독립 리뷰어1명에게 세션 이력·예상 버그 없이 전체 범위를 전달했다. 추가 리뷰어 없음. D02는 독립 리뷰어가 먼저 확인하고 주 리뷰어가 직접 실행 및 1/3/5행·상승/하락으로 확장 검증했다. D01은 주 리뷰어 재현이다.
- 구현 확인: cli, strategy, data, naver, signals, backtest, walkforward, portfolio, records, weekly, reporting, universe_builder, 초기화 및 scripts/build_universe·research_tables. 설정/후보→다운로드/캐시/관측표→신호/선정→회계/성과→출력/기록을 확인했다.
- 문서/설정: README·CLAUDE·pyproject, 전략/조합 JSON, 유니버스/meta, 관련 설계·계획, 연구 요약·이전 리뷰, 테스트 및 새 회귀 조건. 적용되는 AGENTS.md는 작업 영역에서 발견하지 못했다.
- 현재 v3: 788개 목록, top4, 그룹 제한 없음, lookback63/126/252·skip21·가중치 .3/.3/.4, SMA200, vol63, 거래대금10억, KODEX200 필터, 프록시 사용. 연금 허용788/퇴직연금 표시728이며 후보에서 benchmark/cash는 별도 제외한다.
- 그룹 제한 없음, 매수 불가 종목은 사용자가 거르는 원칙, 합성/선물 이름에 따른 계좌 표시 근사, 증권사 목록 미대조는 사용자 결정으로 존중했다. 추가 주문·통합 배분 기능을 필수 개선사항으로 요구하지 않았다.
- 네트워크 요청 없이 합성 자료·로컬 캐시로 실행. 다운로드 reader/mock 외 선정·회계·출력은 실제 코드다. 모든 실험 파일은 `/private/tmp/etf-review-20261011-latest`에 분리했다. 운영 history에 append하지 않았다.
- clean 상태에서 실행한 CLI 실험을 공식 성과로 취급하지 않는다. 이번 문서 생성 전 실행 메타데이터와 입력 해시를 기록하며, 완료 후 새 보고서만 untracked다.

## 발견사항

### D01 — 옛 보완 버전의 캐시를 무효화한 뒤 fallback에서는 다시 사용한다

위치: `src/etf_momentum/data.py:235`~`:250`, `:297`~`:307`. 호출: rank/weekly/backtest 등의 download_prices → _cached_download → cache 판정 → Yahoo 재조회 실패 → stale fallback.

현재 _needs_naver_fill은 보완 기록이 version2가 아니면 캐시를 재다운로드 대상으로 판정한다. version1은 이전 C01의 원종가 비율 오류를 포함할 수 있어 단순히 오래된 유효 가격과 다르다. 하지만 complete=False 이유와 무관하게 stale_cache에 경로를 넣고, 다운로드가 비면 그 캐시를 그대로 반환한다. 버전 검증이 정상 재사용 분기에는 있지만 장애 fallback에는 없다.

최소 재현: tmp에 수정주가 `[90,81,90]`, `.naver_fill.json`의 version=1을 쓴다. 정상 값은 분배금이 반영된 `[90,90,90]`여야 한다. NAVER_FILL=True, _yf_download는 빈 DataFrame 반환, refresh 미지정으로 download_prices 호출.

| 항목 | 기대 | 실제 |
|---|---|---|
| 캐시 판정 | 버전1은 재다운로드 필요 | 재다운로드 시도 |
| 재조회 실패 | 알려진 잘못된 버전 거부 또는 보완 후 사용 | **[90,81,90] 그대로 반환** |
| 경고 | 정확성 실패를 실행 중단/별도 상태로 전달 | `Using stale prices cache ...; download returned no data`만 경고 |
| 기록 | 수리되지 않았음을 사용 경로가 구분 | version1 그대로, 반환 frame에는 상태 없음 |

기대 근거: NAVER_FILL_VERSION=2 주석/버전 무효화 코드와 이전 C01 수정 목적. age만 지난 최신 버전 캐시를 경고하며 쓰는 정책을 전부 금지하는 지적이 아니다. **계산 의미가 잘못된 구버전**을 최신성만 낮은 유효 캐시처럼 다루는 경계다. explicit end여도 발생하며 Yahoo 예외를 잡아 빈 raw로 만드는 경로도 같은 분기를 탄다.

영향: 네트워크 장애 동안 C01의 가짜 낙폭·반등이 다시 신호·SMA·변동성·보유 수익에 전달될 수 있다. 잘못된 값은 유한하고 notna이므로 일반 최신성 검사로 구별되지 않는다. 실제 운영 캐시가 현재 구버전이라고 주장하지 않으며, 이번 로컬 기록은 version2로 갱신되어 있다.

수정 방향: cache의 age 만료와 구조/버전/계산 의미 무효화를 분리한다. fallback도 현재 형식/보완 버전·필수 티커 조건을 충족하는 캐시만 허용한다. 구버전을 안전하게 복구할 수 없으면 명시적으로 중단한다. version1·sidecar 없음/손상·유효 v2의 age 만료·다운로드 빈 응답/예외·refresh를 회귀 검증한다.

### D02 — 마지막 벤치마크 관측 뒤의 실제 보유 수익을 0으로 출력한다

위치: `src/etf_momentum/backtest.py:94`~`:97`, `:112`, `:116`~`:126`, `:173`~`:181`. 호출: 기본 same_close backtest 및 이 일간 수익을 쓰는 compare/walkforward.

마지막 기간의 rebalance_date도 benchmark의 마지막 관측일로 당긴다. 실제 보유 수익은 그 날짜까지 계산하지만 strategy_returns는 전체 가격표를 0으로 초기화했고 equity는 전체 마지막 날짜까지 출력한다. 마지막 관측 뒤의 보유 가격이 정상이어도 계산되지 않은 꼬리 구간이 0% 수익처럼 표시된다.

재현: 2023-01-02~12-29 영업일, A=100×1.001^n, BENCH=100×1.0005^n, CASH=100, 짧은 readiness 설정. 마지막날 A 가격에1.1을 곱하고 BENCH만 NaN. observed=raw.notna(), sessions=입력 세션, same_close. 최종 BENCH 1행 결측은 5행 최신성 허용 안이며 _require_fresh도 통과한다.

| 항목 | 기대 | 실제 |
|---|---|---|
| 최종 평가 날짜 | 12/29이면 해당 보유 수익 반영, 또는 모든 평가를12/28로 종료 | **12/29 그대로 출력** |
| 마지막 hold_until | 평가 수익과 종료일의 의미가 일치 | **12/28** |
| A의12/29 수익 | **+10.11%** | strategy **0%** |
| A 마지막 가격에 .9를 곱한 경우 | **−9.91%** | strategy **0%** |

마지막 benchmark 결측 1/3/5행 × A 상승/하락을 모두 실행했다. 6가지 모두 최신성 검사 통과, 실제 마지막 수익0. 기간이 미완료일 수 있어 마지막 월말 검사를 생략하는 정책과, 출력한 날짜의 실제 보유 수익을 누락하는 동작은 다르다. benchmark의 미관측 비교수익 문제와도 별개로 A 수익 자체가 사라진다.

영향: 상승과 하락을 모두 누락하여 마지막 가치·CAGR·MDD·Sharpe와 WF 연결에 영향을 줄 수 있다. 하락 누락은 손실을 축소한다. 이번 정상 캐시의 마지막 평가일은 benchmark도 관측되어 이 조건을 주입하지 않았다. 특정 실제 결과가 이 오류로 바뀌었다고 주장하지 않는다.

수정 방향: 신호일·매매 종료 경계·평가 종료일을 분리한다. 마지막 평가일까지 기존 보유의 실제 수익을 계산하거나 공통 유효 날짜로 모든 곡선·지표·메타데이터를 동일하게 자른다. 마지막 관측1/5/6행 지연, 보유 가격만 정상, 상승/하락, 실제 휴장 행과 실제 세션 결측, same_close/next_close 모두 회귀 검증한다.

## 이전 지적의 현재 판정

| 항목 | 현재 실행 결과 | 판정 |
|---|---|---|
| C01 분배락1일 보완 | [90,NaN,90]→**[90,90,90]**, approximate 기록 | 원래 조건 해결 |
| C02 빈 기준 응답 | 경고·캐시 미생성, 정상 응답 재개 시 Yahoo1/Naver2회 재시도 후 보완 | 원래 조건 해결 |
| C03 평가 시작 전 cash 공백 | 실제 portfolio **2024-01-02 시작**, 평가 구간 cash 누락0, 정상 실행 | 해결 |
| N01 휴장 행 next_close | 12/30신호→1/2체결 | 해결 유지 |
| N02 월중 benchmark 장기 공백 | SPY 오류로 중단 | 해결 유지 |
| N02 실제 월말 누락 | sessions 전달시3/31오류, rank도오류 | CLI 해결 유지 |
| N03 proxy 거래대금 stale | 평균NaN, liquid=False/eligible=False | 해결 유지 |
| N04 정확히6행 readiness | 첫 신호/기준일1/31 | 해결 유지 |
| N05 보유 cash32행 공백 | .5배는오류, 1배는실행 | 해결 유지 |
| N06 두 번째 replace 실패 | 이전CSV/meta 보존; 성공CSV SHA-256 일치 | 예외 경로 해결 유지 |
| R01 FX stale | 나이5=True,6/10=False | 해결 유지 |
| R02 잘린 기록 | 15필드/기대16오류, 원본bytes불변 | 해결 유지 |
| R03 미사용USD슬리브 | 미존재 파일/FX를 요구하지 않고 동일곡선 | 해결 유지 |
| R04 중복ticker | 로더오류, 라이브러리 반복 후보는1행 | 해결 유지 |

초기 F01/F04 혼합 시장, F02 가격proxy중단, F03 필수cash누락, F05 보유공백, F06 0비중슬리브, F07 최초비용, F08 NaN비중, F09 선택test64행누락, F10 임계값설명도 normal_and_prior.py로 다시 실행했다. 각각 기존 기대 오류/불변/수치와 일치한다.

N02의 library는 sessions 미지정시 기존 관측일 기준을 유지한다고 docstring에 명시하고 CLI는 sessions를 전달한다. N06은 예외 rollback/해시를 확인한 것이며 crash 사이 두 파일의 단일 원자 교체를 보장한 것은 아니다. load_universe가 meta 해시를 자동 검사하지 않는 한계도 남는다.

## 테스트·계산·실제 입력 검산

### 전체 테스트

```bash
env MPLCONFIGDIR=/private/tmp/etf-review-20261011-latest/mpl \
    XDG_CACHE_HOME=/private/tmp/etf-review-20261011-latest/cache \
    .venv/bin/python -m pytest -q
```

**327 passed in 26.09s.** conftest는 Naver 보완을 기본으로 끄고, 관련 테스트가 True/fake fetch로 켠다. 기존 테스트는 정상 버전 전환과 빈 Naver 응답 재시도를 검사하지만 전환을 위한 재조회 실패 시 fallback, 마지막 벤치마크 관측 지연과 보유 수익 귀속은 충분히 다루지 않는다. 별도 재현은 기존 테스트 개수에 포함하지 않는다.

### 독립 계산 검산

| 항목 | 기대식/입력 | 현재 실제 |
|---|---|---|
| 기본 모멘텀 | 700행 A=100×1.002^n; Σw(P[t−21]/P[t−lookback−21]−1) | 기대=실제 .38792207964425396 |
| 과거 신호 | cutoff 이후 A×100 | 과거ranking frame동일 |
| 수량 유지 | A/B .5; A+10%후−10%, B0; .5×1.1×.9+.5 | .9950000000000001 |
| 일간 재조정 | (1+.5×.1)(1−.5×.1) | .9974999999999999 |
| 드리프트 | A종료 .495/총.995 | .49748743718592964 |
| 상한 재분배 | [.9,.1]상한.6합1; 후보하나합.6 | 일치 |
| CAGR | 366일1→1.2; 1.2^(365.25/366)−1 | .19955175204308273 |
| MDD | [1,1.1,.99,1.2]/cummax−1 | −.10000000000000009 |
| 이전US종가/FX | 100→110,1000→1200;1.1×1.2−1 | .32000000000000006 |
| 같은날미래값 | US999/FX9999같은날추가 | 이전날32%유지 |
| 최초비용 | 수익0,100bp | 곡선[1,.99],cost.01,ending.99 |
| WF미래불변 | 미래2022년수익−40%로변경 | 첫2021년선택동일 |

오차는 약 1e−12~1e−10이다. 독립 리뷰어도 다른 입력으로 모멘텀 .020276859063266796, buy-and-hold 1.105/daily 1.1025, previous_close, 0.5배 노출과 현금 이자의 가치 [1,1.055,1.11505]를 검산했다.

### 실제 캐시 CLI

end=2026-10-07은 배제하므로 마지막 평가일은 10/6이다. rank/weekly는 lookback 5년, 로컬 788개 raw/거래대금은 2021-10-06 시작 latest 파일, proxy/FX는 고정 2010 시작 파일을 사용했다. backtest는 start=2024-01-01, 비용 10bp, same_close/next_close로 실행했다. 가격 reader가 현재 디스크 파일을 읽어 기간을 잘랐으며 Yahoo/Naver 실제 호출은 하지 않았다.

워밍업 후 곡선은 2025-02-28~2026-10-06의 **390행/포지션 20개**다. 두 계좌의 9/30 선택은 `442580.KS,367760.KS,395270.KS,363580.KS`다. rank/weekly ranking 전체가 같고, 같은 월말 backtest의 현금을 제외한 보유 집합도 같았다. hold_until은 다음 trade_date와 일치한다. 정상 입력의 이 일치 검사는 D02의 벤치마크 마지막 관측 지연 조건을 검증한 것이 아니다.

아래는 곡선을 2025-03-31~2026-10-06으로 잘라 기준 1로 만든 리뷰 수치다. 공식 2016년 시작 연구와 구분한다.

| 계좌 | 체결 | CAGR | MDD |
|---|---|---|---|
| pension | same_close | 49.0214% | −55.6490% |
| pension | next_close | 54.4793% | −52.7607% |
| retirement | same_close | 44.4943% | −40.4019% |
| retirement | next_close | 44.0774% | −37.8349% |

미국 compare는 top_n 2/3으로 2행, WF는 train 1년/test 12개월로 표본외 1개 창/설정 2개를 실행했다. 기본 portfolio spec은 summary 18행/곡선 4123행이다. 모든 출력은 tmp다. 788개 긴 캐시는 보완 기록이 없는 옛 입력으로, downloader라면 재조회 대상이다. reader가 그 재조회를 우회했으므로 새 v2 보완을 전체 ETF에 적용한 성과라고 해석하지 않는다.

### 유니버스·연구 수치

그룹 생성 당시 788개 2025-04-08 latest 입력의 최근 252행(2025-09-23~2026-10-08)으로 complete-linkage를 다시 계산했다. **96그룹/485ETF**의 저장 분할과 같았고, **5437쌍**의 최저 상관은 **.9000376584548322**다. 현재 v2 보완을 모든 ETF에 다시 적용한 그룹 검산이 아니라 저장 당시 입력의 일치 확인이다. 티커 중복 없음, cash만 risk_asset=0, retirement 허용 728개 표시도 일치한다. builder 합성 CLI는 4개/미가격 1개/raw=True/필수 현금 누락 시 파일 보존/성공 해시 일치를 확인했다.

research_tables를 현금 CSV reader로 전체 실행하고 tmp에 출력했다. 전체 ETF 표는 현재 연구 문서의 표시값과 일치했다. 세 저장 곡선의 직접 CAGR/MDD/Sharpe는 구현과 1e−10 이내로 일치한다. 식은 실제 경과일 CAGR, 누적 최대 대비 MDD, 현금 초과 수익 mean/std(ddof=1)×√252이며 미대응 현금 수익은 기존 구현과 동일하게 0으로 처리했다.

| 저장곡선 | 2016-02-29~2025-02-28 CAGR/MDD/Sharpe | ~2026-10-06 CAGR/MDD |
|---|---|---|
| 기존60개 | 11.552666% / -16.355688% / 0.755811 | 17.544777% / -43.314132% |
| 788개그룹당1개 | 9.106227% / -18.140907% / 0.571435 | 11.789637% / -53.379120% |
| 788개그룹제한없음 | 9.329952% / -36.038515% / 0.534629 | 13.820660% / -54.561698% |

세 곡선은 주 기준 2211행/전체 2600행으로 일치한다. 저장 WF는 학습에서 top3/4 등을 선택하며 고정 v3 top4 JSON과 같은 전략 계산으로 혼동하지 않는다. 전체 원본 WF 그리드를 다시 생성한 것은 아니다. 수식 일치가 기업행사/원자료의 진실성을 검증하는 것도 아니다.

## 설계 한계·추가 경계 관찰·미검증

- **여러 날 gap의 approximate:** 현재 after 조정계수를 빈 구간 전체에 쓴다는 정책은 README에 명시됐다. 기업행사가 gap 중간에 있으면 행사 전날에도 after 계수를 적용할 수 있다. 한 날 분배락 재현은 해결됐지만 모든 경우의 정확한 분배일 복원은 아니다. approximate 기록이 있어도 신호/회계는 notna 자료로 쓴다. 문서화된 근사 정책을 이번 새 버그로 재분류하지 않았으며, 정확한 자료/기업행사 정보 또는 근사 날짜의 평가 정책이 추가 검증 대상이다.
- **기준 달력의 공유 공백:** Yahoo와 Naver 기준 응답이 같은 실제 세션을 빼면 현재 Yahoo 날짜 포함 검사가 통과한다. Jan2/Jan4만 양쪽에 있는 합성 입력은 Jan3 없이 성공했다. 이 검사는 모든 KRX 세션의 완전성을 증명하지 않는다. 현재 명세가 Naver 기준일을 달력으로 삼고 Yahoo 날짜 포함 여부를 검사한다고 명시하므로, 이 관찰을 D01/D02와 같은 확정 구현 버그로 분류하지 않았다. 독립 거래소 달력과의 대조를 원한다면 그 요구와 예외 휴장 정책을 명시해야 한다.
- 현재 로컬 Naver 기록은 version2이며 독립 리뷰어가 읽은 5개 기록은 unfilled=0이었다. 60ETF는 filled=1886/approx=55, 34ETF는 filled=1200/approx=38이다. 기록을 읽은 것이며 각 근사값·분배일을 외부 대조하지 않았다.
- 실제 Yahoo/Naver 장애, 시세·기업행사·조정계수, 증권사별 매수 가능 여부는 새로 조회하지 않았다. D01의 구버전 장애와 D02의 마지막 수익은 합성 자료로 확정했지만 실제 운영 발생 횟수/성과 차이는 미확인이다.
- 788개 전체의 2010년 시작 raw 캐시가 없어 긴 전체 그리드/WF를 재실행하지 않았다. 제한 구간 CLI·저장된 긴 곡선의 집계·문서 일치까지 검증했다.
- 현재 상장 ETF 생존 편향, 현재 상관 그룹의 과거 적용, WF 전환 비용 미반영은 연구 한계다. 사용자 정책인 그룹 제한 없음도 버그로 분류하지 않는다.
- 전체 모듈/호출 경계와 테스트·대표 수식/경계 재현을 확인했지만 체크리스트의 모든 조합을 독립 실행한 것은 아니다.

## 재현과 증거

부록을 표시 파일명으로 `/private/tmp/etf-review-20261011-latest`에 저장하고 리포지터리 루트에서 실행한다. 제품 파일은 변경하지 않고 tmp에만 쓴다. fix_and_cache.py/tail.py는 현재 오류의 재현을 assert하므로 실행 성공은 수정 완료를 뜻하지 않는다.

```bash
mkdir -p /private/tmp/etf-review-20261011-latest
# SCRIPT를아래부록파일명으로바꿔각각실행
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-review-20261011-latest/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261011-latest/cache .venv/bin/python /private/tmp/etf-review-20261011-latest/SCRIPT
```

### 입력 SHA-256

| 파일 | SHA-256 |
|---|---|
| `config/portfolio_pension.json` | `a5da6a8368ff533e5914466ed742f3d550267ef971813100b4f1201b22b2f13f` |
| `config/strategies/pension.json` | `f5bbf6f22f20f87596c0aab6d045a301ebc4b9f4b59b465494986627a438f95b` |
| `config/strategies/retirement.json` | `98a3cd96a425fafad92b33f022fa0e2c1a14c6d1b0e066a4460ca09f0b7604f5` |
| `config/universe_kr_all.csv` | `ee4047581bffc7095301436cef5bc5d6e88ac81de31165fa5f74b813fdc21f98` |
| `config/universe_kr_all.meta.json` | `98520bf072641611cc44fff36e09f266fc71963e4313a3e09b367e7e36e1c4fa` |
| `data/prices_raw_069500_KS_153130_KS_KRW=X_SPY_2010-01-01_2026-10-07.csv` | `cc4cfa798871e05780fe636924e5e732120820ff472b0bf02703c14fa504ce79` |
| `data/prices_raw_069500_KS_153130_KS_KRW=X_SPY_2010-01-01_2026-10-07.csv.naver_fill.json` | `5f741124efcb4c3697c2517e7a52703ec602e164e21312f730033c602310ad4a` |
| `data/prices_raw_069500_KS_2010-01-01_2026-10-07.csv` | `e3aeaa23a776b75d4e378e3e6a935316b61b8eaf26b7c3dac3b390ccdc33c8cd` |
| `data/prices_raw_069500_KS_2010-01-01_2026-10-07.csv.naver_fill.json` | `e00e0ea2d2a45f1c4d0b343560fb03f6a06ee3dd32b468baa9411a98e25496e0` |
| `data/prices_raw_153130_KS_2010-01-01_2026-10-07.csv` | `47fa8e959e11c7610b038c774f61e8ab41455a281fded61342cdca6c934aaf2e` |
| `data/prices_raw_153130_KS_2010-01-01_2026-10-07.csv.naver_fill.json` | `33b3cac09b65bc4a6484d479b2ad82431932fcb22b51c67ff5b918d098799b68` |
| `data/prices_raw_34_tickers_e1eb35400758fc2b_2010-01-01_2026-10-07.csv.naver_fill.json` | `3972efd7dd52bd74d679da7311124aa1b7f348cea1891e5958c7a7d1d7fdc758` |
| `data/prices_raw_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv.naver_fill.json` | `93f4e9c89fd47f0d2cb081c48df1906888938bd24532f66853d74e0e01b88c5b` |
| `data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2021-10-06_latest.csv` | `0d71d40818da9d68ece57597d48f1430bfade237f0d1e6d8f40a29e63bcba978` |
| `data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2025-04-08_latest.csv` | `e378d31a93f6c70bca3e12128ac6beb33a33cbab151881df71e9a74fb8924d8e` |
| `data/prices_raw_BIL_CIBR_DIA_GLD_IBB_ICLN_IGV_ITA_IWM_IYT_KRE_QQQ_SMH_SOXX_SPY_TAN_URA_VNQ_XBI_XLB_XLC_XLE_XLF_XLI_XLK_XLP_XLRE_XLU_XLV_XLY_XME_XRT_2010-01-01_2026-10-07.csv` | `4131f72107ab3c9f3fe3fc26f7ad4d85da497f82f17ba5b17ae4e1d5a558dd2f` |
| `data/prices_raw_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv` | `7aca8716bd62d88f2092b78f11ba7cdbcfc7d67b341f564e895142e3f86453c3` |
| `data/prices_raw_KRW=X_2010-01-01_2026-10-07.csv` | `fe32ed2511a9ad520bd920f723e9c9c1c4136b99264a8d71d002f70c625fab8a` |
| `data/prices_raw_SPY_2010-01-01_2026-10-07.csv` | `a9404c87bc09eaec42cb89140ee0c7be6883fb55ece9821372d402ae0387f83b` |
| `data/traded_value_788_tickers_1d20b3b9c5fefa8f_2021-10-06_latest.csv` | `3b1c2cd79b7b6a39f5de72d00ab49ee6d9db90df5450296c1733ea471f4c8c75` |
| `data/traded_value_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv` | `f7a1fb2eddc6c9d6ba9516b96410fe89745b9082d0fde74f924ce2d089d2f596` |
| `results/walkforward/kr_all_pension_10eok_10bp_nogroup_tvoff/walkforward_equity.csv` | `a93fab642675eb3055e9d69cec569e6102b46257defe76dbeb273a092fac01f8` |
| `results/walkforward/kr_all_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv` | `41d7f8f381c526ae047400bdc9597f3c88be2e9316075fdc3da23e8d6c0c3517` |
| `results/walkforward/kr_pension_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv` | `cfa5191430d2e2886deddb63ddc833691778d6530be2c9f05c05b8392a7b4753` |

### 부록: fix_and_cache.py — C01~C03수정/D01/공유달력경계

```python
import contextlib,io,json,sys,warnings
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum import cli,data,naver
out=Path('/private/tmp/etf-review-20261011-latest');res={}
days=pd.bdate_range('2024-01-02',periods=3)
yahoo=pd.DataFrame({'A.KS':[90.,np.nan,90.]},index=days)
raw=pd.Series([100.,90.,90.],index=days)
def good(ticker,start,end):return pd.Series(100.,index=days) if ticker==naver.REFERENCE else raw
f,report=naver.fill_gaps(yahoo,fetch=good)
assert f['A.KS'].tolist()==[90.,90.,90.]
res['C01_distribution']={'actual':f['A.KS'].tolist(),'report':report}
# C02 original: empty reference must not write a cache, next normal response retries.
cache=out/'empty_cache';cache.mkdir(exist_ok=True)
multi=yahoo.copy();multi.columns=pd.MultiIndex.from_tuples([('Close','A.KS')])
with patch.object(data,'NAVER_FILL',True),patch.object(data,'_yf_download',return_value=multi),patch.object(naver,'naver_closes',return_value=pd.Series(dtype=float,index=pd.DatetimeIndex([]))),warnings.catch_warnings(record=True) as ws:
 warnings.simplefilter('always');first=data.download_prices(['A.KS'],'2024-01-02','2024-01-05',cache_dir=cache,raw=True,refresh=True)
assert not list(cache.glob('prices_raw_*'))
with patch.object(data,'NAVER_FILL',True),patch.object(data,'_yf_download',return_value=multi) as yd,patch.object(naver,'naver_closes',side_effect=good) as nf:
 second=data.download_prices(['A.KS'],'2024-01-02','2024-01-05',cache_dir=cache,raw=True)
assert second['A.KS'].tolist()==[90.,90.,90.]
res['C02_empty']={'warning':str(ws[0].message),'first_cached':False,'retry_yahoo_calls':yd.call_count,'retry_naver_calls':nf.call_count,'second':second['A.KS'].tolist()}
# C03 original: a cash gap before the actual common period must not block.
cal=pd.bdate_range('2023-01-02','2024-04-30')
p=pd.DataFrame({'CAL':100.,'CASH':100*1.0001**np.arange(len(cal))},index=cal);p.loc['2023-02-01':'2023-03-15','CASH']=np.nan
sleeve=pd.DataFrame({'strategy':100*1.001**np.arange(len(cal[cal>='2024-01-02']))},index=cal[cal>='2024-01-02']);sleeve.to_csv(out/'late_sleeve.csv')
spec={'base_currency':'KRW','calendar_ticker':'CAL','cash_ticker':'CASH','start':'2023-01-02','end':'2024-05-01','sleeves':{'late':{'equity_csv':str(out/'late_sleeve.csv'),'currency':'KRW'}},'scenarios':[{'name':'late','weights':{'late':1.},'leverage':.5}]};(out/'late_spec.json').write_text(json.dumps(spec))
def local(tickers,raw=False,**kw):
 f=p[tickers].dropna(how='all');return f if raw else f.ffill()
argv=['etf-momentum','portfolio','--spec',str(out/'late_spec.json'),'--output-dir',str(out/'late_output')]
with patch.object(cli,'download_prices',local),patch.object(sys,'argv',argv),contextlib.redirect_stdout(io.StringIO()):cli.main()
eq=pd.read_csv(out/'late_output/portfolio_equity.csv',index_col=0,parse_dates=True)
assert eq.index[0]==pd.Timestamp('2024-01-02')
res['C03_before_common']={'first_date':str(eq.index[0].date()),'cash_missing_in_curve':int(p.CASH.reindex(eq.index).isna().sum())}
# Adjacent legacy cache boundary: invalidate v1, then empty provider response.
legacy=out/'legacy_cache';legacy.mkdir(exist_ok=True)
path=data._cache_path(['A.KS'],'2024-01-02','2024-01-05',legacy,prefix=data.PRICE_CACHE_PREFIX)
old=pd.DataFrame({'A.KS':[90.,81.,90.]},index=days);old.to_csv(path)
data._fill_record_path(path).write_text(json.dumps({'version':1,'filled':{'A.KS':['2024-01-03']}}))
with patch.object(data,'NAVER_FILL',True),patch.object(data,'_yf_download',return_value=pd.DataFrame()),patch.object(naver,'naver_closes',side_effect=AssertionError('unexpected Naver call')),warnings.catch_warnings(record=True) as ws:
 warnings.simplefilter('always');actual=data.download_prices(['A.KS'],'2024-01-02','2024-01-05',cache_dir=legacy,raw=True)
assert actual['A.KS'].tolist()==[90.,81.,90.]
res['legacy_fallback']={'expected':'stop or repair; do not return known v1 invalid adjustment','actual':actual['A.KS'].tolist(),'warnings':[str(w.message) for w in ws],'sidecar_version':json.loads(data._fill_record_path(path).read_text())['version']}
# Jointly missing real session in Yahoo and Naver reference escapes containment check.
missing=days[1];truncated=pd.DataFrame({'A.KS':[90.,90.]},index=days[[0,2]])
def cutoff(ticker,start,end):return pd.Series(100.,index=days[[0,2]])
f,report=naver.fill_gaps(truncated,fetch=cutoff)
assert missing not in f.index
res['joint_calendar_gap']={'expected_missing_session':str(missing.date()),'returned_dates':[str(x.date()) for x in f.index],'report':report,'run':'success'}
(out/'fix_and_cache.json').write_text(json.dumps(res,ensure_ascii=False,indent=2));print(json.dumps(res,ensure_ascii=False,indent=2))
```

### 부록: tail.py — D02독립수익검산

```python
import json
from pathlib import Path
import numpy as np
import pandas as pd
from etf_momentum import cli
from etf_momentum.backtest import BacktestConfig,run_backtest
from etf_momentum.signals import MomentumConfig
out=Path('/private/tmp/etf-review-20261011-latest');res=[]
index=pd.bdate_range('2023-01-02','2023-12-29')
mc=MomentumConfig(lookbacks=(21,42,63),skip_days=0,sma_window=50,volatility_window=20)
for lag in [1,3,5]:
 for jump in [1.1,.9]:
  prices=pd.DataFrame({'A':100*1.001**np.arange(len(index)),'BENCH':100*1.0005**np.arange(len(index)),'CASH':100.},index=index)
  prices.loc[index[-1],'A']*=jump;prices.loc[index[-lag:],'BENCH']=np.nan
  cli._require_fresh(prices.notna(),{'benchmark':'BENCH','cash':'CASH'})
  config=BacktestConfig(top_n=1,benchmark='BENCH',cash_ticker='CASH',candidate_tickers=('A',),momentum=mc)
  equity,positions,_=run_backtest(prices,config,observed=prices.notna(),sessions=index)
  expected=float(prices.A.pct_change().iloc[-1]);actual=float(equity.strategy.pct_change().iloc[-1])
  assert actual==0. and not np.isclose(expected,0.)
  res.append({'lag_rows':lag,'jump':jump,'expected_last_return':expected,'actual_last_return':actual,'hold_until':str(positions.hold_until.iloc[-1].date()),'reported_end':str(equity.index[-1].date()),'latest_guard':'passed'})
(out/'tail.json').write_text(json.dumps(res,indent=2));print(json.dumps(res,indent=2))
```

### 부록: replay.py — N/R재검토

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

OUT=Path('/private/tmp/etf-review-20261011-latest')
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

### 부록: new_boundaries.py — 월말/저장rollback

```python
from pathlib import Path
from unittest.mock import patch
import os, json
import numpy as np
import pandas as pd
from etf_momentum.universe_builder import write_universe
from etf_momentum.backtest import BacktestConfig, run_backtest, previous_month_end
from etf_momentum.signals import MomentumConfig
out=Path('/private/tmp/etf-review-20261011-latest');out.mkdir(exist_ok=True)
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

### 부록: normal_and_prior.py — 수식/이전방어

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

OUT = Path('/private/tmp/etf-review-20261011-latest')
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

### 부록: cached_checks.py — 실제캐시CLI

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

out=Path('/private/tmp/etf-review-20261011-latest/cached');out.mkdir(exist_ok=True)
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

### 부록: universe_and_research.py — 그룹/연구수식

```python
import hashlib,json,runpy,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum.universe_builder import recent_returns,correlation_groups
from etf_momentum.backtest import summarize_performance
from etf_momentum import data
out=Path('/private/tmp/etf-review-20261011-latest')
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
out=Path('/private/tmp/etf-review-20261011-latest')
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
