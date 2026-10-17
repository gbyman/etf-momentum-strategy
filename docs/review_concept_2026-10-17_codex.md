# ETF 선정 컨셉과 현재 구현 대조 리뷰

검토일: 2026-10-17 (Asia/Seoul)  
대상 HEAD: `9315f284c7e11ae4ed953de856265aa7890df8ef`  
시작 작업 트리: clean. 현재 전체 구현·설정·테스트를 검토했으며 제품 변경은 하지 않았다.

**핵심 선정 구조는 컨셉에 맞게 구현되어 있다. 다만 데이터 누락 경계에서 선정의 신뢰성을 해치는 P2 문제 2건을 재현했다.** 가격 최신성에 대한 직전 G01 수정이 다시 깨진 것은 아니다. 거래대금과 월말 현금 대기 자산에 남은 별도 검증 공백이다. 전체 기존 테스트는 **374 passed in 27.13s**이며, 새 합성 재현은 이 스위트가 놓치는 조건을 다룬다.

## 1. 확인된 문제

### C17-01 — P2: 거래대금 표의 날짜 행이 빠지면 6거래일 지난 자료로 ETF를 선정한다

**위치:** `src/etf_momentum/signals.py:119`, `:235–250`, 특히 `:247–249`; `src/etf_momentum/cli.py:1245–1263`. `rank`/`weekly`/백테스트 → `rank_momentum` → `_average_traded_value` 경로다.

**기대 근거:** README의 유동성 필터는 선정일까지의 최근 63거래일 평균과, 마지막 거래대금 관측 후 5영업일 초과 시 유동성 미달을 설명한다. 아래 미국 사례의 6일은 일반 월~금 영업일과 실제 XNYS 세션 수가 같아 휴일 해석 차이도 없다.

**재현 조건:** 2025-09-30 선정에서 A의 가격은 당일까지 정상 관측되지만 거래대금의 마지막 관측은 09-22다. 거래대금 표에 09-23·24·25·26·29의 행이 없고 09-30에는 B만 관측된다. A의 과거 일 거래대금은 20억, 필터 기준은 10억이다. 가격과 거래대금은 별도 다운로드/캐시이므로 인덱스가 항상 같다는 보장은 없다.

`trailing_missing`은 남은 표에서 A의 마지막 결측 **1행**만 세고, `days_after_data`는 표의 마지막 행이 이미 09-30이어서 **0일**이다. 실제 A 관측 이후 6세션이라는 정보가 사라진다. `sessions`를 받은 `rank_momentum`도 이를 거래대금 계산에 전달하지 않는다. CLI의 경고는 전체 표의 마지막 날짜와 누락 열만 확인하므로 이 사례를 잡지 못한다.

| 동일 원자료의 표현 | A 평균 거래대금 | A liquid | 월말 선정 |
|---|---:|---|---|
| 빠진 날짜 행 없이 압축된 표 | 2,000,000,000 | True | **A** |
| 같은 날짜들을 NaN 행으로 명시한 대조군 | NaN | False | **B** |

합성 가격 `A=100×exp(.002n)`, `B=100×exp(.001n)`, `SPY=100×exp(.0005n)`, BIL=100과 기본 모멘텀 설정으로 직접 함수 및 실제 `rank --end 2025-10-08 --top-n 1 --risk-on-benchmark --min-avg-traded-value 1e9`를 실행했다. 공급자 반환만 mock했고 이후 CLI·선정 경로는 실제 코드다. 두 실행의 선택 CSV도 위와 같았다. 모든 날짜 행이 있는 대조군에서 5세션은 통과하고 6·8세션은 거부된다.

**영향:** 가격은 신선하지만 거래대금은 오래된 ETF가 유동성 기준을 통과한다. 최근 평균의 63행도 실제 63거래일보다 긴 기간으로 늘어날 수 있다. 실제 운영 캐시에서 이 상황이 발생했다는 주장은 아니다.

**수정 방향:** 거래대금의 종목별 마지막 관측일과 평가일의 세션 거리로 최신성을 검사하고, 평균 창·최소 관측 개수도 일관된 평가 달력 위에서 계산한다. NaN을 0이나 이전 값으로 채우지는 않는다. 가격·거래대금 인덱스 불일치, 전체 행 누락, 5/6 경계, KR/US 휴장을 각각 회귀 테스트로 고정한다.

### C17-02 — P2: 조회 전에 복구된 현금 ETF를 월말 당시에도 유효했던 대기 자산처럼 출력한다

**위치:** `src/etf_momentum/cli.py:270–272`, `:392–412`, `:430–434`; `src/etf_momentum/backtest.py:338–355`, `:529–545` 보유 공백 검사. `_require_fresh`는 현재 자료의 마지막 날만 검사하고, `_month_end_selection`은 월말 후보·시장 필터는 검사하지만 현금 ETF의 당시 관측 상태는 확인하지 않는다.

**재현 조건:** BIL이 09-22까지 관측된 뒤 09-23~30 결측이고 10-01부터 복구된다. 가격표의 날짜는 빠지지 않았다. 09-30 SPY가 SMA 아래로 내려가 해당 월말에만 전액 현금이 필요하다. 그 전에는 상승 중인 A를 보유하도록 구성해 이전 현금 공백 때문에 백테스트가 먼저 실패하는 오탐을 피했다.

| 항목 | 실제 결과 |
|---|---|
| BIL의 09-30 기준 신선도 | False: 6 XNYS 세션 경과 |
| BIL의 마지막 조회일 10-07 기준 신선도 | True |
| `rank --end 2025-10-08 --top-n 1 --risk-on-benchmark` | `as_of=2025-09-30, status=cash, ticker=BIL, slots=1` 정상 저장 |
| 동일 입력 백테스트 | `StaleDataError`: 10-01~10-07 보유 구간의 BIL 공백 검사에서 중단 |

재개일에도 이전 관측 사이의 장기 공백을 검사하는 백테스트의 동작은 의도된 보호다. 반면 rank는 나중에 복구됐다는 이유로 과거 현금 선정에 경고나 거부가 없다. 독립 리뷰어도 다른 입력(후보 A의 9월 급락과 BIL의 09-16~10-01 결측)으로 같은 문제를 발견했고 주 리뷰어가 별도로 재현했다.

**명세 해석:** 원자료 설계 표의 필수 현금 사전 검사는 명시적으로 **실행 마지막 날 기준**이다. 따라서 그 좁은 구현 명세를 어겼다고 주장하지 않는다. README의 “같은 월말 실제 선택될 ETF” 및 공통 규칙 취지에 비추어, 선택일의 대기 자산 검증이 빠진 **설계·명령 일관성의 경계 문제**다. 이 재현은 전략 파일 없는 rank이므로 실제 `signals.csv` 기록 오염까지 실측했다고 주장하지 않는다. 전략 경로에서는 같은 cash 행이 기록 함수에 전달되는 구조다.

**영향/수정 방향:** 해당 시점 데이터로 검증되지 않은 대기 자산을 정상 월말 결과로 남길 수 있다. 현금 슬롯이 생길 때 월말까지의 관측표로 현금 최신성을 확인하고, 실패 시 결과·신호 기록 전에 명확히 중단한다. `cash=None`의 미투자 현금과 현금 ETF 상장 전의 기존 정책은 별도로 유지해야 한다. 월말 오래됨→현재 복구, 정상 현금, 후보 부족/risk-off, 미투자 현금 사례를 함께 검증한다.

## 2. 컨셉 대비 구현 상태

판정 기준은 README, CLAUDE.md, 최신 전략 JSON이다. 과거 개선 제안이나 설계의 초기 v1/v2 설정을 최신 v3보다 우선하지 않았다. 목적은 **보유할 모멘텀 ETF 선정**이며, 실제 금액·핵심/보조 비율·주문은 사용자가 정한다.

| 컨셉/요구 | 현재 구현과 판단 |
|---|---|
| 3/6/12개월 상대 모멘텀 | `signals.py`에서 `P[t-21]/P[t-21-L]-1`, L=63/126/252, 가중치 .3/.3/.4. 독립 수식 일치. 일반적인 t기준 “12-1”과 동일하다고 설명하면 안 됨. |
| SMA 위·양의 모멘텀·유동성 통과 | SMA200, 점수>0, 평균 거래대금, 계산 가능한 변동성, 실제 가격 관측 최신성을 조합. 경계는 엄격한 `>`이고 유동성 금액은 `>=`. C17-01 예외 존재. |
| 시장 상태 확인 후 상위 N개 선정 | `select_holdings`가 rank·backtest에 공유됨. 복수 필터는 OR, 오래된 필터는 오류. 후보 부족은 빈 슬롯 현금. C17-02 예외 존재. |
| 전략 파일로 명령 간 규칙 고정 | `strategy.py`와 `_apply_strategy`가 rank/weekly/backtest에 공통 적용. 선정 CLI override 거부, 전략 내용+유니버스 바이트 해시. 기존 일관성 테스트 통과. |
| 연금 계좌별 후보 제한 | 기본 v3는 788종, benchmark·cash 제외 후 pension 786종/retirement 726종. 이름 기반 합성·선물 제외는 명시된 근사이며 증권사 적격성 증명이 아님. |
| 그룹 제한·중복 노출 정책 | 최신 v3는 `max_per_group=0`, top4. 같은 지수/테마 동시 선정은 사용자의 기존 결정에 부합. 그룹 기능 미적용을 버그로 보지 않음. |
| 주간 관찰·월간 선택 구분 | weekly는 현재 랭킹 변화, rank는 현재 순위와 바로 전 달 월말 선택을 구분. 월말은 실제 벤치마크 관측일과 거래소 세션으로 확인. |
| 선정 이유·보유 대비 변경 | rank가 selected/excluded/group_limit/cash와 지표·이유, 유지/편입/제외를 제공. 수량·주문금액은 계산하지 않아 선정 전용 컨셉과 부합. |
| 원자료와 계산용 채움 구분 | 원자료 캐시→ffill 계산 가격, raw.notna 관측표. 실제 동일 가격과 관측 결측 구분. 0/음수/무한 가격 정리, 필수 자산·보유 공백 검사 존재. |
| 해외 프록시의 정보 시점 | 한국 D일에는 D보다 이른 proxy/FX만 사용. 미래 가격 변경 대조와 별도 proxy 산술 검산 통과. 상장 전 대체 유동성은 실제 국내 유동성의 증명이 아님. |
| 선정 이후 평가 기능 | buy-and-hold 드리프트, next_close 체결, 비용, 기존 계좌 상한은 연구 평가 기능. 추가 배분·주문 시스템을 필수 보완으로 요구하지 않음. |
| 기록 보존 | signals는 append-only·결과 변경 시 revision, 과거 조회 무기록. weekly는 같은 설정 해시끼리 비교. 기존 기록 테스트 통과. 가격 입력 스냅샷 보존은 아래 별도 한계. |

기본 pension/retirement의 현재 `config_hash`는 각각 `559a153fc6ea` / `506cf0bb4576`이다. 연구용 weighting/target-vol 등의 옵션은 선정과 비중 평가를 구분하는 기존 설계다.

## 3. 직접 계산·실제 로컬 자료 검증

### 합성 데이터와 시간축

- 기본 모멘텀: A=100×exp(.002n), 기본 63/126/252·skip21에서 독립 기대 **0.3883952069331815**, 실제 동일. 미래 구간 A를 1e9로 바꿔도 과거 순위 DataFrame 동일.
- A/B 각 50%, A +10% 후 B +10%: buy-and-hold 기대·실제 성장 **1.1**, daily-rebalanced **1.1025**. 두 방식의 수익을 혼동하지 않음.
- 09-30 신호 뒤 10-01 A +10%: same_close는 10-01에 **1.1**, next_close는 **1.0**. 후자는 10-01 종가 체결 전 수익을 얻지 않음.
- 마지막 실제 가격 관측 후 5/6/8세션 신선도는 **True/False/False**. 실제 같은 가격 관측도 fresh로 인정. 독립 리뷰어가 그룹 제한·빈 슬롯과 KRX 2026-09-30 월말도 확인.
- proxy와 FX의 같은 날짜 값을 크게 바꾼 예제에서 한국 날짜에 사용하는 환산 값 기대·실제 **[1000,4000,200]** 일치. 상장 전 두 행만 이전 관측으로 채웠고 마지막 200은 실제 상장 가격.
- walk-forward 합성 대조: 2021년 이후 B 수익을 바꿔도 2021년 첫 선택은 A 유지, 그 결과가 학습 자료가 된 2022년 선택만 B로 변경. 전체 연구 그리드의 재실행은 아님.

### 60종 고정 캐시로 명령 대조

`--end 2026-10-07`(미포함), 가격/거래대금 60종 캐시를 읽기만 했다. `config/universe_kr_pension.csv`, benchmark069500.KS·cash153130.KS, min-avg-traded-value=1e9, top4, 그룹 제한 없음, KODEX200 필터를 적용했다. **proxy는 끔**이다. 기본 전체 788종 전략의 실행 결과라고 해석하면 안 된다.

- pension/retirement 각각 현재 rank와 weekly 랭킹 DataFrame 동일.
- 두 계좌의 2026-09-30 선택: **139260.KS, 091230.KS, 091160.KS, 292150.KS**.
- 각 계좌 same_close/next_close 백테스트의 같은 신호일 위험자산 목록도 일치. 체결일은 각각 09-30 / 10-01, 각 실행 23개 보유 구간.
- 표준 `account_candidates`로 benchmark·cash를 제외한 58개 연금 후보 및 실제 `raw.ffill()`을 사용한 모멘텀 검산: 139260.KS 기대·실제 **1.3268633006764898**. 이전 리뷰의 단순 스칼라 하네스와 검증 범위를 구분한다.
- 저장된 60종 walk-forward 곡선의 2016-02-29~2025-02-28 통계만 독립 재계산: CAGR **0.11552666352395513**, MDD **-0.16355688142522617**, Sharpe **0.7558112241895422**로 현재 요약 함수와 일치. 이는 기존 곡선의 산술 검증이며 그 곡선을 만든 리비전·입력·전체 연구를 재승인한 것이 아니다.

입력 SHA-256:

| 파일 (`data/`, 마지막 항목은 `results/walkforward/.../`) | SHA-256 |
|---|---|
| `prices_raw_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv` | `0709b2ccf6295ef45c9859b77e0b65018d7e0f2329d2d756561eef82c980dc1f` |
| `traded_value_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv` | `f48d2ea1e80d2ff2703b551a832e32457c46edc2a060189018b589fdf3378d71` |
| `kr_pension_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv` | `cfa5191430d2e2886deddb63ddc833691778d6530be2c9f05c05b8392a7b4753` |

## 4. 과거 수정 재확인과 테스트

| 과거 항목 | 현재 판정과 이번 근거 |
|---|---|
| G01 가격 최신성의 세션 누락 및 하위 전달 | 해결 유지. 현재 5/6/8 합성 검산, 미래 구간 대조, 기존 `test_review_2026_10_13.py`·`test_g01_selection_sessions.py` 전체 스위트 통과. C17-01은 별도 거래대금 경로. |
| G02 Naver 실패 재시도 병합/잔여 파일 | 해결 유지. `_retry_missing`의 완료 캐시 확인·잔여 파일 삭제를 현재 코드에서 확인했고 실패 보존·retry 잔여물 회귀 테스트 통과. 이번 작업에서 공급자 실패 시나리오를 새로 모두 작성한 것은 아님. |
| G03 최신/고정 부분 다운로드 문서 충돌 | 해결 유지. README가 최신 미캐시와 고정 부분 저장/누락 재시도를 구분. |
| 과거 전체 결측 보유 공백·invalid 가격 등 | 해당 기존 회귀 테스트는 통과. 과거 모든 재현을 새로 독립 작성했다고 주장하지 않음. |

실행 명령(프로젝트 루트):

```sh
env MPLCONFIGDIR=/private/tmp/etf-concept-review-20261017/mpl XDG_CACHE_HOME=/private/tmp/etf-concept-review-20261017/cache .venv/bin/python -m pytest -q
```

결과 **374 passed in 27.13s**. 독립 리뷰어는 전략·기록·weekly·선정·달력 관련 59개를 별도 실행해 통과했다고 보고했다. 이 59개는 전체 스위트와 중복되므로 합산하지 않는다. 이번 재현은 기존 테스트·제품에 추가하지 않고 임시 스크립트로만 실행했다.

## 5. 설계 한계와 선택적 보완

다음은 위 두 결함과 구분한다.

1. **행 기반 지표 창:** 가격 최신성은 거래소 세션을 보지만 lookback/SMA/변동성은 남아 있는 가격 행을 센다. 여러 짧은 전체 패널 공백·워밍업 공백이 누적되면 실제 관측 기간이 의도한 거래일 창보다 길어진다. 이번 리뷰가 이력을 복원하지는 않는다.
2. **실제 사용 시점과 체결:** next_close는 구현되어 있지만 “월초 첫 주말 확인 후 첫 거래일”과 항상 같지는 않다. 예를 들어 월말이 주중이면 다음 거래일은 주말 확인보다 빠르다. 해당 운영 흐름의 성과를 주장하려면 그 지연을 별도 시나리오로 고정해야 한다.
3. **입력 증거 보존:** config_hash는 전략·유니버스 변경을 구별하나 가격·거래대금 원자료의 해시는 아니다. signals는 선택·코드·설정·최종 날짜를 남기지만 당시 원자료와 모든 지표/이유를 불변 스냅샷으로 보존하지는 않는다. 완전한 사후 재현의 별도 과제다.
4. **현재 유니버스의 역사 한계:** 상장폐지 ETF 부재, 현재 시점 그룹과 제한적인 프록시 때문에 성과 해석에 한계가 있다. 최신 기본값의 그룹 제한 해제와 계좌 이름 근사는 명시된 사용자 결정이므로 재차 결함으로 올리지 않는다.
5. **워크포워드 연결:** 학습만으로 선택하는 구조는 확인했지만 구간 경계 파라미터 교체 추가 비용·보유 상태의 완전한 연결은 모델링하지 않는다. README에 공개된 기존 한계다.
6. **최소 이력의 여유 1행:** 기본 모멘텀의 세 항·SMA·변동성은 274행에서도 계산 가능하지만 현재 guard는 275행을 요구한다(`signals.py:107`, backtest의 같은 최소 기준). 재현에서 계산값은 유한하나 ranking은 이력 부족 오류였다. 공통으로 적용되는 기존 보수적 정책이라 이번 두 P2와 별도로 둔다. 이 1행을 의도했는지 명시하고 경계 정책을 맞출 수 있다.
7. **과거 제안 문서:** `docs/next_improvements.md`의 상태 메모는 2026-10-07 기준이며 next_close를 미구현으로 적는다. 실제 현재 구현과 최신 README를 우선해야 한다. 초기 유니버스 설계의 v2/그룹1도 현재 v3/그룹0과 구분한다.

## 6. 범위와 제한

- 적용 스킬: `.agents/skills/etf-momentum-code-review/SKILL.md` 및 체크리스트/보고 형식. 독립 리뷰어 1명에게 요구·리비전·범위만 전달했고 주 리뷰어의 의심이나 예상 답은 전달하지 않았다. 모델별 Orca 구현 작업을 다시 실행한 것은 아니다.
- 읽은 구현: `signals`, `backtest`, `cli`, `strategy`, `data`, `naver`, `records`, `weekly`, `universe_builder`, `walkforward`, `portfolio`, `reporting`; 유니버스 생성·연구 표 스크립트 주요 경로, 전략/유니버스와 관련 설계·과거 리뷰.
- 환경: 프로젝트 `.venv` Python **3.14.3**, pandas **3.0.3**, NumPy **2.4.6**, pytest **9.0.3**, exchange-calendars **4.13.2**, yfinance **1.3.0**. 초기 파일 지문 수집에 쓴 시스템 python3(3.8.2)와 제품/테스트 실행 환경은 다르다.
- 전체 788종 기본 전략은 설정·후보·계좌 표시를 검토했다. 로컬 전체 거래대금 캐시가 없어 유동성 필터를 포함한 전체 실행은 미검증이다. 60종 재현으로 전체 기본 전략을 대체 검증하지 않는다.
- 현재 유니버스 메타에는 `csv_sha256`이 없어 CSV/메타의 해시 짝 검증은 불가했다. 새 writer는 이를 기록하지만 과거 산출물에 소급 적용되지 않는다. 로컬 검산 초기 하네스가 이 키를 가정해 실패한 뒤 누락을 명시하도록 보정했으며 제품 파일은 바꾸지 않았다.
- 외부 시세·상품명·거래소 휴장 공고·증권사 매수 가능 목록은 조회하지 않았다. 네트워크 없이 합성 자료·로컬 파일·설치된 달력을 사용했다. 새 전체 그리드/공식 연구 결과를 생성하지 않았다.
- 시작 시 추적 파일 125개의 SHA-256을 기록했고 보고서 작성 직전 모두 불변임을 확인했다. 운영 `data/`·`results/`에서 작업 시작 이후 수정된 파일도 없었다. 최종 재확인 기록은 임시 `final_integrity.json`에 남긴다. 제품·전략·테스트·기존 문서의 수정, 커밋·푸시는 하지 않았다.

현재 컨셉을 유지하면서 우선 해결할 부분은 **C17-01의 유동성 최신성**과 **C17-02의 월말 현금 검증**이다. 자동 주문이나 별도 배분 기능 추가가 필요한 상태라는 뜻은 아니다. 정상 계산의 일치나 테스트 통과를 실거래 적합성 전체의 승인으로 확대하지 않는다.

## 7. 재현 자료와 실행

임시 경로: `/private/tmp/etf-concept-review-20261017/`. `reproduce.py`(두 발견과 정상 대조), `boundaries.py`(세션·프록시·체결), `local_check.py`(로컬 CLI·통계), 각 `*_results.json`/`results.json` 및 출력 CSV·로그를 보존했다. 독립 리뷰어 자료는 하위 `independent/`다. 이 임시 경로는 장기 보존이 보장되지 않아 핵심 재현 코드 원문을 아래 부록에 포함한다.

프로젝트 루트에서 각 스크립트는 다음처럼 실행한다. 결과·캐시는 임시 경로로만 쓴다. `local_check.py`는 위 입력 파일이 필요하다.

```sh
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-concept-review-20261017/mpl XDG_CACHE_HOME=/private/tmp/etf-concept-review-20261017/cache .venv/bin/python /private/tmp/etf-concept-review-20261017/reproduce.py
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-concept-review-20261017/mpl XDG_CACHE_HOME=/private/tmp/etf-concept-review-20261017/cache .venv/bin/python /private/tmp/etf-concept-review-20261017/boundaries.py
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-concept-review-20261017/mpl XDG_CACHE_HOME=/private/tmp/etf-concept-review-20261017/cache .venv/bin/python /private/tmp/etf-concept-review-20261017/local_check.py
```

### 부록: reproduce.py

SHA-256: `cf0dbd5502850f7cad61ac1d70920abe922da27e098dd52db6ce64ebc2c1552d`

```python
from pathlib import Path
import contextlib,io,json,sys,importlib.metadata as md
from dataclasses import replace
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum import cli
from etf_momentum.signals import MomentumConfig,rank_momentum,_average_traded_value,observed_freshness
from etf_momentum.backtest import BacktestConfig,run_backtest,select_holdings,StaleDataError,_holding_period,summarize_performance
out=Path('/private/tmp/etf-concept-review-20261017');out.mkdir(exist_ok=True)
sessions=cli._exchange_sessions('SPY');idx=sessions[(sessions>='2024-01-01')&(sessions<='2025-10-07')]
n=np.arange(len(idx));prices=pd.DataFrame({'A':100*np.exp(.002*n),'B':100*np.exp(.001*n),'SPY':100*np.exp(.0005*n),'BIL':100.0},index=idx)
asof=pd.Timestamp('2025-09-30');cfg=MomentumConfig();obs=prices.notna()
r=rank_momentum(prices,asof,benchmark='SPY',config=cfg,candidate_tickers=['A','B'],observed=obs,sessions=sessions).set_index('ticker')
hist=prices.loc[:asof];v=hist.A.to_numpy();expected=sum(w*(v[-22]/v[-(k+22)]-1) for k,w in zip([63,126,252],[.3,.3,.4]))
future=prices.copy();future.loc[future.index>asof,'A']=1e9
rf=rank_momentum(future,asof,benchmark='SPY',config=cfg,candidate_tickers=['A','B'],observed=obs,sessions=sessions).set_index('ticker');pd.testing.assert_frame_equal(r,rf)
returns=pd.DataFrame({'A':[.1,0.0],'B':[0.0,.1]},index=pd.to_datetime(['2025-01-02','2025-01-03']));w=pd.Series({'A':.5,'B':.5});ret,endw=_holding_period(returns,w,'buy_and_hold');bh=float((1+ret).prod())
result={'environment':{'python':sys.version,**{k:md.version(k) for k in ['numpy','pandas','pytest','exchange-calendars','yfinance']}},'normal':{'momentum_expected':expected,'momentum_actual':float(r.loc['A','weighted_momentum']),'future_prefix_equal':True,'bh_growth_expected':1.1,'bh_growth_actual':bh,'dr_growth_expected':1.1025,'dr_growth_actual':float((1+_holding_period(returns,w,'daily_rebalanced')[0]).prod())}}
assert abs(expected-r.loc['A','weighted_momentum'])<1e-12 and abs(bh-1.1)<1e-12
# A still has fresh price data, but its last volume observation is six NYSE sessions old.
last=pd.Timestamp('2025-09-22');tv_idx=idx[(idx<=last)|(idx>=asof)];tv=pd.DataFrame(2e9,index=tv_idx,columns=prices.columns);tv.loc[tv.index>last,'A']=np.nan
liqcfg=replace(cfg,min_avg_traded_value=1e9)
def pick(value):
 rank=rank_momentum(prices,asof,benchmark='SPY',config=liqcfg,candidate_tickers=['A','B'],traded_value=value,observed=obs,sessions=sessions)
 chosen=select_holdings(rank,prices,asof,BacktestConfig(top_n=1,benchmark='SPY',cash_ticker='BIL',momentum=liqcfg),observed=obs,sessions=sessions)
 return {'avg_A':None if pd.isna(rank.set_index('ticker').loc['A','avg_traded_value']) else float(rank.set_index('ticker').loc['A','avg_traded_value']),'liquid_A':bool(rank.set_index('ticker').loc['A','liquid']),'selected':chosen.tickers}
compressed=pick(tv);complete=pick(tv.reindex(idx))
result['liquidity_gap']={'as_of':str(asof.date()),'last_A_volume':str(last.date()),'actual_session_age':len(sessions[(sessions>last)&(sessions<=asof)]),'tv_missing_rows':list(map(str,idx[(idx>last)&(idx<asof)].date)),'compressed_input':compressed,'explicit_missing_rows_control':complete}
assert compressed['selected']==['A'] and complete['selected']==['B']
# Exercise real rank CLI with provider-return frames mocked, preserving production selection path.
uni_liq=out/'liquidity_universe.csv';pd.DataFrame({'ticker':['A','B','SPY','BIL'],'name':['A','B','SPY','BIL'],'category':'test','enabled':1}).to_csv(uni_liq,index=False)
cli_liq={}
for label,volume in [('compressed',tv),('explicit_rows',tv.reindex(idx))]:
 args_liq=['etf-momentum','rank','--universe',str(uni_liq),'--benchmark','SPY','--cash','BIL','--end','2025-10-08','--top-n','1','--risk-on-benchmark','--min-avg-traded-value','1e9','--output-dir',str(out/('liq_'+label)),'--cache-dir',str(out/'cache_unused')]
 log_liq=io.StringIO()
 with patch.object(cli,'download_prices',return_value=prices),patch.object(cli,'download_traded_value',return_value=volume),patch.object(sys,'argv',args_liq),contextlib.redirect_stdout(log_liq):cli.main()
 (out/('liq_'+label+'.log')).write_text(log_liq.getvalue());table=pd.read_csv(out/('liq_'+label)/'latest_selection.csv');cli_liq[label]=table.loc[table.status=='selected','ticker'].tolist()
result['liquidity_gap']['real_rank_cli']=cli_liq
assert cli_liq=={'compressed':['A'],'explicit_rows':['B']}

# Cash was observed earlier, stale at September signal, then recovered before October lookup.
raw=prices.copy();raw.loc[(raw.index>last)&(raw.index<=asof),'BIL']=np.nan
raw.loc[raw.index==asof,'SPY']=1.0
# Keep the market risk-off in October; all closes are genuine.
raw.loc[raw.index>asof,'SPY']=1.0
uni=out/'cash_universe.csv';pd.DataFrame({'ticker':['A','B','SPY','BIL'],'name':['A','B','SPY','BIL'],'category':'test','enabled':1}).to_csv(uni,index=False)
args=['etf-momentum','rank','--universe',str(uni),'--benchmark','SPY','--cash','BIL','--end','2025-10-08','--top-n','1','--risk-on-benchmark','--output-dir',str(out/'cash_rank'),'--cache-dir',str(out/'cache_unused')]
log=io.StringIO()
with patch.object(cli,'download_prices',return_value=raw),patch.object(sys,'argv',args),contextlib.redirect_stdout(log):cli.main()
(out/'cash_rank.log').write_text(log.getvalue());sel=pd.read_csv(out/'cash_rank/latest_selection.csv');latestfresh=observed_freshness(raw.BIL.notna(),raw.index,sessions)[1];signalfresh=observed_freshness(raw.BIL.notna(),raw.loc[:asof].index,sessions)[1]
bt=BacktestConfig(top_n=1,benchmark='SPY',cash_ticker='BIL',candidate_tickers=('A','B'),risk_on_benchmark=True,momentum=cfg)
try:run_backtest(raw.ffill(),bt,observed=raw.notna(),sessions=sessions)
except StaleDataError as e:backtest={'error_type':type(e).__name__,'message':str(e)}
else:backtest={'completed':True}
result['cash_signal_gap']={'signal_date':str(sel.as_of.iloc[0]),'selection':sel[['status','ticker','slots']].fillna('').to_dict('records'),'cash_latest_fresh':bool(latestfresh),'cash_signal_fresh':bool(signalfresh),'backtest':backtest}
# Exact minimum arithmetic length versus current warm-up guard.
short=prices.iloc[:274]
try:rank_momentum(short,benchmark='SPY',config=cfg,candidate_tickers=['A'],observed=short.notna(),sessions=sessions)
except ValueError as e:guard=str(e)
else:guard='accepted'
a=short.A.to_numpy();values=[float(a[-22]/a[-(k+22)]-1) for k in [63,126,252]]
result['warmup_boundary']={'rows':len(short),'all_lookback_terms_computable':values,'sma200_computable':True,'vol63_computable':True,'ranking':guard}
(out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps(result,ensure_ascii=False,indent=2))
```

### 부록: boundaries.py

SHA-256: `30b9482923725552c871f50f06cd3022c990d552f740823fc9bb58c8e83d43b0`

```python
import json
from pathlib import Path
import numpy as np
import pandas as pd
from etf_momentum.cli import _exchange_sessions
from etf_momentum.data import splice_proxies
from etf_momentum.signals import observed_freshness,_average_traded_value,MomentumConfig
from etf_momentum.backtest import run_backtest,BacktestConfig
out=Path('/private/tmp/etf-concept-review-20261017');sessions=_exchange_sessions('SPY');idx=sessions[(sessions>='2024-01-01')&(sessions<='2025-09-30')];asof=idx[-1]
res={'freshness':{},'liquidity_explicit_rows':{}}
for age in [5,6,8]:
 last=idx[-(age+1)];mask=pd.Series(idx<=last,index=idx);res['freshness'][str(age)]=observed_freshness(mask,idx,sessions)[1]
 tv=pd.DataFrame({'A':np.where(idx<=last,2e9,np.nan)},index=idx);v=_average_traded_value(tv,asof,63).A;res['liquidity_explicit_rows'][str(age)]=None if pd.isna(v) else float(v)
assert res['freshness']=={'5':True,'6':False,'8':False}
# US D close and D FX are deliberately extreme: local D must use only D-1 or earlier.
localidx=pd.to_datetime(['2025-01-02','2025-01-03','2025-01-06']);frame=pd.DataFrame({'KR':[np.nan,np.nan,200.]},index=localidx);source=pd.Series([10.,20.,1000.,30.],index=pd.to_datetime(['2024-12-31','2025-01-02','2025-01-03','2025-01-06']));fx=pd.Series([100.,200.,10000.,300.],index=source.index)
spliced,_=splice_proxies(frame,{'KR':source},fx,scale=False);expected=[1000.,4000.,200.];assert np.allclose(spliced.KR,expected);res['proxy_previous_close']={'expected':expected,'actual':spliced.KR.tolist()}
# First signal is Sep 30; Oct 1 jump is earned only by same_close, not a next-close entry.
dates=pd.to_datetime(['2024-09-23','2024-09-24','2024-09-25','2024-09-26','2024-09-27','2024-09-30','2024-10-01','2024-10-02','2024-10-31','2024-11-01']);prices=pd.DataFrame({'A':[95,96,97,98,99,100,110,110,110,110],'SPY':100.,'BIL':100.},index=dates);momentum=MomentumConfig(lookbacks=(1,2,3),skip_days=0,sma_window=3,volatility_window=3)
res['execution']={}
for mode in ['same_close','next_close']:
 eq,pos,_=run_backtest(prices,BacktestConfig(top_n=1,cash_ticker='BIL',benchmark='SPY',momentum=momentum,candidate_tickers=('A',),execution=mode))
 actual=float(eq.loc['2024-10-01','strategy']);expected=1.1 if mode=='same_close' else 1.;assert abs(actual-expected)<1e-12;res['execution'][mode]={'oct1_expected':expected,'oct1_actual':actual,'first_trade':str(pos.trade_date.iloc[0].date())}
(out/'boundary_results.json').write_text(json.dumps(res,indent=2));print(json.dumps(res,indent=2))
```

### 부록: local_check.py

SHA-256: `151b1af6d42119f334defc9cf35c7db6cac718351ce6d6de52f2781025333333`

```python
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace
import contextlib,io,sys,json,hashlib
import pandas as pd
import numpy as np
from etf_momentum import cli
from etf_momentum.data import load_universe,account_candidates
from etf_momentum.strategy import load_strategy
from etf_momentum.signals import MomentumConfig,rank_momentum
from etf_momentum.backtest import summarize_performance
from etf_momentum.walkforward import walk_forward
out=Path('/private/tmp/etf-concept-review-20261017');root=Path.cwd();used={}
pricepath=root/'data/prices_raw_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv';tvpath=root/'data/traded_value_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv'
raw=pd.read_csv(pricepath,index_col=0,parse_dates=True);value=pd.read_csv(tvpath,index_col=0,parse_dates=True)
def local(f,path,tickers,start=None,end=None,raw=False,**kw):
 used[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
 frame=f[[t for t in tickers if t in f.columns]].copy()
 if start:frame=frame.loc[start:]
 if end:frame=frame.loc[frame.index<pd.Timestamp(end)]
 return frame if raw or path==tvpath else frame.ffill()
def price(tickers,**kw):return local(raw,pricepath,tickers,**kw)
def tv(tickers,**kw):return local(value,tvpath,tickers,**kw)
def run(name,parts):
 argv=['etf-momentum',*parts,'--cache-dir',str(out/'unused_cache'),'--output-dir',str(out/name)]
 log=io.StringIO()
 with patch.object(sys,'argv',argv),contextlib.redirect_stdout(log):cli.main()
 (out/f'{name}.log').write_text(log.getvalue());return out/name
res={'scope':'Local curated 60 universe, proxy OFF. Same parameters as v3 except universe/use_proxies; not verification of full default 788 strategy.'}
common=['--universe','config/universe_kr_pension.csv','--benchmark','069500.KS','--cash','153130.KS','--end','2026-10-07','--min-avg-traded-value','1e9']
with patch.object(cli,'download_prices',price),patch.object(cli,'download_traded_value',tv):
 for account in ['pension','retirement']:
  flags=[*common,'--account',account]
  rule=['--top-n','4','--max-per-group','0','--risk-on-benchmark']
  rp=run(account+'_rank',['rank',*flags,*rule,'--lookback-years','3'])
  wp=run(account+'_weekly',['weekly',*flags,'--lookback-years','3','--no-save-history'])
  ranking=pd.read_csv(rp/'latest_ranking.csv');weekly=pd.read_csv(wp/'latest_ranking.csv');pd.testing.assert_frame_equal(ranking,weekly)
  selection=pd.read_csv(rp/'latest_selection.csv');selected=selection.loc[selection.status=='selected','ticker'].tolist();day=selection.as_of.iloc[0]
  rows={}
  for execution in ['same_close','next_close']:
   bp=run(account+'_'+execution,['backtest',*flags,*rule,'--start','2023-10-01','--transaction-cost-bps','10','--execution',execution])
   positions=pd.read_csv(bp/'positions.csv');held=positions.loc[positions.rebalance_date==day,'tickers'].iloc[0].split(',')
   held=[t for t in held if t!='153130.KS'];assert set(held)==set(selected),(account,selected,held)
   rows[execution]={'same_signal_selection_equal':True,'positions':len(positions),'trade_date_at_signal':positions.loc[positions.rebalance_date==day,'trade_date'].iloc[0]}
  res[account]={'signal_date':day,'selected':selected,'rank_weekly_equal':True,'backtest':rows}
# Fixed local input direct formula, using standard candidate filtering and production ffill.
items=load_universe('config/universe_kr_pension.csv');candidates=account_candidates(items,'pension','069500.KS','153130.KS');asof=pd.Timestamp('2026-09-30')
rank=rank_momentum(raw.ffill(),asof,'069500.KS',MomentumConfig(min_avg_traded_value=1e9),candidates,value,raw.notna(),cli._exchange_sessions('069500.KS'))
ticker=rank.loc[rank.eligible,'ticker'].iloc[0];arr=raw.ffill().loc[:asof,ticker].to_numpy();expected=sum(w*(arr[-22]/arr[-(k+22)]-1) for w,k in zip([.3,.3,.4],[63,126,252]));actual=float(rank.set_index('ticker').loc[ticker,'weighted_momentum']);assert abs(expected-actual)<1e-12
res['local_formula']={'ticker':ticker,'as_of':str(asof.date()),'expected':expected,'actual':actual,'candidate_count':len(candidates)}
# Real stored curve, statistics only; no strategy/official results generation.
p=root/'results/walkforward/kr_pension_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv'
if p.exists():
 curve=pd.read_csv(p,index_col=0,parse_dates=True).walk_forward.dropna().loc[:'2025-02-28'];v=curve.to_numpy();dr=curve.pct_change().dropna();rf=raw['153130.KS'].ffill().pct_change().reindex(dr.index).fillna(0);ex=dr-rf
 expected={'cagr':float((v[-1]/v[0])**(365.25/(curve.index[-1]-curve.index[0]).days)-1),'max_drawdown':float((v/np.maximum.accumulate(v)-1).min()),'sharpe':float(ex.mean()/ex.std(ddof=1)*np.sqrt(252))};actual=summarize_performance(curve.to_frame('x'),risk_free=rf)
 assert all(abs(z-actual['x_'+k])<1e-12 for k,z in expected.items());res['stored_curve_statistics']={'scope':'Existing curve arithmetic only; generating revision not revalidated','start':str(curve.index[0].date()),'end':str(curve.index[-1].date()),'expected':expected,'actual':{k:actual['x_'+k] for k in expected}};used[str(p.relative_to(root))]=hashlib.sha256(p.read_bytes()).hexdigest()
# Train-only causal control.
dates=pd.bdate_range('2020-01-01','2022-12-30');ret=pd.DataFrame({'A':.001,'B':.0005},index=dates);o1,w1=walk_forward(ret,train_years=1,test_months=12,metric='cagr');changed=ret.copy();changed.loc['2021-01-01':,'B']=.01;o2,w2=walk_forward(changed,train_years=1,test_months=12,metric='cagr');assert w1.selected.iloc[0]==w2.selected.iloc[0]=='A';assert w2.selected.iloc[1]=='B';res['walkforward_future_control']={'first_selection_before':w1.selected.iloc[0],'first_selection_after_future_change':w2.selected.iloc[0],'second_selection_after':w2.selected.iloc[1]}
res['inputs_sha256']=used
res['full_universe_static']={}
u=pd.read_csv('config/universe_kr_all.csv');meta=json.loads(Path('config/universe_kr_all.meta.json').read_text());meta_hash=meta.get('csv_sha256');assert u.ticker.is_unique;assert (u.retirement_ok.eq(0)==u['name'].str.contains('합성|선물')).all();assert (u.risk_asset.eq(0)==u.ticker.eq('153130.KS')).all()
for acct in ['pension','retirement']:
 st=load_strategy(f'config/strategies/{acct}.json');res['full_universe_static'][acct]={'version':st.version,'hash':st.config_hash,'enabled':len(load_universe(st.universe)),'candidates':len(account_candidates(load_universe(st.universe),st.account,st.benchmark,st.cash)),'top_n':st.top_n,'max_per_group':st.max_per_group}
res['universe_csv_meta_hash_equal']=None if meta_hash is None else hashlib.sha256(Path('config/universe_kr_all.csv').read_bytes()).hexdigest()==meta_hash
res['universe_meta_has_csv_sha256']=meta_hash is not None
assert len(u)==meta['etfs']
(out/'local_results.json').write_text(json.dumps(res,ensure_ascii=False,indent=2));print(json.dumps(res,ensure_ascii=False,indent=2))
```
