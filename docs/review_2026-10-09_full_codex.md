# 전체 코드 리뷰 — 2026-10-09

## 판단

ETF를 선정하고 주간 변화를 관찰하는 핵심 구조는 프로젝트 컨셉과 맞는다. 전략 파일을 통한 규칙 고정, 공통 선정 함수, 그룹 제한, 계좌 후보 제한, 원자료 캐시, 월말 거래소 달력, 신호 revision 기록이 구현되어 있다. 기본 전략 두 개의 실제 저장 자료에서도 `rank`와 백테스트의 월말 선택이 일치했다.

다만 **P1 1개, P2 8개, P3 1개**를 확인했다. 기본 전략의 정상 입력 검증과 특정 지원 설정·결측 상황의 검증을 구분해야 한다. 특히 해외 티커를 직접 시장 필터로 넣는 경우의 정보 시점, 프록시 원자료 최신성, 현금 데이터 검증을 먼저 보완하는 것이 좋다. 이 리뷰에서 기본 연금 전략의 정상 자료에 대해 선정 결과가 틀렸다고 확인한 것은 아니다.

기존 테스트는 **245 passed, 25.65초**였다. 테스트 성공과 아래 추가 재현은 서로 다른 결과이며, 기존 테스트만으로 경계 조건을 모두 보장할 수 없다.

## 검토 범위와 환경

- 대상: 현재 작업 트리 전체. 기준 HEAD `99abcb4085cfb55618c8c29031ac47681e89bbf4`.
- 시작 상태: `?? .agents/`만 존재. 제품 코드에는 미커밋 변경이 없었다. 스킬이 untracked이므로 작업 트리는 dirty 상태였다.
- 읽은 구현: `src/etf_momentum/`의 모든 Python 모듈과 `scripts/research_tables.py`. CLI 입력부터 설정·데이터·관측표·프록시·신호·선정·백테스트·워크포워드·조합·기록·출력을 추적했다.
- 기준 문서: `CLAUDE.md`, `README.md`, `pyproject.toml`, 전략 설정/기록 및 원자료/달력 설계 문서, `docs/concept_improvements_2026-10-07.md`, 최근 리뷰와 연구 표. 적용되는 별도 `AGENTS.md`는 작업 영역에서 발견하지 못했다.
- 실행 환경: Python 3.14.3, pandas 3.0.3, numpy 2.4.6, pytest 9.0.3, exchange-calendars 4.13.2, yfinance 1.3.0.
- 테스트 명령: `.venv/bin/python -m pytest -q`. 프로젝트의 기존 가상환경을 사용했다.
- 프로젝트 전용 리뷰 스킬을 적용했고 독립 리뷰어 1명도 검토했다. 독립 리뷰어에는 범위·현재 파일·지침을 전달했으며 예상 버그나 결론을 전달하지 않았다. 반환된 지적은 주 리뷰어가 다시 재현했다.
- 네트워크 다운로드는 하지 않았다. 실제 자료 검증은 기존 `prices_raw_*` 및 거래대금 캐시를 읽는 downloader로 CLI를 실행했다. 출력과 합성 자료는 `/private/tmp/etf-review-20261009/`에 분리했다.
- 제품 코드·전략·유니버스·기존 테스트·운영 이력·캐시·공식 결과를 수정하지 않았다. 커밋하지 않았다. 이 문서는 공식 연구 결과 재생성이 아닌 dirty 상태의 리뷰 실험이다.

## 확인된 발견사항

### F01 — P1: 한국 전략의 미국 시장 필터가 같은 날짜 미국 종가를 참조한다

위치: `src/etf_momentum/cli.py:262`, `:569`, `src/etf_momentum/backtest.py:320`.

발생 조건은 한국 벤치마크/후보에 `--risk-on-benchmark --risk-on-ticker SPY`처럼 미국 티커를 직접 사용하는 것이다. downloader는 두 시장을 하나의 날짜 인덱스에 놓고, 선정은 `prices.loc[:rebalance_date, t]`의 마지막 값으로 시장 필터를 계산한다. 미국 필터에는 프록시·외화 슬리브에 적용한 strictly-before 규칙이 없다.

한국 날짜 D의 종가로 신호를 만들고 같은 종가에 체결하는 모델에서 미국 D일 종가는 아직 알 수 없는 값이다. 미래의 필터 값으로 risk-on/off가 바뀐다. 후보 순위의 날짜 행 문제인 F04와 별개로 정보 이용 시점의 문제다.

재현: 350영업일 동안 SPY=100, 마지막 날 미국 SPY만 400으로 변경했다. 한국 벤치마크는 상승 중이고 후보 A는 eligible=True다. 2021-05-04 한국 기준 선택에서 같은 날 US 값 400을 주면 **risk_on=True, A 선정**이다. 당시 알 수 있는 직전 US 값 100을 유지하면 **risk_on=False**다. 과거 한국 신호가 나중에 확정되는 US 값에 의존한다.

수정 방향: 입력 자산의 시장·평가 시점을 구분하고, 해외 직접 필터도 한국 기준일보다 이른 원자료로 정렬한다. 이를 지원하지 않을 경우 혼합 시장 필터를 명시적으로 거부한다. freshness도 원관측일을 유지해야 한다. 미국 종가 D만 변경했을 때 한국 D의 선정이 변하지 않는 회귀 검증을 추가한다.

범위: 기본 `pension.json`/`retirement.json`은 `069500.KS` 필터이므로 이 사례가 그대로 적용되지 않는다. 직접 US 티커를 넣는 지원 설정에서 확인한 미래 정보 참조이므로 P1로 분류했다.

### F02 — P2: 오래된 프록시·환율이 매일 새 관측으로 표시된다

위치: `src/etf_momentum/data.py:89`, `:113`, `src/etf_momentum/cli.py:1281`, `:1343`.

`_asof_previous_day`는 마지막 값을 기간 제한 없이 연장한다. 프록시 downloader도 기본 filled 결과를 쓰며, `_observations`는 상장 전 채워진 가격이 있으면 그 모든 날짜를 True로 표시한다. 실제 프록시·FX 관측일이 사라지므로 새로 추가한 freshness 검증이 프록시 구간에서는 우회된다.

재현: 국내 달력은 2018-01-01~2020-03-02, LOCAL 자체 가격은 상장일 03-02의 200만 있다. US 프록시는 상승하며 FX=1000이고, 둘 모두 2020-01-15에서 끝난다. 02-28의 기본 ranking은 다음과 같다.

| 항목 | 결과 |
|---|---:|
| 실제 마지막 US/FX 관측 | 2020-01-15 |
| 관측표가 주장하는 마지막 날짜 | 2020-02-28 |
| 모멘텀 | 0.16274533230226118 |
| SMA 대비 거리 | 0.07199028407773178 |
| price_fresh / eligible | True / True |

44일 끊긴 자료가 후보를 통과했다. 단순히 flat 가격을 허용하는 문제가 아니라 원자료가 최신이라고 잘못 기록하는 문제다.

수정 방향: proxy와 FX의 raw 관측일을 각각 유지해 as-of 결과와 함께 전파하고 허용 최신성을 검사한다. 가격이 채워졌다는 이유만으로 관측=True를 만들지 않는다. 정상 휴장에 따른 직전 값 이용과 장기 데이터 단절을 구분한다.

### F03 — P2: rank가 가격 자료 없는 현금 ETF를 정상 대기 자산으로 출력한다

위치: `src/etf_momentum/cli.py:262`, `:268`, `:269`.

현금의 계좌 매수 가능 여부 검증은 수정되었지만 데이터 존재·최신성 검증은 남아 있다. rank의 extras에는 cash가 없고, 필수 티커/최신성 검사는 `include_cash=False`다. cash가 유니버스 밖이면 요청하지 않는다. 유니버스 안이라도 공급자가 cash만 누락하면 필수 검사에서 빠진다.

재현: 유니버스 A/BENCH, 정상 합성 가격, `rank --benchmark BENCH --cash MISSING --end 2026-10-07`. downloader의 실제 요청은 **A,BENCH**뿐이다. 명령은 성공하고 `latest_selection.csv`에 **selected A + cash MISSING, slots=2**를 저장한다. backtest라면 같은 cash 누락을 거부한다.

README/CLAUDE의 필수 현금 가격 누락 시 중단 규칙 및 선정 명령 간 일관성과 충돌한다. 데이터가 없는 ETF를 정상 대기 자산으로 설명하거나 기록할 수 있다.

수정 방향: cash가 지정된 rank는 cash를 요청하고 존재·freshness를 검사한다. `cash=None`은 미투자 현금으로 구분한다. weekly는 매매 선정을 하지 않는 관찰 명령이므로 필요한 데이터 범위를 별도로 명시하면 된다.

### F04 — P2: 다른 시장의 날짜를 추가하면 같은 ETF의 신호가 달라진다

위치: `src/etf_momentum/signals.py:103`, `:111`, `:112`, `:125`, `src/etf_momentum/backtest.py:77`.

lookback·skip·SMA·변동성은 티커/기준 시장의 거래일이 아니라 입력 가격표의 전체 행을 센다. KR/US 혼합 다운로드는 한 시장만 열린 날짜를 추가하고 한국 가격을 채운다. 투자 후보와 한국 가격이 같아도 참고용 US 티커를 추가한 것만으로 계산 기간이 바뀐다.

합성 재현: 국내 날짜 6개와 작은 lookback 1/2/3, skip=0, 가중치 기본값을 사용했다. 국내 자료가 없는 참고용 시장의 날짜 하나를 추가하면 같은 A의 weighted momentum이 **0.16758241758241754 → 0.10439560439560433**으로 바뀐다.

실제 저장 자료 확인: 2026-09-30, 후보 `139260.KS`, 동일한 국내 60티커 원자료에 SPY 원자료의 날짜를 outer-join한 것만으로 다음처럼 변했다. 프록시 연결 없이 입력 행 변화 자체를 분리한 비교다.

| 지표 | 국내 가격표 | 국내 + SPY 날짜 |
|---|---:|---:|
| weighted_momentum | 1.3268633006764898 | 1.3818581270533832 |
| SMA 대비 거리 | 0.252081065374107 | 0.2135127581970755 |

참고 자산 추가가 순위·조건 통과·리밸런싱 날짜를 바꿀 수 있다. `lookback`을 거래일 단위로 설명하는 CLI/README와 맞지 않는 혼합 시장 경로다.

수정 방향: 선정/평가 기준 달력을 먼저 정하고 입력을 정렬한다. 시장별 정보 이용 시점은 F01처럼 별도로 해결해야 한다. 미사용 참고 티커 추가 전후 후보 신호가 불변인지 검증한다. 현재 기본 국내 패널의 모든 행이 올바른 거래일인지까지 외부 자료로 확정한 것은 아니다.

### F05 — P2: 백테스트 중간의 현금 가격 단절이 0% 수익으로 계산된다

위치: `src/etf_momentum/backtest.py:71`, `:76`, `:119`, `src/etf_momentum/cli.py:576`, `:1374`.

CLI의 필수 freshness 검사는 자료 마지막 날짜에서 실행된다. 중간에 오래 끊겼다가 다시 관측되면 통과한다. 백테스트는 선택일마다 시장 필터 freshness를 확인하지만 현금의 역사적 단절은 검사하지 않고 forward-fill 수익률을 사용한다.

재현: 700일 합성 자료의 CASH 101행을 NaN으로 만들고 이후 정상 자료를 남겼다. 마지막 날짜의 `_require_fresh`는 통과했다. 모든 슬롯을 현금으로 두는 백테스트도 정상 완료했고, 결측 기간 측정 내부 **99일을 0% 수익**으로 기록했다. 관측되지 않은 기간 수익이 알려진 0% 수익처럼 계산된다. 재개일에는 이전 관측 이후 변화가 한 날짜에 몰린다.

현재 설계가 명시적으로 강화한 것은 종료일 필수 검사와 중간 시장 필터 검사다. 이 발견사항은 그 구현을 완료하지 않았다는 주장과 달리, **수익 계산에 필요한 현금의 중간 결측 검증이 부족하다**는 것이다. 후보 freshness만 강화해도 보유 중 자산의 값은 계속 채워질 수 있다.

수정 방향: 평가 기간의 필수 현금/벤치마크, 실제 보유 자산에 대해 원자료 관측 간격을 검사하고 허용 범위를 넘으면 오류 또는 명시된 결측 정책을 적용한다. 미래의 재개 관측이 과거 단절을 정상으로 바꾸면 안 된다. 조합의 cash 역시 `cli.py:1006`에서 filled 자료를 쓰는 같은 위험이 있으나 별도 실제 시나리오 오류로 중복 집계하지 않았다.

### F06 — P2: 비중 0인 슬리브가 평가 기간과 수익을 바꾼다

위치: `src/etf_momentum/portfolio.py:170`, `:174`.

개별 비중 0은 유효한 입력이지만, `names=list(config.weights)`와 `.dropna()`는 0 비중 슬리브까지 필수 공통 자료로 취급한다.

재현: 2024-01-01부터 6영업일, A=[0,.1,.1,.1,.1,.1], B=[NaN,NaN,0,0,NaN,NaN].

| 설정 | 평가 기간 | 종료가치 |
|---|---|---:|
| A=1 | 01-01~01-08 | 1.6105100000000008 |
| A=1, B=0 | 01-03~01-04 | 1.1 |

노출이 0인 B 때문에 A의 수익 4일이 사라진다. 0 비중 이름이 아예 입력에 없으면 필수 티커 오류도 발생한다. README의 미사용 슬리브가 결과를 바꾸지 않는다는 규칙과 충돌한다.

수정 방향: 양수 비중의 이름만 시나리오 필수 자료·공통 기간·목표 배열에 포함한다. 설정 검증 자체는 유지한다. 현재 기본 JSON에 이 0 비중 패턴이 들어 있다고 주장하지 않는다.

### F07 — P2: 조합 성과 요약에서 최초 진입 비용이 사라진다

위치: `src/etf_momentum/portfolio.py:197`, `:199`, `src/etf_momentum/cli.py:1029`.

`run_portfolio`는 최초 비용을 첫 가치에서 차감한다. 요약은 그 차감된 첫 가치를 다시 1로 나누므로 평가가 최초 배분일부터 시작하는 경우 진입 비용이 성과에서 제외된다. 반면 `annual_rebalance_cost`에는 최초 비용이 포함된다.

재현: 2영업일, A 수익 0%, A=1, 초기 비용 100bp. equity는 **[0.99,0.99]**, history.cost=[0.01]이지만 CLI와 동일한 정규화로 계산한 ending value는 **1.0**, CAGR은 0이다. 처음 투입한 1원 기준 실제 종료가치는 0.99다.

부분 기간 성과를 해당 기간의 시작 가치로 정규화하는 것은 정상이다. 문제는 비용을 포함하는 전체 시작 기간에서도 기준점이 비용 이후로 잡히며, 비용 포함 총성과와 구분해서 설명하지 않는 것이다.

수정 방향: 최초 비용 전 기준 가치가 성과에 포함되도록 곡선/기준 행을 설계하거나, 진입 비용 후 성과를 별도로 표시한다. 비용 합계와 수익률의 측정 구간을 일치시킨다. 최초 진입보다 훨씬 뒤에서 시작한 기존 연구 표 전체가 이 비용만큼 틀렸다는 뜻은 아니다.

### F08 — P2: 일반 CLI 모멘텀 가중치의 NaN이 오류 대신 현금 선정으로 처리된다

위치: `src/etf_momentum/cli.py:1427`, `:1428`, `src/etf_momentum/signals.py:74`.

일반 CLI의 `--weights`는 단순 float 변환과 길이만 검사한다. NaN은 `total <= 0` 검사도 우회하므로 정상 실행처럼 진행한다. 전략 JSON은 finite 검증을 하므로 경로에 따라 검증이 다르다.

실제 main 재현: 정상 A/B 자료에 `rank ... --weights nan,0,1`. 명령이 성공하고 순위 2행의 모멘텀이 모두 NaN이며, 선택은 **cash CASH + excluded A/B**로 저장된다. 잘못된 입력이 데이터 기반 방어 신호처럼 보인다.

수정 방향: 세 가중치의 유한성과 유효한 합계를 검사하고, lookback도 양수 정수인지 공통 검증한다. `--strategy`와 일반 CLI가 같은 유효성 기준을 사용해야 한다. 음수 가중치 허용 여부는 명시적 정책으로 정하고, NaN/inf는 거부한다.

### F09 — P2: 워크포워드가 선택된 테스트 수익률 결측을 조용히 0으로 바꾼다

위치: `src/etf_momentum/walkforward.py:60`, `src/etf_momentum/cli.py:865`.

학습 coverage는 확인하지만 선택된 테스트 구간은 `.fillna(0.0)`으로 처리한다. 실제 수익 0과 미관측·종료된 수익 흐름을 구분하지 못한다. 고정 전략 비교도 결측을 0으로 바꾼다.

재현: 2020~2022 영업일, WIN=.002, OTHER=.0001. WIN은 2020 학습에서 승리하나 2021-01-01~03-31 테스트 수익 64행이 NaN이다. 첫 테스트는 WIN을 그대로 선택하고 **64행을 0으로 반환**, 해당 연도 수익 0.4823171784874569를 정상 결과로 만든다.

범위: 이번 실제 2개 그리드의 수익 흐름에서 이 결측이 발생했다는 증거는 없다. 결측을 허용하는 함수 입력의 데이터 무결성 문제로 재현했다.

수정 방향: 선택 후 필요한 테스트 수익이 없으면 중단하거나 사전에 정한 현금 대체 정책을 명시한다. 테스트 전 기간의 결측 여부를 보고 다른 승자를 사후 선택하는 수정은 미래 정보 참조를 만들 수 있으므로 피한다. 학습 coverage와 테스트 데이터 유효성은 별개로 검사한다.

### F10 — P3: 사용자 임계값을 바꿔도 제외 설명은 0/SMA 기준으로 고정된다

위치: `src/etf_momentum/cli.py:451`, `:461`, `src/etf_momentum/signals.py:155`.

SMA 기간 표시 수정은 확인했다. 그러나 `min_weighted_momentum`과 `min_trend_strength`를 바꾸면 제외 문구는 계속 `모멘텀≤0`, `200일선 아래`다.

재현: 상승 A의 실제 모멘텀 **0.38792207964425396**, SMA 대비 거리 **+0.21186064855525233**. 설정을 각각 0.5와 0.3으로 두면 제외는 맞지만 이유가 **모멘텀≤0, 200일선 아래**다. 두 설명 모두 실제 지표와 반대다.

수정 방향: 설명 생성에 신호 설정을 전달해 `모멘텀 기준 50% 미달`, `SMA 대비 최소 거리 30% 미달`처럼 실제 임계값을 표시한다. 기본 전략은 임계값 0이므로 이 재현은 일반 CLI/라이브러리 사용자 설정에 해당한다.

## 독립 검산과 정상 동작 확인

| 검증 | 독립 기대값 / 실제 결과 |
|---|---|
| 기본 모멘텀 | `sum(w*(P[-22]/P[-(L+22)]-1))` = 0.38792207964425396, 구현과 일치 |
| 과거 신호의 미래 입력 불변성 | as_of 이후 A 가격을 100배 변경해도 과거 ranking 동일 |
| buy-and-hold | A/B 각 0.5, A +10% 후 -10%, B 0%: 종료 0.995, 구현 0.9950000000000001 |
| daily-rebalanced | 동일 경로 종료 0.9975, 구현 0.9974999999999999 |
| 가격에 따른 비중 변화 | A 종료 비중 .495/.995 = 0.49748743718592964, 구현 일치 |
| 개별 비중 상한 | 0.9/0.1 → 상한 .6 적용 후 합 1; 단일 자산 상한 .6이면 잔여 .4 보존 |
| CAGR | 2020-01-01~2021-01-01, 1→1.2: `1.2**(365.25/366)-1` = 0.19955175204308273 |
| MDD | [1,1.1,.99,1.2] → -10%, 구현 -0.10000000000000009 |
| 외화 이전 종가 | 자산 +10%, FX +20%: 곱 수익 +32%, 구현 +32.000000000000006%; 같은 날짜 미래 값 제외 |
| same_day 원자료 공백 | [100,NaN,110] 내부 공백을 오류로 거부 |
| 거래소 월말 | 9/15 + 10/01에 9/30 세션 지정 → 누락 월말 오류 |
| 워크포워드 미래 변경 | 2022 테스트 이후 수익을 바꿔도 첫 2021 학습 선택 동일 |

수치 대조에는 `np.isclose`의 허용 오차를 사용했고 계산상 부동소수점 차이를 버그로 취급하지 않았다. 기존 테스트에는 비용·회전율·유동성·그룹·계좌·프록시 시점·기록 중복/revision·주간 설정 분리의 검증도 포함돼 있으며 모두 통과했다.

## 실제 저장 자료 실행 결과

downloader만 로컬 raw/거래대금 캐시 읽기로 바꾸고 실제 CLI 경로를 실행했다. 입력 종료일 `2026-10-07`은 배제형이며 실제 마지막 가격은 2026-10-06이다. rank/weekly는 lookback-years=20으로 같은 전체 캐시 범위를 사용했다.

- 연금저축·퇴직연금 각각 rank, weekly, backtest same_close/next_close를 실행했다.
- 두 계좌의 2026-09-30 선정: **139260.KS, 292150.KS, 102780.KS, 381180.KS**.
- 같은 시점 rank/weekly 순위 CSV가 일치했다. 선정 네 종목은 각 백테스트의 그 월말 신호에서 현금을 제외한 보유와 일치했다. retirement는 기존 모델의 위험자산 상한 때문에 실제 비중은 다르다.
- 각 백테스트는 평가 행 3,810개, 포지션 188개를 생성했다. 연속 포지션 187쌍 모두 `hold_until == 다음 trade_date`였다.

주 기준 기간 2016-02-29~2025-02-28, 비용 10bp, 동일 비중의 리뷰 측정값:

| 전략 | 체결 | CAGR | MDD |
|---|---|---:|---:|
| pension | same_close | 11.597384% | -24.776565% |
| pension | next_close | 10.303659% | -24.599838% |
| retirement | same_close | 9.152717% | -17.541112% |
| retirement | next_close | 8.283887% | -17.416585% |

연금저축의 체결 지연 차이는 약 **-1.293725%p/년**이다. 현재 코드·같은 캐시·같은 선정 규칙의 종가 체결 모델 비교이며 실제 주문 성과를 측정한 것은 아니다.

미국 compare는 top_n 2/3, ME, equal, weighted_momentum, 상대강도 off의 2개 조합을 실행했다. walkforward는 같은 두 조합, 그룹 1, risk off, train 5년/test 12개월로 **11개 테스트 구간**을 생성했다. 전체 96개/32개 연구 그리드는 반복하지 않았다.

`portfolio_pension.json`은 저장된 전략 곡선과 raw 가격을 사용해 **요약 18행·조합 곡선 4,091행**을 생성했다. 이때 기존 전략 곡선은 입력 파일이며 모든 워크포워드를 이번 리비전에서 재생성한 것은 아니다. `scripts/research_tables.py`도 임시 출력으로 실행했으며 출력된 핵심 수치가 연구 문서의 표시 정밀도에서 일치함을 확인했다. 표 생성 성공은 입력 성과 전체의 재검증을 의미하지 않는다.

## 2026-10-08 리뷰 재확인

| 과거 항목 | 현재 확인 |
|---|---|
| 1 필수 자산 freshness | latest guard와 선택일 시장 필터 중단은 구현·테스트 통과. rank cash 경로와 프록시/중간 현금 구간은 F02/F03/F05로 잔존 범위를 분리 |
| 2 불완전 월말 | 거래소 달력이 있는 정상 지원 경로에서 오류로 중단. 달력 없는 fallback의 기존 휴리스틱은 문서화된 제한으로 유지 |
| 3 rank cash 계좌 검증 | `_validate_cash` 공통 적용, 계좌·안전자산 위반 15개 파라미터 테스트 통과. 가격 검증은 F03과 별개 |
| 4 same_day 결측 은폐 | raw 티커 경로와 결측 거부 테스트 통과; 직접 합성 공백도 거부 |
| 5 과거 조회 시작일 | 종료일 기준 역산으로 수정, 회귀 테스트 통과 |
| 6 weekly 외부 필터 누락 | 관찰 명령에서 필터 자료를 필수로 요구하지 않는 정책으로 수정, 회귀 테스트 통과 |
| 7 portfolio 설정 검증 | finite/범위/long-only 검증 추가, 회귀 테스트 통과 |
| 8 next_close hold_until | 실제 캐시 실행의 187개 연속 포지션 경계와 두 체결 모드 회귀 테스트 통과 |
| 9 SMA/점수 설명 | 설정된 SMA 기간과 실제 순위 점수 표시 수정 확인. 사용자 최소 임계값 설명은 F10으로 별도 확인 |

## 우선 수정 순서와 검토 한계

1. **F01/F02/F04:** 입력 시장의 정보 시점·기준 달력·원관측일을 함께 보존한다. 동일 날짜 문자열을 정보가 동시에 이용 가능하다는 뜻으로 취급하지 않는다.
2. **F03/F05:** 필수 현금 데이터 검증을 선정 명령과 역사적 수익 평가 경로에 적용한다.
3. **F06/F07:** 조합의 실제 노출 자산만으로 평가 기간을 정하고 초기 비용 측정 기준을 정리한다.
4. **F08/F09/F10:** 입력 검증과 결측 정책, 사용자 설명을 일관되게 만든다.

외부 시세·상품명·실제 증권사 계좌 목록·호가·주문 체결은 확인하지 않았다. 한국 2025-03 이후 데이터의 외부 대조 부재는 프로젝트에 명시된 한계이며 이번 리뷰로 해결하지 않았다. 신호 기록의 raw 입력 스냅샷/해시 부족, 일반 CLI 주간 이력의 수동 경로 분리, 워크포워드 경계 추가 비용 미반영도 설계 한계로 남는다.

모든 과거 버그·모든 옵션 조합·모든 실패 시나리오를 소진한 검토는 아니다. 정상 입력 경로의 정적 검토와 전체 기존 테스트, 위 합성 경계 재현 및 고정 로컬 자료 실행을 수행했다. 외부 종목 사실을 코드 내부에서 확정하지 않았고 추가 통합 배분·주문 기능을 필수 과제로 제안하지 않았다.

## 재현 방법

프로젝트 루트에서 실행한다. 아래 합성 예제는 네트워크를 쓰지 않는다. CLI 실험은 출력 경로를 반드시 임시 경로로 지정한다. 기존 결과를 그대로 덮어쓰는 명령은 리뷰 재현에 사용하지 않는다.

### F01 해외 직접 필터

```python
import numpy as np, pandas as pd
from etf_momentum.backtest import BacktestConfig, select_holdings
d = pd.bdate_range('2020-01-01', periods=350)
p = pd.DataFrame({'069500.KS':100*1.001**np.arange(350),
                  'SPY':100., 'A':100*1.002**np.arange(350)}, index=d)
r = pd.DataFrame({'ticker':['A'], 'eligible':[True]})
c = BacktestConfig(top_n=1, benchmark='069500.KS', cash_ticker=None,
                   risk_on_benchmark=True, risk_on_tickers=('SPY',))
p.loc[d[-1], 'SPY'] = 400.
print(select_holdings(r,p,d[-1],c,observed=p.notna()))  # risk_on True
p.loc[d[-1], 'SPY'] = 100.
print(select_holdings(r,p,d[-1],c,observed=p.notna()))  # risk_on False
```

### F02 프록시 관측, F06 0 비중, F07 초기 비용

```python
from types import SimpleNamespace
from etf_momentum import cli
from etf_momentum.data import splice_proxies
from etf_momentum.signals import rank_momentum
from etf_momentum.portfolio import PortfolioConfig, run_portfolio
from etf_momentum.backtest import summarize_performance
d = pd.bdate_range('2018-01-01','2020-03-02')
raw = pd.DataFrame({'LOCAL':np.nan},index=d); raw.iloc[-1,0] = 200
u = pd.bdate_range('2017-12-29','2020-01-15')
proxy = pd.Series(100*np.exp(np.arange(len(u))*.001),index=u)
fx = pd.Series(1000.,index=u)
filled, starts = splice_proxies(raw,{'LOCAL':proxy},fx)
observed = cli._observations(raw,SimpleNamespace(proxy_starts=starts),filled)
print(rank_momentum(filled,pd.Timestamp('2020-02-28'),observed=observed))

d = pd.bdate_range('2024-01-01',periods=6)
r = pd.DataFrame({'A':[0,.1,.1,.1,.1,.1],
                  'B':[np.nan,np.nan,0,0,np.nan,np.nan]},index=d)
print(run_portfolio(r,PortfolioConfig({'A':1}))[0])
print(run_portfolio(r,PortfolioConfig({'A':1,'B':0}))[0])
e,h = run_portfolio(pd.DataFrame({'A':[0.,0.]},index=d[:2]),
                    PortfolioConfig({'A':1},rebalance_cost_bps=100))
print(e.tolist(), h.cost.tolist())
print(summarize_performance((e/e.iloc[0]).to_frame('portfolio')))
```

### F03/F08 CLI 다운로드 계측

```python
import contextlib, io, sys
from pathlib import Path
from unittest.mock import patch
from etf_momentum import cli
root = Path('/private/tmp/etf-review-reproduce'); root.mkdir(exist_ok=True)
d = pd.bdate_range(end='2026-10-06',periods=700); n = np.arange(700)
p = pd.DataFrame({'A':100*1.002**n, 'BENCH':100*1.0005**n,
                  'CASH':100*1.0001**n},index=d)
u = root/'u.csv'
u.write_text('ticker,name,category,enabled\nA,Alpha,X,1\nBENCH,Bench,X,1\n')
calls = []
def downloaded(tickers,**kw):
    calls.append(list(tickers))
    return p[[t for t in tickers if t in p]]
argv = ['etf-momentum','rank','--universe',str(u),'--benchmark','BENCH',
        '--cash','MISSING','--end','2026-10-07','--output-dir',str(root/'missing')]
with patch.object(cli,'download_prices',downloaded), patch.object(sys,'argv',argv):
    cli.main()
print(calls, pd.read_csv(root/'missing/latest_selection.csv'))
# F08: 정상 현금 자료와 후보 B를 추가하고 잘못된 가중치로 실행한다.
p['B'] = 100*1.001**n
u.write_text('ticker,name,category,enabled\nA,Alpha,X,1\nB,Beta,X,1\nCASH,Cash,Cash,1\n')
argv[argv.index('--cash')+1] = 'CASH'
argv[argv.index('--output-dir')+1] = str(root/'nanweights')
argv += ['--weights','nan,0,1']
with patch.object(cli,'download_prices',downloaded), patch.object(sys,'argv',argv):
    cli.main()
print(pd.read_csv(root/'nanweights/latest_ranking.csv').weighted_momentum)
print(pd.read_csv(root/'nanweights/latest_selection.csv'))
```

### F04/F05/F09/F10 계산 경계

```python
from etf_momentum.signals import MomentumConfig, rank_momentum
from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.walkforward import walk_forward
from etf_momentum import cli
d = pd.to_datetime(['2020-01-01','2020-01-02','2020-01-03',
                    '2020-01-06','2020-01-07','2020-01-09'])
p = pd.DataFrame({'A':[100.,110.,120.,130.,140.,150.]},index=d)
c = MomentumConfig(lookbacks=(1,2,3),skip_days=0,sma_window=2,volatility_window=2)
print(rank_momentum(p,config=c).weighted_momentum)
mix = p.reindex(d.union(pd.to_datetime(['2020-01-08']))); mix['REFERENCE']=100.
print(rank_momentum(mix.ffill(),config=c,candidate_tickers=['A']).weighted_momentum)

d = pd.bdate_range(end='2026-10-06',periods=700); n = np.arange(700)
p = pd.DataFrame({'BENCH':100*1.0005**n,'CASH':100*1.0001**n},index=d)
p.loc[d[400]:d[500],'CASH'] = np.nan
cli._require_fresh(p.notna(),{'benchmark':'BENCH','cash':'CASH'})
print(run_backtest(p.ffill(),BacktestConfig(top_n=1,benchmark='BENCH',
      cash_ticker='CASH',candidate_tickers=('NOPE',)),observed=p.notna())[0])

d = pd.bdate_range('2020-01-01','2022-12-30')
r = pd.DataFrame({'WIN':.002,'OTHER':.0001},index=d)
r.loc['2021-01-01':'2021-03-31','WIN'] = np.nan
oos, windows = walk_forward(r,train_years=1,metric='cagr')
print(windows.iloc[0], (oos.loc['2021-01-01':'2021-03-31']==0).sum())

d = pd.bdate_range(end='2026-10-06',periods=700)
p = pd.DataFrame({'A':100*1.002**np.arange(700)},index=d)
r = rank_momentum(p,config=MomentumConfig(min_weighted_momentum=.5,
                      min_trend_strength=.3),observed=p.notna()).iloc[0]
print(r.weighted_momentum,r.trend_strength_vs_sma,cli._exclusion_reason(r))
```

위 스크립트 블록은 공통 `numpy as np`, `pandas as pd` import를 포함해 실행한다. 실행 당시 전체 재현 스크립트는 임시 폴더의 `main_checks.py`, `additional_checks.py`, `cached_checks.py`이고 JSON 결과·CLI 로그도 그 아래에 있다. 임시 자료는 장기 보존을 보장하지 않으므로 핵심 입력과 기대/실제 값은 본문에 함께 남겼다.


## 입력 파일 해시

리뷰 시점의 자료 식별자다. 파일을 보존한다는 뜻은 아니며, 이후 같은 이름의 캐시가 바뀌었는지 비교하는 데 사용한다.

| 파일 | SHA-256 |
|---|---|
| `data/prices_raw_069500_KS_153130_KS_KRW=X_SPY_2010-01-01_2026-10-07.csv` | `fa7a0d00fc0dfd396e631691bb85dd4314eb406a48315b6376a7b036e9b5244c` |
| `data/prices_raw_069500_KS_2010-01-01_2026-10-07.csv` | `47a6c34cdb5d793dec154ee944f1f07fef6639379d783734584738fa1b792019` |
| `data/prices_raw_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv` | `cb9c6320b81d5f0f7c359923abeb5986ec4f352a9f15274c58d85bf23f2fba0c` |
| `data/prices_raw_BIL_CIBR_DIA_GLD_IBB_ICLN_IGV_ITA_IWM_IYT_KRE_QQQ_SMH_SOXX_SPY_TAN_URA_VNQ_XBI_XLB_XLC_XLE_XLF_XLI_XLK_XLP_XLRE_XLU_XLV_XLY_XME_XRT_2010-01-01_2026-10-07.csv` | `4131f72107ab3c9f3fe3fc26f7ad4d85da497f82f17ba5b17ae4e1d5a558dd2f` |
| `data/prices_raw_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv` | `7aca8716bd62d88f2092b78f11ba7cdbcfc7d67b341f564e895142e3f86453c3` |
| `data/prices_raw_KRW=X_2010-01-01_2026-10-07.csv` | `fe32ed2511a9ad520bd920f723e9c9c1c4136b99264a8d71d002f70c625fab8a` |
| `data/prices_raw_SPY_2010-01-01_2026-10-07.csv` | `a9404c87bc09eaec42cb89140ee0c7be6883fb55ece9821372d402ae0387f83b` |
| `data/traded_value_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv` | `f48d2ea1e80d2ff2703b551a832e32457c46edc2a060189018b589fdf3378d71` |
| `data/traded_value_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv` | `f7a1fb2eddc6c9d6ba9516b96410fe89745b9082d0fde74f924ce2d089d2f596` |
| `config/universe_kr_etfs.csv` | `8bc3f5ec6878a575d112742210d9fc7ec2baab4ac25a27378e60d15ab28ac559` |
| `config/universe_kr_pension.csv` | `111c17b3a6980c3fab8a33f9de51495fc4f83deacdbb62171bae50bbb0a4bb6c` |
| `config/universe_us_sectors.csv` | `759d061f56faa692d89c12d73a6d93aaafc64968dc987271a646fe1b3f4f2f2b` |
| `config/strategies/pension.json` | `f366951f0e33bcdf726d6aa1ec4afd33b1c18f78702195ad7c071f81ccdf72a0` |
| `config/strategies/retirement.json` | `fd0d6648ab66585540e0a533f025ba70165336cec59226cac0d52c9595cdf42a` |
