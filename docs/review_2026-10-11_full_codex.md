# 전체 코드 리뷰·계산 및 경계 조건 재현 — 2026-10-11

## 확인된 문제와 판단

**현재 HEAD에서 중요도 P2 문제 6건을 확인했다.** 10월 10일의 N01~N05는 현재 코드로 다시 실행해 모두 잔존함을 확인했다. 새 전체 ETF 유니버스 생성기의 저장 실패 문제를 N06으로 추가했다. N02에는 월말 결측을 이전 날짜로 당기는 추가 재현도 포함한다. 서로 다른 증상 7개를 원인·개선 경로 기준 6개 항목으로 정리했다.

| ID | 등급 | 문제 | 확인 결과 |
|---|---|---|---|
| N01 | P2 | next_close가 다음 거래일 대신 합친 가격표의 다음 행에서 체결 | 2025-12-30 신호 → 휴장일 12-31 체결; 기대 2026-01-02 |
| N02 | P2 | 과거 벤치마크 결측을 채우고 월말 결측 신호를 이전 관측일로 당김 | 월중 11행 0% 기준수익; 3월 31일 결측은 3월 30일 신호로 정상 실행 |
| N03 | P2 | 오래된 프록시 거래대금이 유동성 조건을 통과 | 마지막 관측 4월 28일인데 6월 20일 평균 26억, eligible=True |
| N04 | P2 | 워밍업의 행 수와 위치를 혼동 | 6행이 충분한 1월 31일을 건너뛰고 첫 신호 2월 28일 |
| N05 | P2 | portfolio 현금 ETF 내부 결측을 무제한 채움 | 현금 잔액 50%, 원자료 32거래일 결측을 0% 현금수익으로 처리 |
| N06 | P2 | CSV·메타데이터 교체 중 실패하면 서로 다른 버전이 남음 | 두 번째 replace 실패 → 새 CSV + 이전 meta |

핵심 모멘텀 식, 보유 수량 회계, 비용, 환산 수익률, 성과 집계는 독립 검산과 일치한다. 현재 788개 유니버스 v3 전략도 정상 캐시에서 월말 선택의 명령 간 일관성을 유지했다. **ETF 선정 도구라는 컨셉은 구현되어 있으나, 원자료 결측·체결 달력·저장 실패 경계까지 올바르다고 승인할 상태는 아니다.** 선정에 직접 영향을 주는 N03, 체결·성과의 N01/N02/N05, 유니버스 갱신의 N06을 먼저 보완하고 N04를 함께 고치는 것이 좋다.

등급은 지원되는 경계 입력의 중요 오류라는 기준으로 P2를 적용했다. 독립 리뷰어는 N01/N02/N03에 P1 의견을 제시했으나, 실제 정상 캐시에서 해당 원인으로 기본 선정이 잘못됐다고 확인한 것은 아니므로 주 리뷰에서 P2로 정리했다. 숫자가 단순히 나쁜 성과라는 이유로 버그로 분류하지 않았다.

## 검토 기준·범위

- 검토일: 2026-10-11, 전체 리뷰 + 과거 지적 재검토. 시작·실행 HEAD: `b1917cf5750ce20056a09e50a2bc471bcdb075a7`.
- 시작 작업 트리: `?? docs/review_2026-10-10_full_codex.md`만 존재. 이 문서는 보존했다. 제품 코드·전략·유니버스·기존 테스트·가격 캐시·운영 기록·공식 결과를 변경하거나 커밋하지 않았다. 이번 보고서만 추가했다.
- 환경: Python 3.14.3, pandas 3.0.3, numpy 2.4.6, pytest 9.0.3, exchange-calendars 4.13.2, yfinance 1.3.0. 기존 `.venv` 사용, 설치 없음.
- 적용 스킬: `.agents/skills/etf-momentum-code-review/SKILL.md`, 두 references. 스킬의 요구에 따라 예상 버그나 세션 이력을 전달하지 않은 독립 리뷰어 1명을 사용했다. 독립 지적은 주 리뷰어가 직접 실행하고 중복을 통합했다.
- 읽은 구현: `cli.py`, `strategy.py`, `data.py`, `signals.py`, `backtest.py`, `walkforward.py`, `portfolio.py`, `records.py`, `weekly.py`, `reporting.py`, `universe_builder.py`, 패키지 초기화, 두 scripts. CLI → 설정/후보 → 다운로드/관측표 → 선정 → 회계 → 기록 경로를 추적했다.
- 읽은 자료: README, CLAUDE, pyproject, 전략·조합 JSON, 유니버스와 메타데이터, 테스트 전반, 관련 raw/calendar·strategy/records·전체 ETF 설계/계획, 연구 요약, 과거 리뷰. 적용되는 별도 AGENTS.md는 작업 영역에서 발견하지 못했다.
- 10월 10일 검토 이후 제품 변경은 전체 유니버스 생성 모듈/스크립트/테스트, 연구 표 확장, 788개 CSV/meta, 두 전략 v3, 관련 문서다. 기존 핵심 계산 모듈은 같지만 잔존 결론은 현재 리비전에서 재실행한 결과에 근거한다.
- 현재 v3: lookback 63/126/252, skip 21, 가중치 0.3/0.3/0.4, SMA 200, 변동성 창 63, weighted_momentum, 거래대금 10억, top 4, KODEX 200 시장 필터, 프록시 사용. `max_per_group=0`은 사용자 결정이며 의도된 규칙이다.
- 연금저축 후보 788개, 퇴직연금 허용 표시 728개. 벤치마크/현금은 선정 후보에서 별도 제외된다. 합성·선물 이름 제외와 증권사 매수 가능 여부는 문서에 정한 근사/사용자 판단을 따른다. 추가 배분·주문 기능이나 증권사별 목록 대조를 필수 보완으로 요구하지 않는다.
- 네트워크 요청 없이 합성 입력과 기존 로컬 CSV를 사용했다. CLI의 다운로드만 reader/mock으로 대체하고 선정·회계·출력은 실제 코드로 실행했다. 실험 출력은 `/private/tmp/etf-review-20261011`에 분리했다. dirty=true 리뷰 실험으로, 공식 연구 결과 생성과 구분한다.

## 발견사항 상세

### N01 — next_close의 체결 달력이 신호 달력과 다르다

위치: `src/etf_momentum/backtest.py:78`, `:108`~`:115`. 호출: backtest/compare/walkforward → run_backtest.

월말 신호는 벤치마크 관측일로 보정하지만 체결과 다음 보유 종료는 전체 인덱스 위치 +1로 구한다. 같은 시장의 가격표라도 한 ETF에만 휴장일 행이 있으면 그 행에서 체결한다. 다음 거래일 종가라는 README/CLI 의미와 다르다.

재현: 2025-01-02~2026-02-27 영업일 가격표에서 2026-01-01 제거, 2025-12-31 A만 관측되고 BENCH/CASH는 NaN. `execution='next_close'`, observed=raw.notna(). 신호 12-30, 실제 체결 **12-31**, 기대 **1-2**. 설치된 XKRX 달력에서도 12-31은 비세션이고 1-2는 세션임을 확인했다. 새 거래소 외부 자료와 대조한 것은 아니다.

영향: 체결 비용 및 새/이전 보유의 수익 귀속 시점이 달라진다. 이번 정상 캐시의 두 계좌 next_close 31개 체결에서는 XKRX 비세션 체결이 발견되지 않았다. 합성 경계의 오류와 실제 입력의 발생 여부를 구분한다.

수정 방향: 신호·체결·최초 수익일·다음 체결 경계를 같은 시장 세션에서 계산한다. 실제 거래일의 벤치마크 결측을 휴장으로 취급해 삭제하지 않도록 N02와 함께 검증한다. 회귀 조건은 신호 직후 휴장 행, 다음 신호 직후 휴장 행, 끝부분 휴장 행, 실제 세션의 벤치마크 결측이다.

### N02 — 벤치마크의 과거 결측과 월말 결측이 정상 자료처럼 처리된다

위치: `src/etf_momentum/backtest.py:71`, `:76`, `:82`~`:89`, `:161`; `src/etf_momentum/cli.py:1437`의 _require_fresh, `:1500`의 _benchmark_baseline.

최신성 검사는 마지막 입력 날짜를 검사한다. 계산용 ffill → pct_change 이후의 유한한 0은 원자료 공백 검사를 대신하지 못한다. 보유 자료 검사는 실제 보유 티커에 적용되어 미보유 벤치마크의 월중 장기 공백은 누락될 수 있다.

재현 A: 2023-01-02~05-31, A/BIL 정상, SPY만 3월 6~20일 NaN. 최신성 검사 통과, risk_on_benchmark=True 백테스트 성공, **11행 기준수익이 모두 0%**, _benchmark_baseline도 통과. 기대는 원자료 장기 공백을 오류 또는 비교 불가로 처리하는 것이다. 변동성·Sharpe·낙폭 경로에 영향을 주며, 시작/종료 값이 같으면 CAGR은 같을 수 있다.

재현 B: 3월 31일 BENCH만 NaN이고 같은 평가 세션의 A/CASH는 정상. 백테스트는 **3월 30일을 월말 신호로 선정**하지만 동일 자료의 `previous_month_end(..., sessions=...)`는 `2023-03 last trading day 2023-03-31 is missing from the data` 오류다. 기대는 실제 월말 데이터가 없다는 오류이며, 이전 날 정보로 월말 선택을 대체하는 동작은 rank와 불일치한다. 휴장 행 제거와 실제 세션 누락은 다르다.

수정 방향: 원관측표와 시장 세션을 함께 유지하고 필요한 비교 기간/신호 앵커의 결측을 검사한다. 월말 세션을 먼저 정하고 그날 관측 유효성을 확인한다. 채운 수익 배열의 NaN 검사만 추가해서는 해결되지 않는다. 회귀 조건은 월중 장기 공백, 실제 월말 1일 공백, 전 달 전체 공백, 진짜 휴장 행이다.

### N03 — 프록시 거래대금의 원관측 최신성이 소실된다

위치: `src/etf_momentum/cli.py:1334`~`:1337`, `src/etf_momentum/data.py:101`, `:131`, `src/etf_momentum/signals.py:224`~`:239`. 호출: _apply_proxies → splice_proxies(scale=False) → rank_momentum.

가격/FX 관측표는 보존하지만 거래대금은 무제한 as-of 값으로 연결한다. 평균 거래대금 함수는 채운 숫자를 실제 관측으로 보고 마지막 결측이 없다고 판단한다. README의 실제 거래대금이 5영업일 넘게 오래되면 유동성 미달이라는 규칙과 충돌한다.

재현: 국내 A.KS 실제 이력은 6월 26일 시작. US 가격과 FX=1300은 계속 관측, US 거래대금=2,000,000은 **4월 28일 이후 NaN**. 6월 20일, 창 10, 기준 1e9로 판정하면 평균 **2.6e9**, price_fresh/liquid/eligible 모두 True. 기대 liquid=False/eligible=False. 가격과 거래대금이 별개로 중단되는 조건이다.

영향: 프록시와 유동성 필터를 함께 쓰는 기본 설정에도 적용되는 선정 경계다. 이번 정상 캐시의 선정이 이 문제로 바뀌었다고 확인한 것은 아니다. 프록시 거래대금이 국내 상품의 실제 유동성을 뜻하지 않는 모델 한계와, 프록시 자체의 오래된 값을 새 관측으로 취급하는 구현 오류는 구분한다.

수정 방향: 거래대금도 원천 관측 날짜/유효 마스크를 전달하고 stale source를 부적격 또는 NaN으로 처리한다. 평균의 관측 수를 단순히 채운 국내 행 수로 세지 않는다. 중단/재개, 가격만 정상, FX 중단, 상장일 경계, 전부 결측을 회귀 검증한다.

### N04 — 필요한 이력의 첫 월말을 한 번 더 제외한다

위치: `src/etf_momentum/backtest.py:91`~`:96`, 비교 `src/etf_momentum/signals.py:103`~`:109`.

min_history는 행 개수, get_loc은 0 기반 위치인데 `get_loc >= min_history`로 비교한다. 필요한 6행의 마지막 위치는 5다.

재현: 2023-01-24~04-28, lookback=(2,3,4), skip=0, SMA=3, vol=3. 1월 31일까지 정확히 6행. rank eligible=True지만 백테스트 첫 신호와 평가 기준일은 **2월 28일**이다. 기대 1월 31일. 하루 준비 경계 차이가 한 달 누락으로 커진다.

수정 방향: 행 수와 위치를 맞춰 `get_loc+1 >= min_history`처럼 비교하고 공통 신호의 readiness와 통일한다. 신호 함수가 정한 현재 최소 표본 수 자체를 바꾸라는 지적은 아니다. 필요 행 수 -1/정확히/+1, SMA/vol이 최대 창인 경우, 두 체결 방식을 회귀 검증한다.

### N05 — portfolio 현금 ETF의 내부 장기 공백을 보유 검증하지 않는다

위치: `src/etf_momentum/cli.py:1044`, `src/etf_momentum/portfolio.py:178` 및 reserve 수익 적용. 호출: portfolio CLI → cash_returns → run_portfolio.

일반 same_day 슬리브는 원자료 내부 공백을 거부하지만 현금은 계산용 prices에서 다시 ffill/pct_change한다. 현금을 실제 보유하는 시나리오도 긴 단절을 정상 보합 수익으로 처리한다.

재현: 2024-01-02~04-30, CAL/GOOD 정상, CASH는 2월 1일~3월 15일 32거래일 NaN, 최신 관측은 정상. GOOD 100%, leverage=.5로 남는 50%를 현금에 둔다. CLI 성공, 곡선 86행, **현금수익 32행 모두 0%**. 기대 보유 현금 원자료 공백에 대한 오류/명시적 평가 불가다. 지정한 현금 ETF의 공백과 현금 ETF를 지정하지 않은 0% 미투자 현금 가정을 섞어서는 안 된다.

영향: 조합의 수익·낙폭·레버리지 비교가 왜곡될 수 있다. 기본 backtest의 held gap 검사는 같은 종류의 공백을 거부하는데 portfolio 경로만 우회한다.

수정 방향: 현금 원관측표를 유지해 같은 평가 달력에서 실제 사용 구간을 검사한다. 현금이 사용되지 않는 시나리오에 불필요한 의존성을 더하지 않는다. 양의 reserve, 1배/차입 구간, 짧은 휴장, 내부 장기 결측, 없는 현금 ETF를 회귀 검증한다.

### N06 — 새 유니버스와 메타데이터를 함께 교체하지 못한다

위치: `src/etf_momentum/universe_builder.py:135`~`:148`, 특히 `:144`, `:145`; 호출 `scripts/build_universe.py:81`의 write_universe. 근거: 함수 docstring과 전체 ETF 설계 §8의 중간 실패 시 기존 파일 보존.

두 임시 파일을 먼저 작성해도 두 번의 os.replace는 하나의 트랜잭션이 아니다. CSV 교체 후 meta 교체가 실패하면 CSV는 새 목록인데 meta는 이전 생성일·개수·그룹이다. 기존 테스트는 JSON 직렬화 실패(첫 교체 전)만 검증해 이 조건을 놓친다.

재현: 이전 CSV=`OLD_CSV`, meta=`{"old":true}`를 tmp에 작성. 실제 os.replace를 첫 호출에 수행하고 두 번째 meta 경로에서 OSError를 주입한다. 실패 후 **CSV=`ticker\nNEW\n`, meta={"old":true}**이며 예외가 발생한다. 기대는 이전 한 쌍 보존 또는 이전/새 버전의 일관된 쌍이다. 기본 사용 디렉터리의 권한 오류·I/O 실패·프로세스 중단 사이에서도 일관성을 보장하지 못하는 구조다.

영향: 월초 갱신 실패를 보고도 실제 다음 선정은 새 CSV를 사용할 수 있고, 점검용 meta가 다른 입력을 설명한다. 전략 해시가 CSV 내용 자체를 담기 때문에 신호 해시가 그대로라는 주장은 하지 않는다.

수정 방향: 버전 디렉터리에 CSV/meta를 함께 저장한 뒤 단일 manifest/pointer를 원자 교체하는 방식 등으로 한 쌍의 가시성을 보장한다. 예외 rollback만으로는 두 교체 사이 프로세스 종료까지 해결하지 못한다. 선택한 저장 계약에 따라 두 번째 교체 실패·중단·동시 실행·임시 파일 정리를 회귀 검증한다.

## 실행 검증과 계산 실측

### 기존 테스트

```bash
env MPLCONFIGDIR=/private/tmp/etf-review-20261011/mpl \
    XDG_CACHE_HOME=/private/tmp/etf-review-20261011/cache \
    .venv/bin/python -m pytest -q
```

결과: **300 passed in 26.28s**. 아래 별도 재현은 기존 테스트 개수에 포함하지 않는다. 기대 오류/잘못된 결과를 assert하는 리뷰 스크립트는 실행 성공이 버그 해결을 뜻하지 않는다.

### 독립 수식 검산

| 계산 | 입력·기대식 | 실제 |
|---|---|---|
| 기본 모멘텀 | A=100×1.002^n, 700행; Σw×(P[t−21]/P[t−lookback−21]−1) | 기대=실제 0.38792207964425396 |
| 미래 참조 | 과거 cutoff 이후 A 가격 ×100 | 과거 ranking DataFrame 동일 |
| 보유 수량 유지 | A/B 각 .5, A +10% 다음 −10%, B 0%; .5×1.1×.9+.5 | 0.9950000000000001 |
| 일간 재조정 | (1+.5×.1)×(1−.5×.1) | 0.9974999999999999 |
| 비중 드리프트 | A 종료가치 .495 / 총 .995 | 0.49748743718592964 |
| 상한 재분배 | [.9,.1] 상한 .6 → 합 1; 후보 하나 상한 .6 → 합 .6 | 두 조건 통과 |
| CAGR | 2020-01-01→2021-01-01, 1→1.2; 1.2^(365.25/366)−1 | 0.19955175204308273 |
| MDD | [1,1.1,.99,1.2], min(V/cummax(V)−1) | −0.10000000000000009 |
| 미국 이전 종가+FX | 자산 100→110, FX 1000→1200; 1.1×1.2−1 | 0.32000000000000006 |
| 동일 날짜 미래값 차단 | 같은 날짜 US=999, FX=9999를 추가 | 이전 날짜 32% 계산 유지 |
| 최초 비용 | 수익 0, 100bp 비용 | 곡선 [1,.99], 비용 .01, ending_value .99 |
| 학습/테스트 | 미래 2022년 수익을 −40%로 변경 | 첫 2021년 선택 불변 |

직접 검산 허용 오차는 약 1e−12~1e−10, 미래 불변은 frame/선택값 동일성으로 검사했다. 별도 독립 리뷰어도 다른 합성 입력으로 모멘텀·수량 회계·환산·워크포워드의 같은 원리를 확인했다.

### 788개 유니버스의 실제 캐시 재계산

입력: `data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2025-04-08_latest.csv`. 저장된 생성 시점과 맞춰 2026-10-08까지 사용했다. 이는 아래 고정 end=2026-10-07의 전략 측정과 구분한다.

- 벤치마크 관측일 최근 수익률 252행: 2025-09-23~2026-10-08.
- 다시 계산한 complete-linkage 분할이 저장 CSV의 **96개 그룹·485개 소속 ETF**와 일치.
- 그룹 내 **5,437개 쌍**의 관측 63일 이상 상관을 개별 검사. 최저 상관 **0.9000376584548322**로 모두 ≥0.9. 결측/정체/짧은 겹침·사슬 연결·입력 순서·그룹 대표 이름은 새 기존 테스트와 구현을 함께 확인했다.
- 788개 중복 없음. risk_asset=0은 현금 153130.KS만. retirement_ok=0은 이름의 합성/선물 조건과 일치, 허용 728개.
- 제외 집계는 1172−37−169−124−53−1=788. 마지막 1개는 meta의 야후 미관측 265690.KS. 현재 Naver 서버에서 이 티커의 제공 여부를 다시 조회한 것은 아니다.
- builder CLI를 작은 합성 목록/가격으로 실행: 제외 후 4개, 미가격 000005.KS 보고, raw=True 요청 확인. 필수 현금 가격 누락 때 CSV/meta 모두 변경 없음. 이 정상 실패 경로와 N06의 저장 중 실패는 별개다.

### 현재 v3 전략·명령의 실제 캐시 측정

원자료 788개는 2021-10-06 시작 latest 캐시와 거래대금 캐시, 프록시/FX는 고정 2010~2026-10-07 파일을 사용했다. 모든 reader는 end를 **배제**해 마지막 평가 2026-10-06으로 잘랐다. rank/weekly lookback-years=5, backtest start=2023-01-01, 거래비용 10bp, 두 체결 방식. 충분한 워밍업 이후 결과 608행, 월간 포지션 31개씩이다.

두 계좌 모두 월말 **2026-09-30** 선택: `442580.KS, 367760.KS, 395270.KS, 363580.KS`. rank/weekly의 latest_ranking 전체가 같고, 해당 월말 backtest 보유에서 현금을 뺀 집합도 같았다. 모든 기간의 hold_until은 다음 trade_date와 일치했다. 두 next_close 체결 목록을 설치된 XKRX 세션에 대조했고 비세션 체결은 없었다.

아래 숫자는 **2024-02-29~2025-02-28**로 잘라 다시 기준 1로 만든 짧은 리뷰 측정이며, 2016년 시작 연구 표나 새 공식 성과가 아니다.

| 계좌 | 체결 | CAGR | MDD |
|---|---|---|---|
| pension | same_close | 4.4104% | −26.5955% |
| pension | next_close | 1.9811% | −26.5787% |
| retirement | same_close | 4.2621% | −19.1601% |
| retirement | next_close | 2.5997% | −19.1041% |

미국 compare는 top_n 2/3의 작은 그리드로 2행, walkforward는 train_years=1/test_months=12로 2개 표본외 창·2개 설정을 실제 CLI로 실행했다. 기본 portfolio spec도 로컬 원자료로 실행해 summary 18행/곡선 4091행을 얻었다. 모든 출력은 tmp이며 운영 history에는 append하지 않았다. 생성된 11개 run_config 메타데이터 모두 시작 HEAD와 일치하고 dirty=true임을 확인했다.

### 저장된 연구 표와 문서의 대조

`research_tables.py` 전체를 현금 CSV reader로 실행하고 출력만 tmp로 변경했다. 전체 ETF 표는 연구 문서와 표시 자리수까지 일치했다. 아래 세 곡선은 별도로 NumPy/pandas 직접식으로 검산하여 summarize_performance와 1e−10 이내 일치했다.

CAGR=(V끝/V시작)^(365.25/경과일)−1, MDD=min(V/cummax(V)−1), Sharpe=mean(r−r_cash)/std(r−r_cash,ddof=1)×√252. 현금은 각 수익 날짜로 정렬하며 기존 함수와 동일하게 미대응 현금수익은 0으로 적용했다.

| 저장 곡선 | 2016-02-29~2025-02-28 CAGR/MDD/Sharpe | ~2026-10-06 CAGR/MDD |
|---|---|---|
| 기존 60개 | 11.6879% / −16.3608% / .7694 | 16.2468% / −34.4398% |
| 788개, 그룹당 1개 | 9.2528% / −18.0896% / .5824 | 11.7246% / −53.3791% |
| 788개, 그룹 제한 없음 | 8.3167% / −37.6823% / .4737 | 13.0095% / −54.5617% |

기존 60개 주기준 2185행/전체 2574행, 788개 주기준 2187행/전체 2576행이다. 788개 곡선에만 있는 날짜는 2019-01-08, 2022-05-02다. 어느 입력이 그 날짜를 누락했는지 외부 시세와 새로 대조하지 않았으므로 날짜 차이만으로 버그를 확정하지 않았다. 같은 시작/종료라도 저장 인덱스의 행 수가 다르므로 시장 달력 정합성은 성과식 일치와 별개다. 전체 원본 워크포워드 그리드를 다시 생성한 것은 아니므로 이 표의 검산이 원자료·그리드 생성 전 과정의 무결성을 증명하지 않는다.

현재 v3의 고정 top4 설정과 저장된 워크포워드(학습 구간에서 top3/4 등을 선택)는 구분해야 한다. 표의 '현재 전략'은 전체 목록/그룹 제한 없음 정책을 뜻하며 고정 JSON 전체가 동일한 계산을 뜻하지 않는다.

## 과거 항목 재검토

| 과거 항목 | 현재 결과 | 현재 실행 증거 |
|---|---|---|
| 10/10 N01~N05 | 모두 잔존 | 위 발견사항과 boundaries.py |
| 10/09 재검토 R01 FX 단독 stale | 해결 유지 | 나이 5: fresh=True, 6/10: False |
| R02 잘린 기록 파일 | 해결 유지 | 15필드/기대16 오류, 원본 bytes 불변 |
| R03 미사용 USD 슬리브 | 해결 유지 | 존재하지 않는 파일/FX를 요구하지 않고 동일 곡선 |
| R04 중복 티커 | 해결 유지 | 공백/대소문자 중복 로더 오류, 라이브러리 후보 중복은 1행 |
| 초기 F01/F04 혼합 시장 | 해결 유지 | KR/US 혼합 명시 오류 |
| F02 가격 프록시 중단 | 해결 유지 | 마지막 2020-01-16, 2/28 fresh=False/eligible=False |
| F03 필수 현금 누락 | 해결 유지 | MISSING 다운로드 요구 후 명시 오류 |
| F05 실제 보유 공백 | 해결 유지 | CASH 5거래일 초과 공백 StaleDataError |
| F06 0비중 슬리브 | 해결 유지 | 라이브러리/CLI 미사용 슬리브 결과 불변 |
| F07 최초 비용 | 해결 유지 | 비용 .01, 종료 .99 유지 |
| F08 NaN 비중 | 해결 유지 | argparse exit 2 |
| F09 선택된 test 수익 누락 | 해결 유지 | WIN 64행 누락 명시 오류 |
| F10 임계값 설명 | 해결 유지 | 사용자 50%/30% 조건이 제외 이유에 표시 |

'해결 유지'는 표의 구체적 재현 조건을 뜻하며 해당 영역의 모든 가능한 입력을 보증하지 않는다. 가격 프록시 stale 방어가 통과해도 거래대금의 N03은 별도로 남는다. 보유 ETF 공백 방어가 통과해도 portfolio 현금 N05는 남는다.

## 제한·설계 판단

- 상장폐지 ETF 누락과 현재 상관 그룹의 과거 적용은 문서에 명시된 연구 한계다. 이번 리뷰에서 버그로 재분류하지 않았다. 그룹 제한 없음 역시 사용자의 운영 정책이다.
- 네이버 목록 실시간 fetch, 야후 다운로드/부분 응답의 실제 제공자 장애, 증권사 매수 가능 여부, 외부 시세 진실성은 새로 검증하지 않았다. docs의 기존 네이버 교차 확인을 이번 리뷰의 독립 외부 검증으로 주장하지 않는다.
- `build_universe --end`는 가격 종료일을 고정하지만 목록은 현재 네이버 목록이다. 과거 구성 목록의 point-in-time 재현 옵션으로 해석해서는 안 된다.
- 788개 전체의 2010 시작 raw 캐시가 로컬에 없어 긴 기본 전체 그리드/WF를 재실행하지 않았다. 실제 CLI는 짧은 캐시 구간과 작은 그리드로 검증하고, 기존 긴 결과는 저장 곡선/집계식/문서 일치까지만 확인했다.
- 리뷰 전체는 현재 파일 읽기·전체 테스트·대표 계산/경계 재현을 함께 사용했다. 체크리스트의 모든 조합을 독립 재현한 것은 아니다. 배당/세금·체결 가능 가격·호가·슬리피지·특정 계좌 매수 가능성을 승인하지 않는다.
- 전체 유니버스 설계의 v2·그룹당1 설명은 설계 당시 상태이고, 현재 README/연구 요약/JSON의 v3·그룹 제한 없음이 이후 사용자 결정을 반영한다. 이전 계획을 자동 우선해 현재 정책을 오류로 처리하지 않았다.

## 재현 명령과 증거 보존

아래 부록 스크립트를 각각 표시 이름으로 `/private/tmp/etf-review-20261011`에 저장하고 **리포지터리 루트**에서 실행한다. 기존 스크립트가 하는 파일 쓰기는 해당 tmp 아래로만 향한다. 실제 캐시는 reader로 읽기만 한다. 보고서의 입력 해시와 다르면 동일 수치가 나오지 않을 수 있다.

```bash
mkdir -p /private/tmp/etf-review-20261011
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-review-20261011/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261011/cache .venv/bin/python /private/tmp/etf-review-20261011/boundaries.py
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-review-20261011/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261011/cache .venv/bin/python /private/tmp/etf-review-20261011/normal_and_prior.py
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-review-20261011/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261011/cache .venv/bin/python /private/tmp/etf-review-20261011/cached_checks.py
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-review-20261011/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261011/cache .venv/bin/python /private/tmp/etf-review-20261011/universe_and_research.py
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-review-20261011/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261011/cache .venv/bin/python /private/tmp/etf-review-20261011/builder_cli.py
```

### 입력 SHA-256

| 파일 | SHA-256 |
|---|---|
| `config/portfolio_pension.json` | `a5da6a8368ff533e5914466ed742f3d550267ef971813100b4f1201b22b2f13f` |
| `config/strategies/pension.json` | `f5bbf6f22f20f87596c0aab6d045a301ebc4b9f4b59b465494986627a438f95b` |
| `config/strategies/retirement.json` | `98a3cd96a425fafad92b33f022fa0e2c1a14c6d1b0e066a4460ca09f0b7604f5` |
| `config/universe_kr_all.csv` | `ee4047581bffc7095301436cef5bc5d6e88ac81de31165fa5f74b813fdc21f98` |
| `config/universe_kr_all.meta.json` | `98520bf072641611cc44fff36e09f266fc71963e4313a3e09b367e7e36e1c4fa` |
| `data/prices_raw_069500_KS_153130_KS_KRW=X_SPY_2010-01-01_2026-10-07.csv` | `fa7a0d00fc0dfd396e631691bb85dd4314eb406a48315b6376a7b036e9b5244c` |
| `data/prices_raw_069500_KS_2010-01-01_2026-10-07.csv` | `47a6c34cdb5d793dec154ee944f1f07fef6639379d783734584738fa1b792019` |
| `data/prices_raw_153130_KS_2010-01-01_2026-10-07.csv` | `40c4df5a97a270dd9ad6e5c5925c25e89e11c945f1b9a334cf82faf2370cae90` |
| `data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2021-10-06_latest.csv` | `0d71d40818da9d68ece57597d48f1430bfade237f0d1e6d8f40a29e63bcba978` |
| `data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2025-04-08_latest.csv` | `e378d31a93f6c70bca3e12128ac6beb33a33cbab151881df71e9a74fb8924d8e` |
| `data/prices_raw_BIL_CIBR_DIA_GLD_IBB_ICLN_IGV_ITA_IWM_IYT_KRE_QQQ_SMH_SOXX_SPY_TAN_URA_VNQ_XBI_XLB_XLC_XLE_XLF_XLI_XLK_XLP_XLRE_XLU_XLV_XLY_XME_XRT_2010-01-01_2026-10-07.csv` | `4131f72107ab3c9f3fe3fc26f7ad4d85da497f82f17ba5b17ae4e1d5a558dd2f` |
| `data/prices_raw_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv` | `7aca8716bd62d88f2092b78f11ba7cdbcfc7d67b341f564e895142e3f86453c3` |
| `data/prices_raw_KRW=X_2010-01-01_2026-10-07.csv` | `fe32ed2511a9ad520bd920f723e9c9c1c4136b99264a8d71d002f70c625fab8a` |
| `data/prices_raw_SPY_2010-01-01_2026-10-07.csv` | `a9404c87bc09eaec42cb89140ee0c7be6883fb55ece9821372d402ae0387f83b` |
| `data/traded_value_788_tickers_1d20b3b9c5fefa8f_2021-10-06_latest.csv` | `3b1c2cd79b7b6a39f5de72d00ab49ee6d9db90df5450296c1733ea471f4c8c75` |
| `data/traded_value_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv` | `f7a1fb2eddc6c9d6ba9516b96410fe89745b9082d0fde74f924ce2d089d2f596` |
| `results/walkforward/kr_all_pension_10eok_10bp_nogroup_tvoff/walkforward_equity.csv` | `a1a39ebabf74c62816932b807bcfe603dcd500a014902daad25be2f76d5f0f60` |
| `results/walkforward/kr_all_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv` | `2f32ef1a1d478643a510a5456eefd1c4c475d57af46323c039a3f39b4c4a4805` |
| `results/walkforward/kr_pension_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv` | `652d8effcb6f94ab0548c64d5ca1c5c29fdd2669f50125869d1f766d58f05e5c` |

### 추가 경계: new_boundaries.py

실행 방식은 위 명령의 파일명을 `new_boundaries.py`로 바꾼다.

```python
from pathlib import Path
from unittest.mock import patch
import os, json
import numpy as np
import pandas as pd
from etf_momentum.universe_builder import write_universe
from etf_momentum.backtest import BacktestConfig, run_backtest, previous_month_end
from etf_momentum.signals import MomentumConfig
out=Path('/private/tmp/etf-review-20261011');out.mkdir(exist_ok=True)
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
assert csv.read_text()=='ticker\nNEW\n' and json.loads(meta.read_text())=={'old':True}
idx=pd.bdate_range('2023-01-02','2023-05-31')
p=pd.DataFrame({'A':100*np.exp(.002*np.arange(len(idx))),'BENCH':100*np.exp(.001*np.arange(len(idx))),'CASH':100.},index=idx)
p.loc['2023-03-31','BENCH']=np.nan
mc=MomentumConfig(lookbacks=(2,3,4),skip_days=0,sma_window=3,volatility_window=3)
_,positions,_=run_backtest(p.ffill(),BacktestConfig(top_n=1,candidate_tickers=('A',),benchmark='BENCH',cash_ticker='CASH',momentum=mc,risk_on_benchmark=True),observed=p.notna())
assert pd.Timestamp('2023-03-30') in set(positions.rebalance_date)
try: previous_month_end(p.index[p.BENCH.notna()],pd.Timestamp('2023-04-03'),sessions=p.index)
except ValueError as exc: print(exc)
else: raise AssertionError('missing month-end accepted')
print('N06: NEW CSV + OLD meta; N02B: backtest shifts while rank raises')
```

### 부록: boundaries.py — 잔존 문제·기록 재검토

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

OUT=Path('/private/tmp/etf-review-20261011')
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
assert dec.trade_date==pd.Timestamp('2025-12-31')
res['N01_next_close']={'rebalance_date':str(dec.rebalance_date.date()),'actual_trade_date':str(dec.trade_date.date()),'expected_next_benchmark_date':'2026-01-02'}

# Historical benchmark holes: the downstream baseline checker sees finite zero returns.
idx=pd.bdate_range('2023-01-02','2023-05-31')
raw=pd.DataFrame({'A':100*np.exp(np.arange(len(idx))*.002),'SPY':100*np.exp(np.arange(len(idx))*.001),'BIL':100.},index=idx)
gap=(idx>='2023-03-06')&(idx<='2023-03-20')
raw.loc[gap,'SPY']=np.nan
cli._require_fresh(raw.notna(),{'benchmark':'SPY','cash':'BIL'})
cfg=BacktestConfig(top_n=1,candidate_tickers=('A',),momentum=MomentumConfig(lookbacks=(2,3,4),skip_days=0,sma_window=3,volatility_window=3),risk_on_benchmark=True)
eq,pos,_=run_backtest(raw.ffill(),cfg,observed=raw.notna())
returns=eq.benchmark.pct_change().loc['2023-03-06':'2023-03-20']
assert len(returns)==11 and returns.eq(0).all()
baseline=cli._benchmark_baseline(raw.ffill().SPY.pct_change(),returns.index)
assert baseline.eq(0).all()
res['N02_benchmark']={'latest_guard':'passed','run':'success','missing_raw_rows':11,'zero_benchmark_returns':int(returns.eq(0).sum()),'baseline_guard':'passed'}

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
assert row.liquid and row.eligible and row.avg_traded_value==2.6e9
res['N03_proxy_volume']={'source_last_date':str(proxy_value.US.last_valid_index().date()),'as_of':'2023-06-20','avg_traded_value':float(row.avg_traded_value),'price_fresh':bool(row.price_fresh),'liquid':bool(row.liquid),'eligible':bool(row.eligible)}

# Warm-up exact row count: rank is ready but the backtest waits one extra row.
idx=pd.bdate_range('2023-01-24','2023-04-28')
panel=pd.DataFrame({'A':100*1.002**np.arange(len(idx)),'BENCH':100*1.001**np.arange(len(idx)),'CASH':100.},index=idx)
mc=MomentumConfig(lookbacks=(2,3,4),skip_days=0,sma_window=3,volatility_window=3)
row=rank_momentum(panel,pd.Timestamp('2023-01-31'),config=mc,candidate_tickers=('A',),observed=panel.notna()).iloc[0]
cfg=BacktestConfig(top_n=1,candidate_tickers=('A',),benchmark='BENCH',cash_ticker='CASH',momentum=mc)
eq,pos,_=run_backtest(panel,cfg,observed=panel.notna())
assert row.eligible and eq.index[0]==pd.Timestamp('2023-02-28')
res['N04_warmup']={'rows_at_Jan31':len(panel.loc[:'2023-01-31']),'required_rows':6,'rank_eligible':bool(row.eligible),'actual_first_signal':str(pos.rebalance_date.iloc[0].date()),'actual_base_date':str(eq.index[0].date()),'expected_first_signal':'2023-01-31'}

# Portfolio CLI ignores a cash price gap although the reserve is invested in cash.
cal=pd.bdate_range('2024-01-02','2024-04-30')
cash_raw=pd.DataFrame({'CAL':100.,'GOOD':100*1.001**np.arange(len(cal)),'CASH':100*1.0005**np.arange(len(cal))},index=cal)
cash_raw.loc['2024-02-01':'2024-03-15','CASH']=np.nan
def download(tickers,raw=False,**kw):
    frame=cash_raw[list(tickers)].dropna(how='all')
    return frame if raw else frame.ffill()
def run_portfolio_case(unused=False):
    spec={'base_currency':'KRW','calendar_ticker':'CAL','cash_ticker':'CASH','start':'2024-01-02','end':'2024-05-01',
          'sleeves':{'good':{'ticker':'GOOD','currency':'KRW'}},'scenarios':[{'name':'g','weights':{'good':1.0},'leverage':.5}]}
    if unused:
        spec['sleeves']['unused']={'equity_csv':str(OUT/'does_not_exist.csv'),'currency':'USD'}
        spec['scenarios'][0]['weights']['unused']=0.
    path=OUT/f'portfolio{unused}.json';path.write_text(json.dumps(spec))
    target=OUT/f'portfolio{unused}'
    with patch.object(cli,'download_prices',download),patch.object(sys,'argv',['etf-momentum','portfolio','--spec',str(path),'--output-dir',str(target)]),contextlib.redirect_stdout(io.StringIO()):
        cli.main()
    return pd.read_csv(target/'portfolio_equity.csv',index_col=0)
a=run_portfolio_case();b=run_portfolio_case(True)
pd.testing.assert_frame_equal(a,b)
res['R03_unused_sleeve']='same curve, no unused file or FX loaded'
cash_returns=cash_raw.CASH.ffill().pct_change().loc['2024-02-01':'2024-03-15']
assert len(cash_returns)==32 and cash_returns.eq(0).all()
res['N05_portfolio_cash']={'run':'success','leverage':.5,'missing_raw_rows':32,'cash_flat_return_rows':int(cash_returns.eq(0).sum()),'curve_rows':len(a)}

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

(OUT/'boundaries.json').write_text(json.dumps(res,ensure_ascii=False,indent=2))
print(json.dumps(res,ensure_ascii=False,indent=2))
```

### 부록: normal_and_prior.py — 계산·과거 방어 검산

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

OUT = Path('/private/tmp/etf-review-20261011')
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

out=Path('/private/tmp/etf-review-20261011/cached');out.mkdir(exist_ok=True)
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
   bp=run(f'{account}_{execution}','backtest','--strategy',s,'--start','2023-01-01','--end','2026-10-07','--transaction-cost-bps','10','--execution',execution)
   eq=pd.read_csv(bp/'equity_curve.csv',index_col=0,parse_dates=True);pos=pd.read_csv(bp/'positions.csv')
   held=pos.loc[pos.rebalance_date==selection.as_of.iloc[0],'tickers'].iloc[0].split(',')
   assert sorted(t for t in held if t!='153130.KS')==sorted(selected)
   assert (pos.hold_until.iloc[:-1].to_numpy()==pos.trade_date.iloc[1:].to_numpy()).all()
   main=eq.loc['2024-02-29':'2025-02-28'];met=summarize_performance(main/main.iloc[0])
   res[account]['executions'][execution]={'main_cagr':met['strategy_cagr'],'main_mdd':met['strategy_max_drawdown'],'full_rows':len(eq),'positions':len(pos),'monthend_selection_matches':True,'hold_until_matches_next_trade':True}
 # Exercise both grid commands through real cached data, with a bounded grid.
 common=['--universe','config/universe_us_sectors.csv','--benchmark','SPY','--cash','BIL','--start','2023-01-01','--end','2026-10-07','--top-n-grid','2,3','--weighting-grid','equal','--rank-by-grid','weighted_momentum','--rebalance-grid','ME','--relative-strength-grid','off']
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

### 부록: universe_and_research.py — 유니버스·연구 수식 검산

```python
import hashlib,json,runpy,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum.universe_builder import recent_returns,correlation_groups
from etf_momentum.backtest import summarize_performance
from etf_momentum import data
out=Path('/private/tmp/etf-review-20261011')
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

### 부록: builder_cli.py — 유니버스 생성 CLI

```python
import contextlib,importlib.util,io,json,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
out=Path('/private/tmp/etf-review-20261011')
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
