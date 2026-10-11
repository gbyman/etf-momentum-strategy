# ETF Momentum 전체 코드 리뷰 및 계산·경계 조건 재현 — 2026-10-11, 재검토 3

현재 코드에서 **새로운 P2 결함 2건**을 재현했다. 공급 데이터의 0·음수·무한대 가격을 정상 관측으로 인정하는 문제와, 모든 티커가 동시에 결측인 거래일을 삭제해 보유 신선도 검사를 우회하는 문제다. 기본 선정·정상 입력의 계산은 독립 검산과 일치했고, 직전 보고서의 D01·D02는 해결됐다. 전체 기존 테스트는 **342 passed, 26.55초**다. 두 결함은 이 테스트 집합에 없는 입력 경계에서 발생한다.

## 1. 검토 기준과 범위

- 검토일: 2026-10-11, Asia/Seoul.
- 코드/실행 HEAD: `9bffdbc3fd2de0ffaf0dff8273d69dc2d1cb52b0`. 시작 작업 트리는 깨끗했다. 전체 현재 코드와 설정을 검토하고 과거 지적을 재실행했다. 변경분 확인 기준은 직전 검토 리비전 `03c9b88`이며, 전체 판정을 이 diff에 한정하지 않았다.
- Python 3.14.3, pandas 3.0.3, NumPy 2.4.6, pytest 9.0.3, exchange-calendars 4.13.2, yfinance 1.3.0. 설치된 `.venv`를 사용했다.
- 적용 지침: `.agents/skills/etf-momentum-code-review/SKILL.md`와 체크리스트·보고 형식, `CLAUDE.md`. 적용되는 `AGENTS.md`는 발견하지 못했다.
- 요구사항: `README.md`, 개념 개선 문서, 연구 요약, 이전 리뷰, `pyproject.toml`, 전략 JSON과 유니버스 CSV·메타데이터. 프로젝트 목적은 ETF 선정이다. 신규 주문·배분 시스템을 필수 요구사항으로 추가하지 않았다.
- 주요 기본 전략: v3 연금/퇴직연금, 788 ETF, 모멘텀 63/126/252일·최근 21일 제외·가중치 0.3/0.3/0.4, SMA 200, 변동성 63, 거래대금 10억원, top 4, 그룹 제한 0, 시장 필터 069500.KS, 현금 153130.KS, 상장 전 프록시 사용. 퇴직연금 위험자산 한도는 0.7이다.
- 독립 리뷰어 한 명에게 현재 리비전·요구사항·체크리스트만 전달했다. 주 리뷰어의 의심이나 예상 정답은 전달하지 않았다. 독립 리뷰어도 같은 두 문제를 찾았고, 주 리뷰어가 다운로드부터 실제 거래소 달력까지 직접 재현했다.
- 제품 코드·설정·기존 테스트·운영 캐시·운영 결과·기존 리뷰는 수정하지 않았다. 신규 보고서만 저장하며 커밋하지 않는다. 실험 출력은 `/private/tmp/etf-review-20261011-final`에 분리했다.

| 검토 영역 | 따라간 경로와 검증 |
|---|---|
| CLI·설정 | `cli.py`, `strategy.py`, 전략/포트폴리오 JSON: 인수 우선순위, 시장 혼합 제한, 날짜와 필수 티커, 출력 리비전 |
| 원자료·프록시 | `data.py`, `naver.py`: 조정종가, 관측표, 결측·캐시 버전·fallback, 분배금 경계, 프록시 가격·거래대금·환율 |
| 선정 | `signals.py`, `weekly.py`: 스킵과 룩백, 미래 자료 불변성, SMA·상대강도·유동성·신선도, 계좌 필터, 동점/중복/그룹 제한 |
| 평가 회계 | `backtest.py`: 신호/체결일, 두 체결 방식, 보유 방식, 비용·위험 한도·현금, 마지막 평가일, 수익곡선·지표 |
| 연구 | `walkforward.py`, `scripts/research_tables.py`: 훈련/테스트 분리, 미래 변경 불변성, 테스트 결측 거부, 소규모 그리드, 저장 곡선 독립 집계 |
| 포트폴리오 | `portfolio.py`: 슬리브 날짜 정렬, 환율 이전 관측, 미사용 슬리브, 현금 결측·첫 비용·잔여 현금 |
| 기록·유니버스 | `records.py`, `reporting.py`, `universe_builder.py`: 불완전 CSV, 중복, 원자적 출력 실패, 유니버스/그룹/메타 해시 |
| 테스트·문서 | 전체 pytest, 과거 회귀 항목, README·연구 문서 수치와 실행 결과 대조 |

## 2. 확인된 결함

### E01 — P2: 유효하지 않은 가격이 실제 관측으로 인정되어 평가곡선이 붕괴한다

**위치:** `src/etf_momentum/data.py:359–361` (`_clean_raw_prices`), `src/etf_momentum/cli.py:1460–1465` (`_observations`). 다운로드 → Close 추출 → 원자료 정리/캐시 → `notna()` 관측표 → 전방 채움 → `run_backtest` → 보유 수익 계산으로 전달된다. 원자료 정리는 NaN만 제거하고 `0`, 음수, `inf`를 검사하지 않는다. 세 값 모두 `notna()`가 참이므로 실제 종가로 취급된다.

**조건과 기대 동작:** 지원하는 미국 ETF 경로에서 공급자가 보유 중인 ETF의 조정종가를 잘못 반환하는 경우다. 양수이며 유한한 ETF 종가만 수익 계산에 사용할 수 있다. 기존의 관측 신선도·결측 중단 원칙에 따라 오류로 거부하거나 결측으로 표시해 정책을 적용해야 한다. 유한하지 않거나 음수인 평가곡선을 성공 결과로 반환해서는 안 된다.

**직접 재현:** 부록 `raw_boundaries.py`. 설치된 XNYS 달력의 2024-01-02~03-15 세션에 A=100×1.002^n, SPY=100×1.001^n, BIL=100을 생성했다. 룩백 (1,2,3), skip 0, SMA/변동성 2, top 1로 A를 보유시켰다. 2024-02-05 A 가격 하나를 변경했다. `_yf_download`의 반환값만 mock하고 `download_prices(raw=True, refresh=True)`의 실제 Close 추출·정리·캐시 경로를 실행했다. `auto_adjust=True`도 확인했다. 프록시나 네이버는 사용하지 않았다.

| 입력 가격 | observed | 실제 2월 5일 전략 가치 | NaN 곡선 행 | 마지막 가치 | 실행 결과 |
|---|---|---:|---:|---:|---|
| 0 | True | 0 | 17 | 0 | 성공 반환, accumulate 경고 |
| -1 | True | -0.009608278203311149 | 0 | 1.0638964689246049 | 성공 반환, 음수 가치 이후 다시 양수 |
| inf | True | inf | 28 | NaN | 성공 반환, 수치 경고 |

**영향:** 가격 오류가 신선한 관측으로 통과해 모멘텀·변동성·수익률과 성과 지표를 훼손할 수 있다. 이는 공급 데이터의 비정상 값 경계에서 확인된 결함이다. 검토한 정상 로컬 자료에 이러한 값이 실제 존재한다고 주장하지 않는다. 독립 리뷰어가 확인한 60/34 티커 캐시에는 0·무한대·중복 날짜가 없었다.

**수정 방향:** 다운로드와 캐시 읽기의 공통 원자료 경계에서 양수·유한성 검증을 적용한다. 필수 티커/보유 자산/후보 제외 정책과 연결하고, 평가곡선의 비정상 값도 명시적 실패로 처리한다. 값을 NaN으로 바꾸는 경우 E02처럼 거래일까지 삭제하지 않아야 한다. 신호 기준점·당일·보유 중·현금·벤치마크의 0/음수/inf 및 뒤이어 정상 가격이 돌아오는 경우를 회귀 검증한다.

### E02 — P2: 전체 티커 동시 결측일 삭제로 6거래일 관측 공백이 검사를 통과한다

**위치:** `src/etf_momentum/data.py:361`의 `.dropna(axis=0, how="all")`, `src/etf_momentum/backtest.py:471–476` (`_rows_since_observation`), `:487–504` (기간 말일 검사), `:507–523` (보유 신선도 검사). 캐시 읽기도 같은 정리를 거친다. 관측 나이는 축소된 가격 인덱스의 행 수로 계산되고 기간 말일 검사는 월중 공백을 검출하지 않는다.

**조건과 기대 동작:** 공급 자료에서 전 티커의 월중 거래일이 동시에 결측이다. 문서/구현의 보유 자산·벤치마크 정책은 5거래일을 초과한 실제 종가 공백을 중단한다. 알려진 거래소 세션의 결측은 관측표에서 False로 남아야 하며, 행 삭제로 공백 자체가 사라져서는 안 된다.

**직접 재현:** E01과 같은 양수 가격 패널에서 실제 NYSE 세션인 2024-02-05, 06, 07, 08, 09, 12의 A·SPY·BIL을 모두 NaN으로 설정했다. 실제 다운로드 정리 경로가 **6행을 삭제**했다. `_require_fresh`는 통과하고, 실제 세션 목록을 `sessions`에 전달한 백테스트도 성공했다(보유 구간 2개, 곡선 26행). 반대로 원래 세션 인덱스를 보존하고 같은 가격 전방 채움·관측표를 전달하면 다음 오류로 정상 중단했다.

```text
Held ticker(s) without a close for more than 5 trading days between 2024-02-01 and 2024-02-29: A, SPY
```

**영향:** 장기 보유 공백 검사가 우회되고 룩백·SMA·변동성의 거래일 개수가 압축된다. 여러 세션의 가격 변동을 한 행의 수익률로 취급할 수 있다. 미국 경로에는 네이버 보완이 없으며, 한국 경로도 보완 실패/참조 달력 공백과 축소된 캐시 읽기에서 같은 구조를 갖는다. 정상 네이버 보완이 모든 거래일을 복원한 경우까지 오류라고 판정하지 않는다.

**수정 방향:** 전체 결측 날짜도 원자료에 보존한다. 해당 시장의 세션 달력에 평가 가격/관측표를 정렬하고, 파일에 처음부터 없는 날짜도 복원해 False로 표시한다. 상장 전·실제 휴장·평가 범위 밖 날짜는 기존 정책과 구분한다. 5일/6일 경계, 한 티커/전체 티커, 월중/월말, KR/US, 실제 휴일, 프록시, 축소된 구버전 캐시를 검증해야 한다.

## 3. 직전 수정 및 과거 지적 재검증

현재 수정은 `data.py:230–251`의 구 네이버 보완 버전 fallback 거부와 `backtest.py:123–129`의 마지막 보유 구간 평가 종료일 반영이다. 과거 문서의 결론을 복사하지 않고 현재 코드로 아래 재현을 실행했다.

| 항목 | 현재 판정 | 현재 실행 증거 |
|---|---|---|
| D01 구 네이버 v1 캐시 재사용 | 해결 | v1 값 [90,81,90] + 빈 Yahoo 응답이 RuntimeError로 중단; 기존 캐시 바이트 보존. 유효 v2의 오래된 캐시 fallback은 기존 새 회귀 테스트에서 통과 |
| D02 마지막 보유 수익 누락 | 해결 | same_close/next_close × 마지막 관측 지연 1/3/5행 × 상승/하락 = 12건. 마지막 수익 +0.1011/-0.0991 일치, hold_until/보고 종료일 모두 2023-12-29. 6행 지연은 새 회귀 테스트에서 거부 |
| C01 분배금 보완 비율 | 해결 유지 | Yahoo [90,NaN,90]을 [90,90,90]으로 보완; 1월 3일 근사 조정률 0.11111111111111116 |
| C02 빈 참조 달력 캐시 고착 | 해결 유지 | 첫 빈 네이버 참조는 경고·캐시 미작성, 다음 호출 재시도 Yahoo 1회/네이버 2회 후 정상 보완 |
| C03 공통 시작일 전 현금 결측 | 해결 유지 | 2023년 현금 공백은 공통 평가 시작 2024-01-02 이전; 실제 곡선 안 현금 결측 0 |
| N01 다음 실제 벤치마크 종가 체결 | 해결 유지 | 2025-12-30 신호 → 2026-01-02 실제 체결 |
| N02 벤치마크 월중·월말 공백 | 해결 유지, E02는 별도 | 한 열만 11행 결측이면 중단. 3월 31일 벤치마크 미관측은 rank/backtest 모두 중단 |
| N03 프록시 거래대금 최신성 | 해결 유지 | 4월 28일 거래대금 이후 6월 20일 평가: 평균 거래대금 없음, liquid/eligible=False |
| N04 정확한 워밍업 경계 | 해결 유지 | 필요한 6행을 갖춘 1월 31일 rank와 backtest 첫 신호 일치 |
| N05 실제 사용하는 현금 공백 | 해결 유지 | 현금 노출 있는 포트폴리오 32일 공백 중단; 현금 노출 없는 설정 정상 |
| N06 유니버스 CSV/메타 교체 실패 | 해결 유지 | 두 번째 os.replace 실패 주입 시 기존 두 파일 모두 보존; builder 성공 출력 별도 확인 |
| R01 환율 신선도 | 해결 유지 | 5행 True, 6/10행 False |
| R02 불완전 주간 CSV | 해결 유지 | 16필드 기대에 15필드 입력은 append 거부; 파일 바이트 불변 |
| R03 0비중 슬리브 | 해결 유지 | 미사용 파일/환율 읽지 않고 같은 곡선 |
| R04 중복 후보 | 해결 유지 | CSV loader는 중복 거부, 직접 rank 후보 중복은 결과 1행 |
| F01/F04 혼합 시장 | 해결 유지 | KR/US 혼합 요청 명시적 오류 |
| F02 프록시 가격 중단 | 해결 유지 | 마지막 실제 관측이 오래되면 price_fresh/eligible=False |
| F03 필수 현금 누락 | 해결 유지 | 다운로드 요청에 현금 포함, 누락 시 명시적 오류 |
| F05 보유 현금 장기 공백 | 해결 유지 | CASH 5일 초과 공백 중단 |
| F06 미사용 슬리브 / F07 첫 비용 | 해결 유지 | 0비중 무시; 초기 곡선 [1,0.99], 종료가치 0.99로 비용 보존 |
| F08 NaN 비중 / F09 테스트 결측 | 해결 유지 | CLI 비중 오류 exit 2; 선택된 WIN의 테스트 수익 64개 누락 시 중단 |
| F10 탈락 사유 임계값 | 해결 유지 | 설정한 모멘텀 50%, SMA 대비 30%가 실제 탈락 설명에 반영 |

공동 참조 달력에서 하루 자체가 빠진 합성 사례도 실행했다. 현재 반환 날짜는 1월 2일/4일이며 3일이 복원되지 않는다. 이는 별도 새 번호를 추가하지 않고 E02의 같은 원인으로 분류했다.

## 4. 주요 계산 독립 검산

기대값은 작은 입력의 수식으로 계산했다. 구현 함수를 다시 호출한 값으로 기대값을 만들지 않았다. 부동소수점 허용 오차는 항목별 1e-12~1e-10이다. 저장 결과 집계와 일반 정상 계산을 구분했다.

| 계산/시간축 | 독립 기대값과 실제 결과 |
|---|---|
| 가중 모멘텀 | Σw×(P[t−21]/P[t−21−L]−1), L=(63,126,252), w=(.3,.3,.4). A=100×1.002^n에서 기대/실제 0.38792207964425396 |
| 미래 참조 | 평가일 뒤 A 가격을 100배 바꿔도 과거 rank 전체 DataFrame 불변 |
| buy_and_hold | A +10%→−10%, B 0%→0%, 초기 각 0.5: .5×1.1×.9+.5=0.995; 실제 0.9950000000000001 |
| daily_rebalanced | (1+.5×.1)×(1−.5×.1)=0.9975; 실제 0.9974999999999999 |
| 보유 비중 드리프트 | A 최종 .495/.995=0.49748743718592964, 실제 일치 |
| 비중 상한 | [.9,.1], cap .6: 재배분 후 총합 1. 후보 1개면 총합 .6, 잔여 현금 유지 |
| CAGR/MDD | 366일 동안 1→1.2: 1.2^(365.25/366)−1=0.19955175204308273. 10% 하락 MDD −0.10000000000000009 |
| 이전 종가 환율 | 가격 +10%, 사용 가능 이전 환율 +20%: 1.1×1.2−1=.32; 실제 .32000000000000006 |
| 같은 날 슬리브 결측 | 내부 NaN을 수익 0으로 삼지 않고 2020-01-02 위치 오류 |
| 거래소 월말 | 2026-09-30 실제 마지막 세션 누락은 오류 |
| 포트폴리오 첫 비용 | 초기자본 1, 비용 .01: 곡선 [1,.99], 최종 .99. 재정규화로 비용 사라지지 않음 |
| 워크포워드 미래 변경 | 2022년 이후 수익 변경에도 2021년 첫 훈련 선택 불변 |

독립 리뷰어는 다른 작은 입력으로도 모멘텀 0.38792207964425396, buy_and_hold 1.1/daily_rebalanced 1.1025, 환율 반영, 포트폴리오 초기 비용을 직접 검산했다. 같은 데이터/동일 함수에 의존한 중복 테스트를 독립 증거로 계산하지 않았다.

## 5. 로컬 실제 자료를 이용한 CLI 대조

외부 다운로드 없이 보존된 CSV를 읽도록 다운로드 함수만 mock했다. 그 이후 설정·선정·회계·출력은 실제 CLI 경로다. 이 실험은 캐시 읽기/네이버 새 다운로드 자체의 통합 검증이 아니다. 해당 경계는 별도 합성 재현으로 확인했다. 특히 788 원자료 전체를 네이버 v2로 다시 다운로드한 결과라고 해석해서는 안 된다.

- rank/weekly: 두 계좌, lookback-years 5, 종료일 `2026-10-07` 미포함. 동일 입력에서 전체 ranking CSV 일치.
- backtest: 두 계좌 × 두 체결 방식, 시작 `2024-01-01`, 종료 `2026-10-07` 미포함, 비용 10bp. 실제 곡선 2025-02-28~2026-10-06의 390행/보유 20구간.
- 최근 선택일 `2026-09-30`: 두 계좌 모두 `442580.KS, 367760.KS, 395270.KS, 363580.KS`. 월말 백테스트의 비현금 보유와 일치. 마지막을 제외한 모든 구간의 hold_until이 다음 trade_date와 일치.
- US compare: top_n 2/3 두 설정, 실제 결과 2행. US walkforward: 같은 작은 그리드, train 1년/test 12개월, 1개 테스트 윈도/그리드 2행.
- 기본 portfolio spec: 요약 18행, 곡선 4,123행. 슬리브는 저장된 과거 결과를 사용하므로 입력 슬리브의 코드 리비전은 현재 리비전과 별개로 메타에 보존된다.
- 11개 CLI 출력의 최상위 실행 메타를 확인: 모두 현재 HEAD, dirty=False. 저장 슬리브 내부 메타의 과거 commit을 현재 실행 commit으로 오인하지 않았다.

아래 성과는 짧은 리뷰 실험이다. 2025-03-31~2026-10-06 구간을 1로 다시 맞춰 계산했으며 공식 장기 연구 결과와 직접 비교할 수 없다.

| 계좌 | 체결 | CAGR | MDD |
|---|---|---:|---:|
| 연금 | same_close | 49.0214% | −55.6490% |
| 연금 | next_close | 54.4793% | −52.7607% |
| 퇴직연금 | same_close | 44.4943% | −40.4019% |
| 퇴직연금 | next_close | 44.0774% | −37.8349% |

## 6. 유니버스·그룹·연구 문서 실측

`universe_kr_all.csv`는 티커 중복 없는 788행, 연금 허용 788/퇴직연금 허용 728이다. 퇴직연금 제외는 현 문서의 상품명 `합성|선물` 규칙과 일치하며, 현금 153130.KS만 risk_asset=0이다. 증권사 매수 가능 목록과의 대조를 요구하지 않는 사용자 정책을 존중한다. 내부 분류 일치가 실제 계좌 매수 가능성을 보증하지는 않는다.

그룹 생성 입력은 보존된 `2025-04-08_latest` 788 가격 CSV다. 최근 수익률 252행(2025-09-23~2026-10-08)으로 재계산한 그룹 분할이 저장 CSV와 일치했다. 96그룹/485 ETF, 그룹 내 5,437쌍의 최저 상관계수 0.9000376584548322로 complete-linkage의 0.9 조건을 만족했다. 그룹 번호 문자열이 아니라 구성원 집합을 비교했다. 기본 전략의 max_per_group=0은 의도된 설정이다. 이 그룹 측정은 입력 캐시에 대한 검증이며 새 보완 시세를 이용한 재생성은 아니다.

유니버스 builder CLI는 mock 목록/가격의 전체 경로로 실행했다. 원목록 4/제외 1 기록, raw=True 다운로드, 필수 현금 누락 시 기존 파일 보존, 성공 CSV/메타 출력을 확인했다. 이는 실제 시장 상장 목록의 완전성을 검증한 실험은 아니다.

저장된 세 워크포워드 곡선의 CAGR/MDD/Sharpe를 NumPy 수식으로 다시 집계하고 `summarize_performance`와 1e-10 이내 일치 확인했다. CAGR은 실제 경과일 365.25, MDD는 누적최대 대비, Sharpe는 일별 초과수익 평균/표본표준편차×√252다. 위험 없는 수익은 153130.KS 종가 수익을 정렬하고 미정렬 날짜는 현재 집계 정책의 0을 따른다. 연구 표 생성 스크립트도 현금 자료만 로컬로 제공해 임시 Markdown까지 실행했다.

| 저장 연구 | 2016-02-29~2025-02-28 CAGR / MDD / Sharpe | 2016-02-29~2026-10-06 CAGR / MDD / Sharpe |
|---|---|---|
| 60 ETF | 11.5527% / −16.3557% / 0.755811 | 17.5448% / −43.3141% / 0.771108 |
| 788, 그룹 제한 1 | 9.1062% / −18.1409% / 0.571435 | 11.7896% / −53.3791% / 0.529850 |
| 788, 그룹 제한 0 | 9.3300% / −36.0385% / 0.534629 | 13.8207% / −54.5617% / 0.583441 |

주 구간 2,211행, 전체 구간 2,600행이다. 현 문서의 표시 수치와 일치한다. 저장 워크포워드는 훈련에서 top 3/4 등을 선택한 결과로, 현 고정 top 4 전략과 같지 않다. 곡선 재집계는 현재 코드로 전체 장기 전략을 다시 실행한 것이 아니다.

## 7. 테스트와 재현 실행법

실행한 전체 테스트 명령:

```sh
env MPLCONFIGDIR=/private/tmp/etf-review-20261011-final/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261011-final/cache .venv/bin/python -m pytest -q
```

결과: **342 passed in 26.55s**. 독립 리뷰어의 관련 테스트 80개는 이 집합과 겹치므로 422개로 합산하지 않는다. 아래 스크립트의 assert 성공은 정상 검산 또는 결함이 지금도 재현됨을 뜻하며 결함 수정 완료를 뜻하지 않는다.

부록의 각 Python 블록을 제목의 파일명으로 `/private/tmp/etf-review-20261011-final`에 저장한다. 프로젝트 루트에서 다음 준비 후 각각 실행한다. 제품 변경이나 외부 네트워크는 필요 없다. 로컬 자료 검증 스크립트는 아래 해시의 입력 파일을 필요로 한다. 실행 당시 설치된 패키지는 `src`를 참조했다. 다른 환경에서도 현재 소스를 명확하게 참조하도록 `PYTHONPATH=src`를 지정한다.

```sh
mkdir -p /private/tmp/etf-review-20261011-final/mpl /private/tmp/etf-review-20261011-final/cache
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-review-20261011-final/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261011-final/cache .venv/bin/python /private/tmp/etf-review-20261011-final/raw_boundaries.py
```

파일명을 바꾸어 나머지 스크립트도 실행한다. 임시 출력만 덮어쓴다. 보고서 생성 후 재실행하면 작업 트리가 dirty이므로 새 메타의 dirty=True는 정상이다. 이 문서의 실측 값은 문서 작성 전 깨끗한 HEAD에서 실행한 결과다.

## 8. 제한과 구현 판단

정상 입력에서 선정 컨셉은 구현과 일치했다. 계좌별 허용 여부, 모멘텀/추세/유동성/시장 필터, 현금 대체, 시간축과 기본 평가 회계는 검산과 명령 대조를 통과했다. 현재 먼저 보완할 부분은 E01의 유효 가격 검증과 E02의 거래 세션 보존이다. 둘 다 입력 경계 결함으로, 이번 측정만으로 정상 로컬 자료의 선정 결과가 잘못됐다고 결론내리지 않는다.

외부 Yahoo/네이버 시세·분배금·실제 상장 목록·계좌 매수 가능 여부는 조회하지 않았다. 제공된 캐시의 경제적 진실성과 상품 분류 정확성을 외부 기관으로 검증한 것은 아니다. 전체 2010년 시작 788 원자료가 로컬에 없어 장기 전체 그리드/워크포워드를 새로 재생성하지 않았다. 저장 곡선 수치 검산, bounded CLI 그리드, 합성 훈련/테스트 시점 검증으로 범위를 한정했다. 현재 선정 컨셉의 실행 정확성과 과거/향후 투자 성과의 타당성은 별개의 판단이다.

## 부록 A. 입력 파일 SHA-256

로컬 CLI 자료, 전략/포트폴리오 설정, 유니버스, 저장 연구 곡선의 지문이다.

| 파일 | SHA-256 |
|---|---|
| `config/portfolio_execution.json` | `37450ebc4455fbf5bf39f233958e0f25f4803bd6df2026320f7ab9bce980c434` |
| `config/portfolio_pension.json` | `a5da6a8368ff533e5914466ed742f3d550267ef971813100b4f1201b22b2f13f` |
| `config/portfolio_spy_kr.json` | `5d0434c49f44aa28f356d7d32d7c13508c6f89725baf3cd4a15eed0e982576c3` |
| `config/strategies/pension.json` | `f5bbf6f22f20f87596c0aab6d045a301ebc4b9f4b59b465494986627a438f95b` |
| `config/strategies/retirement.json` | `98a3cd96a425fafad92b33f022fa0e2c1a14c6d1b0e066a4460ca09f0b7604f5` |
| `config/universe_kr_all.csv` | `ee4047581bffc7095301436cef5bc5d6e88ac81de31165fa5f74b813fdc21f98` |
| `config/universe_kr_all.meta.json` | `98520bf072641611cc44fff36e09f266fc71963e4313a3e09b367e7e36e1c4fa` |
| `config/universe_kr_pension.csv` | `d886e53918845effa9bdee99bf4f996844eb48296b137a6e42f6e64b98e31b3a` |
| `config/universe_us_sectors.csv` | `759d061f56faa692d89c12d73a6d93aaafc64968dc987271a646fe1b3f4f2f2b` |
| `data/prices_raw_069500_KS_153130_KS_KRW=X_SPY_2010-01-01_2026-10-07.csv` | `cc4cfa798871e05780fe636924e5e732120820ff472b0bf02703c14fa504ce79` |
| `data/prices_raw_069500_KS_2010-01-01_2026-10-07.csv` | `e3aeaa23a776b75d4e378e3e6a935316b61b8eaf26b7c3dac3b390ccdc33c8cd` |
| `data/prices_raw_153130_KS_2010-01-01_2026-10-07.csv` | `47fa8e959e11c7610b038c774f61e8ab41455a281fded61342cdca6c934aaf2e` |
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

## 부록 B. 실행한 재현 코드

공급자 응답 mock, 수식 기대값, 출력 분리까지 포함한다. 원자료 결함은 첫 스크립트만으로 재현 가능하다.

### raw_boundaries.py

```python
import json,warnings
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum import cli,data
from etf_momentum.backtest import BacktestConfig,run_backtest,StaleDataError
from etf_momentum.signals import MomentumConfig
out=Path('/private/tmp/etf-review-20261011-final')
ix=cli._exchange_sessions('SPY');ix=ix[(ix>='2024-01-02')&(ix<='2024-03-15')]
base=pd.DataFrame({'A':100*1.002**np.arange(len(ix)),'SPY':100*1.001**np.arange(len(ix)),'BIL':100.},index=ix)
conf=BacktestConfig(top_n=1,candidate_tickers=('A',),momentum=MomentumConfig(lookbacks=(1,2,3),skip_days=0,sma_window=2,volatility_window=2))
results={}
def download(p,name):
 frame=p.copy();frame.columns=pd.MultiIndex.from_tuples([('Close',t) for t in frame.columns])
 with patch.object(data,'_yf_download',return_value=frame) as yf:
  raw=data.download_prices(['A','SPY','BIL'],'2024-01-02','2024-03-16',cache_dir=out/name,raw=True,refresh=True)
 assert yf.call_args.kwargs['auto_adjust'] is True
 return raw
for name,value in [('zero',0.),('negative',-1.),('infinite',np.inf)]:
 p=base.copy();p.loc['2024-02-05','A']=value;raw=download(p,name)
 assert raw.notna().loc['2024-02-05','A']
 with warnings.catch_warnings(record=True) as ws:
  warnings.simplefilter('always');eq,pos,metrics=run_backtest(raw.ffill(),conf,observed=raw.notna(),sessions=ix)
 results[name]={'raw_value':str(raw.loc['2024-02-05','A']),'observed':True,'run':'success','value_on_bad_day':str(eq.strategy.loc['2024-02-05']),'nan_equity_rows':int(eq.strategy.isna().sum()),'ending':str(eq.strategy.iloc[-1]),'warnings':[str(w.message) for w in ws]}
 assert not np.isfinite(eq.strategy.loc['2024-02-06']) or eq.strategy.loc['2024-02-05']<=0
p=base.copy();gap=pd.to_datetime(['2024-02-05','2024-02-06','2024-02-07','2024-02-08','2024-02-09','2024-02-12']);p.loc[gap,:]=np.nan
raw=download(p,'whole_gap')
assert len(p)-len(raw)==6
cli._require_fresh(raw.notna(),{'benchmark':'SPY','cash':'BIL'})
eq,pos,_=run_backtest(raw.ffill(),conf,observed=raw.notna(),sessions=ix)
results['whole_gap']={'removed_rows':6,'missing_real_sessions':[str(x.date()) for x in gap],'run':'success','positions':len(pos),'curve_rows':len(eq)}
try:run_backtest(p.ffill(),conf,observed=p.notna(),sessions=ix)
except StaleDataError as e:results['whole_gap']['preserved_index_control']=str(e)
else:raise AssertionError('control should reject six missing sessions')
(out/'raw_boundaries.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
```

### tail.py

```python
import json
from pathlib import Path
import numpy as np
import pandas as pd
from etf_momentum import cli
from etf_momentum.backtest import BacktestConfig,run_backtest
from etf_momentum.signals import MomentumConfig
out=Path('/private/tmp/etf-review-20261011-final');res=[]
index=pd.bdate_range('2023-01-02','2023-12-29')
mc=MomentumConfig(lookbacks=(21,42,63),skip_days=0,sma_window=50,volatility_window=20)
from itertools import product
for execution,lag,jump in product(['same_close','next_close'],[1,3,5],[1.1,.9]):
  prices=pd.DataFrame({'A':100*1.001**np.arange(len(index)),'BENCH':100*1.0005**np.arange(len(index)),'CASH':100.},index=index)
  prices.loc[index[-1],'A']*=jump;prices.loc[index[-lag:],'BENCH']=np.nan
  cli._require_fresh(prices.notna(),{'benchmark':'BENCH','cash':'CASH'})
  config=BacktestConfig(top_n=1,benchmark='BENCH',cash_ticker='CASH',candidate_tickers=('A',),momentum=mc,execution=execution)
  equity,positions,_=run_backtest(prices,config,observed=prices.notna(),sessions=index)
  expected=float(prices.A.pct_change().iloc[-1]);actual=float(equity.strategy.pct_change().iloc[-1])
  assert np.isclose(actual,expected,atol=1e-12)
  res.append({'execution':execution,'lag_rows':lag,'jump':jump,'expected_last_return':expected,'actual_last_return':actual,'hold_until':str(positions.hold_until.iloc[-1].date()),'reported_end':str(equity.index[-1].date()),'latest_guard':'passed'})
(out/'tail.json').write_text(json.dumps(res,indent=2));print(json.dumps(res,indent=2))
```

### fix_and_cache.py

```python
import contextlib,io,json,sys,warnings
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum import cli,data,naver
out=Path('/private/tmp/etf-review-20261011-final');res={}
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
with patch.object(data,'NAVER_FILL',True),patch.object(data,'_yf_download',return_value=pd.DataFrame()),warnings.catch_warnings(record=True) as ws:
 warnings.simplefilter('always')
 try:data.download_prices(['A.KS'],'2024-01-02','2024-01-05',cache_dir=legacy,raw=True)
 except RuntimeError as e:res['legacy_fallback']={'run':'stopped','error':str(e),'old_bytes_unchanged':path.read_bytes()==old.to_csv().encode()}
 else:raise AssertionError('invalid version cache was returned')
# Jointly missing real session in Yahoo and Naver reference escapes containment check.
missing=days[1];truncated=pd.DataFrame({'A.KS':[90.,90.]},index=days[[0,2]])
def cutoff(ticker,start,end):return pd.Series(100.,index=days[[0,2]])
f,report=naver.fill_gaps(truncated,fetch=cutoff)
assert missing not in f.index
res['joint_calendar_gap']={'expected_missing_session':str(missing.date()),'returned_dates':[str(x.date()) for x in f.index],'report':report,'run':'success'}
(out/'fix_and_cache.json').write_text(json.dumps(res,ensure_ascii=False,indent=2));print(json.dumps(res,ensure_ascii=False,indent=2))
```

### normal_and_prior.py

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

OUT = Path('/private/tmp/etf-review-20261011-final')
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

### replay.py

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

OUT=Path('/private/tmp/etf-review-20261011-final')
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

### new_boundaries.py

```python
from pathlib import Path
from unittest.mock import patch
import os, json
import numpy as np
import pandas as pd
from etf_momentum.universe_builder import write_universe
from etf_momentum.backtest import BacktestConfig, run_backtest, previous_month_end
from etf_momentum.signals import MomentumConfig
out=Path('/private/tmp/etf-review-20261011-final');out.mkdir(exist_ok=True)
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

### cached_checks.py

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

out=Path('/private/tmp/etf-review-20261011-final/cached');out.mkdir(exist_ok=True)
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

### universe_and_research.py

```python
import hashlib,json,runpy,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum.universe_builder import recent_returns,correlation_groups
from etf_momentum.backtest import summarize_performance
from etf_momentum import data
out=Path('/private/tmp/etf-review-20261011-final')
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

### builder_cli.py

```python
import contextlib,importlib.util,io,json,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
out=Path('/private/tmp/etf-review-20261011-final')
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

