# ETF Momentum 전체 코드 리뷰·계산 및 경계 조건 재현

검토일: 2026-10-13 (Asia/Seoul)  
검토/실행 리비전: `a57d4d245dd658a50b99e5805b9b1319bd2d8cde`

**P2 2건과 P3 문서 불일치 1건**을 확인했다. 실제 거래 세션이 원자료에서 사라지는 경우 선정 최신성 검사가 오래된 후보를 통과시키고, 고정 기간 부분 캐시 재시도에서 네이버 보완 실패 자료가 정상 캐시로 고착된다. 기본 수식·정상 입력 회계·시간축 검산은 일치했다. 직전 E01은 해결됐고 E02의 보유 공백 검사는 해결됐지만, 선정 경로의 거래일 정의 문제는 G01로 남는다. 전체 기존 테스트는 **359 passed in 26.79s**다.

## 1. 확인된 발견사항

### G01 — P2: 실제 8거래일 미관측 후보가 fresh/eligible=True로 선정된다

**현재 위치:** `src/etf_momentum/signals.py:211–221` (`observed_freshness`), `src/etf_momentum/data.py:436–439` (전체 결측 행 삭제), `src/etf_momentum/cli.py:1209–1216` (가격표 달력 보존 없음), `:1496–1512` (필수 자산 신선도), `src/etf_momentum/backtest.py:341–350`의 시장 필터 신선도 호출. `rank`/`weekly` → 다운로드 원자료 정리 → ffill → raw.notna → 신선도 → rank/select 경로다.

**발생 조건:** 전체 패널의 월중 세션 7일에 자료가 없고, 다음 날 벤치마크·현금 자료만 돌아온다. 후보 A는 마지막 실제 관측 이후 **8 NYSE 세션**이 지났지만, 축소된 인덱스에는 그 뒤 행이 하나뿐이다. 후보 및 필수 티커 검사는 거래소 세션이 아니라 남은 행 개수로 판단한다.

**명세 대조:** README의 데이터 확인(`README.md:234`)은 마지막 실제 관측 후 5거래일 초과 후보를 제외하고 필수 티커는 중단한다고 설명한다. 반면 2026-10-09 원자료 설계 `docs/superpowers/specs/2026-10-09-raw-observations-and-exchange-calendar-design.md:32`는 거래일을 가격표 날짜 행으로 센다고 명시한다. 구현이 이 과거 정의를 어긴 것은 아니다. 그 정의가 전체 결측 행 삭제와 결합하면 알려진 실제 세션 공백을 숨기는 **검증/설계 경계 결함**이다. 현재 보유 공백 검사는 거래소 세션도 세므로 선정·보유 검사의 의미가 달라진다.

**직접 재현:** 부록 `rank_gap.py`. 2024-01-01~2025-09-30의 설치된 XNYS 달력으로 A=100×exp(.001n), SPY=100×exp(.0002n), BIL=100 패널을 만들었다. 2025-09-19/22/23/24/25/26/29를 전부 NaN으로, 9월 30일 A만 NaN으로 설정했다. Yahoo 응답만 mock하고 실제 다운로드 정리·`rank`/`weekly` CLI를 실행했다. 종료일 2025-10-01 미포함, rank top 1·시장 필터 켬, 기록/캐시는 임시 경로다.

| 항목 | 거래소 달력을 보존한 기대/대조 | 현재 결과 |
|---|---|---|
| A 마지막 관측 | 2025-09-18 | 2025-09-18 |
| 9월 30일까지 실제 경과 세션 | 8 | 남은 행은 1 |
| price_fresh / eligible | False / False | rank와 weekly 모두 True / True |
| 직전 월말 선택 | A 제외 | rank 선택 `[A]` |
| A를 필수 market filter로 검사 | 5세션 초과이므로 중단 필요 | `_require_fresh` 통과 |

원 세션 인덱스를 보존한 동일 입력에서 A의 fresh/eligible은 모두 False였다. 독립 리뷰어가 먼저 발견했고 주 리뷰어가 다운로드부터 두 CLI까지 재현했다.

**영향:** 가격을 전방 채운 오래된 후보가 정상 후보가 된다. 필수 필터의 실제 장기 공백도 행 수가 압축되면 중단되지 않는다. 정상 입력의 선정이 틀렸다는 주장은 아니며, 미국 경로와 국내 보완 실패 등에서 전체 세션이 없어질 때의 위험이다. 룩백·SMA의 행 개수 역시 압축될 수 있다.

**수정 방향:** 선정과 실행 전 신선도 판단에도 해당 시장의 실제 세션 수를 전달하거나 원자료/관측표를 시장 세션으로 정렬한다. 휴장·상장 전·프록시·달력 범위 밖 정책을 구분하고, 원자료 설계 문서의 날짜 행 정의도 맞춘다. 후보뿐 아니라 벤치마크/필터/현금, 5·6일 경계, 전체 패널/한 열 결측, 가격이 재개된 날, KR/US를 함께 검증한다. `_rows_since_observation`의 보유 공백 계산은 정상 재개일에 과거 공백도 검출하는 용도이므로 이를 그대로 모든 fresh 판정에 옮기지는 않아야 한다.

### G02 — P2: 부분 캐시 재시도가 네이버 보완 실패 자료를 저장해 재시도를 끝낸다

**현재 위치:** `src/etf_momentum/data.py:267–272`, `:327–350`, 특히 `:345–350`. `_cached_download`는 네이버 실패 때 경고 후 캐시하지 않은 DataFrame을 반환한다. 새 `_retry_missing`은 반환 자료의 보완 성공 여부를 구분하지 않고 원 캐시에 합쳐 저장하고 missing 목록에서 티커를 제거한다. 원 캐시의 기존 v2 보완 기록은 그대로 유효하다.

**명세와 기대:** `CLAUDE.md`와 `README.md:232`의 보완 실패 정책은 잘린/실패한 자료를 캐시에 고정하지 않고 다음 실행에 다시 시도하는 것이다. 고정 기간의 부분 캐시 허용은 이 실패 정책을 우회할 이유가 되지 않는다. 새 티커 자료가 도착해도 보완 실패 상태는 재시도 대상으로 남아야 한다.

**직접 재현:** 부록 `retry_gap.py`, 종료일을 고정한 A.KS/NEW.KS 3일 입력. 초기에는 A만 받고 네이버 보완 성공으로 v2 기록/NEW missing 기록을 생성한다. 두 번째는 NEW의 Yahoo `[100,NaN,120]`을 받고 네이버에 일시적 OSError를 주입한다. 세 번째 호출은 외부 함수 호출 시 실패하도록 막아, 실제 재시도 유무를 확인한다.

| 단계 | 기대 | 실제 |
|---|---|---|
| 첫 다운로드, NEW 전부 NaN | A 부분 캐시와 NEW 재시도 기록 | 정상 작동 |
| NEW 재다운로드, 네이버 실패 | 미보완 상태 보존·다음 실행 재시도 | 경고는 'prices not cached, the next run retries'이나 원 캐시에 NEW 결측 자료 저장 |
| missing 기록 | 실패가 해소될 때까지 재시도 표시 | NEW 열 존재만 보고 `.missing.json` 제거 |
| 원 캐시 보완 상태 | NEW의 보완 성공을 확인해야 함 | 기존 v2 기록 때문에 `_needs_naver_fill=False` |
| 세 번째 호출 | 보완 재시도 | Yahoo/네이버 호출 0, NEW 중간 가격 계속 NaN |
| 명시적 refresh + 정상 네이버 대조 | 중간값 110 | 110으로 복원 |

재현의 모든 캐시와 sidecar는 임시 경로다. 공급자 네트워크 연결은 하지 않았다. C02의 일반 다운로드 실패는 여전히 정상 재시도되며, 이번 문제는 **고정 부분 캐시의 새 재시도 경로**에서 발생한다.

**영향:** 경고와 달리 고정 캐시가 영구 재사용되며 월말·룩백 가격/수익률을 전방 채운 값으로 계산할 수 있다. 하나의 결측만으로 즉시 5일 신선도 거부가 되는 것도 아니므로 오류가 조용히 남는다. 현재 운영 캐시에서 이 순서가 실제 발생했다는 주장은 아니다.

**수정 방향:** 다운로드 결과와 캐시/보완 완료 상태를 구분해 반환한다. 보완 실패 자료는 원 캐시의 완료 상태에 합치지 않거나, 미완료 티커/보완 재시도 상태를 유지한다. sidecar의 보완 상태가 실제 합친 티커 자료와 대응하도록 한다. 실패→다음 정상 응답, 잘린 참조 달력, 재시도 디렉터리에 기존 sidecar가 남은 경우, 여러 누락 티커 중 일부만 성공, 정상 v2 merge를 검증한다.

### G03 — P3: README의 부분 다운로드 캐시 설명이 같은 문서 안에서 충돌한다

`README.md:234`는 고정 `--end`의 부분 다운로드를 저장하고 누락 티커만 재시도한다고 설명하지만, `README.md:237`은 일부 티커가 빠지면 캐시하지 않고 요청 티커가 빠진 캐시를 다시 받는다고 제한 없이 설명한다. 실제 코드는 최신 조회는 미저장, 고정 기간은 부분 저장/누락 티커 재시도로 나뉜다. 새 회귀 테스트와 G02의 첫 단계에서 이 차이를 확인했다. 237행을 최신 조회에 한정하고 고정 기간 예외를 연결하면 된다. 선정 계산에 영향을 주지 않는 문서 문제이며 G02와는 별개다.

## 2. 범위·환경·검토 방법

- 시작 작업 트리 clean, HEAD 위와 동일. 미커밋 변경 없음. 직전 `9bffdbc`와 현재 스냅샷 diff도 읽었으나 검토를 변경분에 한정하지 않았다.
- 스킬: `.agents/skills/etf-momentum-code-review/SKILL.md`, 체크리스트/보고 형식. `CLAUDE.md`, README, pyproject, 개념 개선 문서, 연구 요약, 전략/기록·관측/달력·전체 유니버스 설계, 과거 리뷰의 재현 항목을 기준으로 삼았다. 적용되는 AGENTS.md는 발견하지 못했다.
- Python 3.14.3, pandas 3.0.3, NumPy 2.4.6, pytest 9.0.3, exchange-calendars 4.13.2, yfinance 1.3.0. 설치된 `.venv` 사용. 아래 재현은 `PYTHONPATH=src`로 현재 소스를 명확하게 참조한다.
- 기본 v3 연금/퇴직 전략: 유니버스 788, 63/126/252·skip 21·가중치 .3/.3/.4, SMA 200, 변동성 63, 일 거래대금 10억원, top 4, 그룹 제한 0, 069500.KS 시장 필터, 153130.KS 현금, 프록시 사용. 퇴직 평가의 위험자산 한도 .7.
- 목적은 보유 ETF 선정이다. 주문·통합 배분 기능 추가를 필수 개선으로 제안하지 않는다. 계좌 허용 여부는 현 사용자 정책의 상품명 기준이며 증권사 목록 대조는 하지 않는다.
- 독립 리뷰어 한 명에게 리비전·요구사항·체크리스트를 전달했다. 세션 전체 이력이나 주 리뷰어의 의심을 초기 전달하지 않았다. G01과 별도 정상 수식/60종목 자료 검산을 받았으며 주 리뷰어가 직접 재실행했다. G02는 주 리뷰어의 별도 재현이다.
- 제품 코드·설정·기존 테스트·운영 캐시·결과·기존 보고서는 변경하지 않았다. 신규 리뷰 문서만 작성하고 커밋하지 않는다. 실험은 `/private/tmp/etf-review-20261013`에 분리했다.

| 전체 검토 영역 | 코드/설정과 실행 증거 |
|---|---|
| 입력·설정 | cli.py/strategy.py, 전략 JSON·유니버스: override 충돌, 유효 숫자, 시장 혼합, 필수 티커, 계좌·현금 허용, 출력 메타 |
| 데이터 | data.py/naver.py: 원자료 관측표, 양수·유한성, 부분·만료·구버전 캐시, 실패 fallback, 분배금 배율, 네이버 참조 달력, 프록시/환율 |
| 선정 | signals.py/backtest.select_holdings: 모멘텀·SMA·변동성·상대강도·유동성, fresh, 그룹·빈 슬롯·중복·시장 필터, 날짜 불변성 |
| 평가 | backtest.py: 신호/체결/수익/종료일, 보유 방식, 드리프트·회전율·비용·상한·잔여 현금, 성과 지표 |
| 연구 | walkforward.py와 CLI 그리드: 훈련/테스트 경계, 미래 변경 불변성, 누락 수익 거부, 개별 비중 상한 독립 검산, 작은 실제 자료 그리드 |
| 조합 | portfolio.py와 CLI/spec: 평가 달력·이전 미국 종가·환율, 내부 결측·현금 신선도·비용·미사용 슬리브 |
| 기록 | records.py/weekly.py/reporting.py: append/revision, 잘린 CSV 보호, 같은 규칙 비교, 변경 notice, 원자료/설정/리비전 메타 |
| 유니버스 | universe_builder.py/scripts.build_universe: 제외·코드 0-padding·중복·필수 자산, complete linkage, 원자적 쓰기/메타 해시 |
| 문서/산출물 | scripts.research_tables, 저장 연구 곡선의 독립 집계·표 대조, 전체 pytest와 이전 항목 재실행 |

## 3. 과거 지적 재검증

현재 신규 결함과 과거 번호를 섞지 않기 위해 이번 발견은 G01~G03으로 표기한다.

| 과거 항목 | 현재 판정·실행 결과 |
|---|---|
| E01 비정상 가격 | 해결. 0/−1/+inf/−inf 네 경우 모두 raw NaN·observed=False, 경고, 전략 곡선 양수·유한. 캐시 읽기 마스킹 회귀 테스트도 통과 |
| E02 전체 결측 행/보유 공백 | 보유 공백 경로 해결. 실제 XNYS 5세션 전체 결측은 허용, 6세션은 StaleDataError. 데이터 행은 여전히 삭제되므로 선정 경로의 별도 G01은 잔존 |
| D01 구 네이버 v1 fallback | 해결 유지. 빈 새 응답은 RuntimeError, 기존 바이트 불변; 유효 v2의 오래된 캐시 fallback 회귀 테스트 통과 |
| D02 마지막 보유 수익 | 해결 유지. 두 체결 방식×1/3/5행 지연×상승/하락=12건, 마지막 수익 +.1011/−.0991 일치; hold_until/보고 끝 2023-12-29. 6행 지연은 기존 회귀 테스트 거부 |
| C01 보완 분배금 | 해결 유지. [90,NaN,90]→[90,90,90], 배율 변화 .11111111111111116 기록 |
| C02 빈 참조 달력 | 일반 경로 해결 유지. 첫 실패 캐시 미작성, 다음 Yahoo 1/네이버 2 재호출로 정상 보완. 부분 캐시 경로는 G02 |
| C03 시작 전 현금 공백 | 해결 유지. 공통 평가 시작 2024-01-02 이전 공백 제외, 평가곡선 안 현금 결측 0 |
| N01 다음 실제 벤치마크 체결 | 해결 유지. 2025-12-30 신호→2026-01-02 체결 |
| N02 벤치마크 월중/월말 결측 | 해결 유지. 한 열 11행 중단, 실제 월말 누락 rank/backtest 둘 다 중단 |
| N03 프록시 거래대금 최신성 | 해결 유지. 4/28 관측→6/20 평가: 평균 없음/liquid=False/eligible=False |
| N04 워밍업 하루 경계 | 해결 유지. 필요한 6행을 갖춘 1/31 rank eligible, backtest 첫 신호/기준행 동일 |
| N05 실제 사용하는 현금 | 해결 유지. 노출 있는 현금 32일 공백 중단, 미사용 설정은 정상 |
| N06 파일 쌍 교체 | 해결 유지. 두 번째 replace 실패 시 CSV/메타 기존 두 파일 보존 |
| R01/R02/R03/R04 | 환율 5행 True·6/10 False, 15/16필드 기록 append 거부·바이트 보존, 0비중 슬리브 파일 미조회, loader 중복 거부/direct rank 1행 |
| F01/F04/F02/F03 | 시장 혼합 오류, 중단 프록시 stale, 필수 현금 다운로드 포함·누락 중단 |
| F05/F06/F07/F08/F09/F10 | 보유 현금 공백 중단, 0비중 무시, 초기 비용 포함, NaN 비중 exit 2, 선택 WIN 테스트 64수익 누락 중단, 설정 50%/30% 탈락 사유 반영 |

같은 참조 달력에 날짜 자체가 없으면 보완 함수는 그 날짜를 복원하지 못하는 합성 사례도 실행했다. 해결된 것으로 판정하지 않고 G01의 달력 보존 문제에 포함한다. 위 표의 개별 재현과 수식은 부록 코드에 보존했다.

이번 소규모 후속 수정은 전체 테스트에서도 검증했다: 고정 부분 캐시의 정상 누락 재시도/성공 merge, 설정된 KRX 2026-06-03·07-17 제외(미국 달력은 유지), builder 채권혼합 제외/정수 종목코드 6자리/제외 종목 중복 집계/필수 벤치마크 중단, 유니버스 밖 holdings 표시, 버전은 같고 config_hash만 바뀐 weekly notice. 달력에 설정한 날짜 제외를 확인한 것이며 이번 검토에서 외부 휴장 공고를 조회하지 않았다.

## 4. 독립 수식과 경계 검산

허용 오차는 수익·모멘텀·가격비·지표 1e-12~1e-10, 원화 거래대금 1e-6이다. 수식/배열 연산으로 기대값을 만들고 구현 결과와 비교했다.

| 항목 | 입력·독립 기대·현재 실제 |
|---|---|
| 가중 모멘텀 | A=100×1.002^n. Σw(P[t−21]/P[t−21−L]−1), L=63/126/252. 기대=실제 .38792207964425396 |
| 미래 정보 | 평가일 뒤 가격×100에도 이전 rank 전체 불변 |
| buy_and_hold | A +10%→−10%, B 0→0, 각 .5: .5×1.1×.9+.5=.995, 실제 .9950000000000001 |
| daily_rebalanced | 1.05×.95=.9975, 실제 .9974999999999999 |
| 드리프트 | A 종료 .495/.995=.49748743718592964 일치 |
| 개별 상한 | [.9,.1] cap .6 재배분 합 1, 후보 1개 cap .6이면 잔여 현금 .4 |
| CAGR/MDD | 366일 1→1.2: 1.2^(365.25/366)−1=.19955175204308273, 10% 하락 MDD −.10000000000000009 |
| 환율 이전 종가 | 가격1.1×이전환율1.2−1=.32, 실제 .32000000000000006. 같은 날 미래 가격 사용 안 함 |
| 최초 비용 | 초기자본1/비용.01: 곡선[1,.99], 최종 .99, 요약에서 비용 사라지지 않음 |
| 달력/슬리브 | 같은 날 내부 결측 오류, 9/30 월말 누락 오류, 미사용 파일 미조회 |
| 워크포워드 시점 | 2022년 이후 데이터 변경에도 2021년 첫 훈련 선택 불변, 선택된 테스트 결측 거부 |
| 워크포워드 상한 | 실제 CLI `--max-position-weight` 1/.25가 각각 config에 전달되고 곡선이 달라짐. 2021-02-02~2022-12-30, A 전량 기대 가격비 2.619640994563373/실제 2.619640994563369. .25A+.75현금 월별 수량 유지 독립 곡선 기대/실제 종료 1.2771187025208997 |

마지막 항목은 `wf_cap.py`의 월별 .75+.25×가격비 재귀식으로 표본외 전체 곡선을 비교했다. 두 cap을 바꿔도 결과가 같은지 의심했으나 실제 전달·수식 일치로 문제가 없음을 확인했다. 최초 비용은 문서에 정한 보유 첫날 수익 차감 방식으로 검증했으며 새 비용 모델을 도입하지 않았다.

### 60종목 보존 자료의 직접 검산

네이버 v2 보완된 2010-01-01~2026-10-06 고정 가격/거래대금, 9개 프록시와 KRW 환율, 2026-09-30, top 4/그룹 제한 0/10억원 유동성/연금 후보로 계산했다. 이것은 **현재 788개 전체 전략 검증이 아니라 기존 60개 자료의 검산**이다. 선택된 종목의 모멘텀·SMA·변동성·63일 평균 거래대금을 배열 수식으로 대조했다. 069500의 실제 SMA 위 조건도 직접 대조했다.

| 티커 | 가중 모멘텀 | SMA 대비 | 연 변동성 | 평균 일 거래대금(원) |
|---|---:|---:|---:|---:|
| 139260.KS | 1.3268633006764898 | .25208106580649114 | 1.0330718787056972 | 108717527393.33333 |
| 091230.KS | .9426624557928809 | .2654198506249712 | .9791468026820385 | 51609585479.12698 |
| 091160.KS | .9178802297041152 | .25540890714684994 | .9813126981838376 | 249305908476.74603 |
| 292150.KS | .7346702769110914 | .12036225390533306 | .7211267320425316 | 40369206915.95238 |

독립 리뷰어의 입력/수식 스크립트를 주 리뷰어가 실행해 확인했다. 일반 합성 검산도 독립 리뷰어가 별도 입력으로 수행했다. 지수 상승 가격의 모멘텀 기대 .1744311171682354/실제 .17443111716823534, A(+10%,−10%)/B(0%,+20%) 각 .5의 buy-and-hold 1.095/daily-rebalanced 1.1025와 종료 비중이 직접 수식과 일치했다. 같은 계산을 반복한 테스트 수를 합산하지 않는다.

## 5. 로컬 캐시를 이용한 명령 대조

다운로드 함수만 보존 CSV reader로 교체하고 실제 CLI 설정·선정·회계·출력을 실행했다. 처음의 '요청 티커가 모두 있는 CSV' reader는 0246X0.KS 자료 부재로 실행할 수 없었다. 현재 부분 다운로드와 같은 방식으로 존재하는 티커만 전달하도록 바꾸었다. 이 변경은 임시 리뷰 스크립트에만 적용했다.

**중요한 자료 제한:** 고정 788 요청 가격 캐시는 787열이며 `.missing.json`에 0246X0.KS가 기록돼 있다. 전체 거래대금 캐시는 없다. 가장 넓은 로컬 거래대금은 60종목 캐시이며, 788 유니버스와의 교집합은 **59종목(누락 729)**이다. CLI는 누락을 경고하고 유동성 미달로 제외한다. 따라서 아래 기본 전략 CLI 결과는 부분 거래대금 입력에서의 일관성 측정이며, 실제 전체 788개 최신 선정 결과나 정식 성과로 사용할 수 없다. 데이터 부족을 버그나 성공한 완전 검증으로 보고하지 않는다.

- 두 계좌 rank/weekly: lookback 5년, 종료 2026-10-07 미포함. 전체 ranking CSV 일치.
- 월말 2026-09-30 선택: 두 계좌 모두 139260/091230/091160/292150.KS. 같은 월말 백테스트 비현금 보유와 일치.
- 두 계좌×두 체결 방식 백테스트: 시작 2024-01-01, 비용10bp, 종료 고정. 곡선 2025-02-28~2026-10-06 390행, 20보유 구간. 각 마지막 구간을 제외한 hold_until=다음 trade_date.
- 부분 입력의 짧은 리뷰 구간 2025-03-31~2026-10-06 재기준 결과: 연금 same_close CAGR 82.8926%/MDD −47.7221%, next_close 86.6038%/−47.2280%; 퇴직 same_close 59.2310%/−34.0587%, next_close 59.9634%/−33.7860%. 기간/후보 유동성 제한이 달라 저장 장기 연구와 직접 비교할 수 없다.
- US compare top 2/3, 동일비중·월말·상대강도 끔·5bp의 두 결과 행. 같은 bounded walkforward train1년/test12개월은 1윈도/그리드2행.
- 기본 portfolio_pension spec: 요약18행/곡선4,123행. 저장 슬리브의 과거 리비전은 그대로 입력 메타에 보존한다.
- 위 11개 CLI의 최상위 `code_revision`은 모두 현재 HEAD/dirty=False. 보고서 작성 전 실행한 리뷰 실험이며 공식 연구 결과로 저장하지 않았다.

## 6. 유니버스·연구 문서 대조

788 티커 중복 없음, 연금 허용788/퇴직728, 현금153130만 risk_asset=0. 퇴직 제외는 현재 문서의 이름 `합성|선물` 규칙과 일치한다. 그룹96/포함485는 CSV/메타와 일치한다. 현재 기본 그룹 제한0은 사용자의 의도된 선택이다.

그룹 생성에 쓰인 2026-10-08까지의 예전 `2025-04-08_latest` 원자료는 현재 로컬에서 없어 **그룹 구성의 동일 입력 재생성은 확인 불가**다. 현재 고정 자료는 10/6까지이고 비정상 가격 정리도 달라져 이를 대체해 같은 그룹을 요구하지 않았다. complete-linkage의 체인 연결 방지·평평/63일 미만 자료·열 순서 불변성은 현재 코드와 전체 테스트로 확인했다. 이전 리뷰의 최저 상관 수치를 이번 리비전 실측으로 다시 쓰지 않는다.

builder CLI는 임시 mock 목록/가격으로 4종목 성공, unpriced 000005.KS 제거, raw=True, 필수 현금 누락 시 기존 CSV/메타 보존을 확인했다. 교체 실패 주입도 별도로 실행했다. 성공한 메타의 CSV SHA-256을 실제 바이트와 대조했다. 실제 상장 목록이나 증권사 구매 가능성 검증은 아니다.

저장 워크포워드 3곡선은 NumPy로 CAGR·MDD·Sharpe를 다시 집계해 summarize_performance와 1e-10 이내 일치를 확인했다. 실제 경과일/365.25, 누적최대 대비 MDD, 현금 수익 정렬 후 초과수익 평균/표본표준편차×√252로 계산했다. 현금 수익에 없는 날짜는 현 집계 정책의 0이다. 연구 표 스크립트 전체도 현금 다운로드만 로컬로 대체해 임시 Markdown을 생성했다.

| 저장 연구 | 2016-02-29~2025-02-28 CAGR / MDD / Sharpe | 2016-02-29~2026-10-06 CAGR / MDD / Sharpe |
|---|---|---|
| 60 ETF | 11.5527% / −16.3557% / .755811 | 17.5448% / −43.3141% / .771108 |
| 788 그룹1 | 9.0981% / −18.1409% / .570888 | 11.7825% / −53.3791% / .529564 |
| 788 그룹0 | 9.5058% / −36.0385% / .545222 | 13.9760% / −54.5617% / .589239 |

구간 각각2,211/2,600행. 현 연구 문서의 표시9.5%/14.0% 등과 일치하며 직전 리뷰의 그룹0 9.3%/13.8%를 현재 결과로 쓰지 않았다. 저장 연구는 top3/4 등을 훈련 선택한 곡선으로 고정 top4 선정 전략과 다르다. 표 재집계는 전체 장기 전략 재실행을 뜻하지 않는다.

## 7. 실행 명령·제한·판단

전체 테스트:

```sh
env MPLCONFIGDIR=/private/tmp/etf-review-20261013/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261013/cache .venv/bin/python -m pytest -q
```

결과 **359 passed in 26.79s**. 독립 리뷰어의 359개 실행은 같은 집합이므로 합산하지 않는다. 경계 재현의 assert 통과는 해당 오류가 현재도 발생함을 뜻하는 경우가 있다. 테스트 성공과 결함 수정 완료를 구분한다.

부록 각 Python 블록을 해당 파일명으로 임시 디렉터리에 저장하고 프로젝트 루트에서 아래 형태로 실행한다. 전부 임시 출력만 쓰며 네트워크를 사용하지 않는다. 로컬 자료 검산은 부록 A 입력이 필요하다.

```sh
mkdir -p /private/tmp/etf-review-20261013/mpl /private/tmp/etf-review-20261013/cache
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-review-20261013/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261013/cache .venv/bin/python /private/tmp/etf-review-20261013/rank_gap.py
env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-review-20261013/mpl XDG_CACHE_HOME=/private/tmp/etf-review-20261013/cache .venv/bin/python /private/tmp/etf-review-20261013/retry_gap.py
```

같은 형식으로 파일명을 바꿔 나머지를 실행한다. 문서 저장 뒤 재실행하면 untracked 보고서 때문에 dirty=True가 되는 것은 정상이다. 여기의 기록은 문서 생성 전 clean 코드에서 측정했다.

정상 자료에서 선정 컨셉과 주요 계산은 일치했다. 우선 수정할 부분은 **G01의 선정 신선도 거래일 정의와 G02의 보완 실패 상태 보존**이다. G03은 설명 정리다. 외부 시세/분배금/시장 목록/휴장 공고/상품 적격성을 이번에 대조하지 않았다. 전체 788 거래대금 입력 부재로 현 기본 전략의 완전한 최신 선정과 장기 유동성 포함 전 그리드를 새로 실행하지 않았다. 저장 곡선 수치·60종목 직접 검산·부분 자료 명령 일관성·bounded US 그리드·합성 시간축/회계로 검토 범위를 명시했다. 그룹 생성의 옛 원자료도 부재다. 이 한계와 생존 편향·사후 그룹·프록시 근사·워크포워드 전환 비용 미반영은 확인된 구현 결함과 구분한다.

## 부록 A. 입력 파일 지문

현재 실측에 사용한 입력과 설정, 핵심 sidecar의 SHA-256이다. 입력 메타의 과거 code_revision을 현재 실행 리비전으로 해석하지 않는다.

| 파일 | SHA-256 |
|---|---|
| `config/portfolio_pension.json` | `a5da6a8368ff533e5914466ed742f3d550267ef971813100b4f1201b22b2f13f` |
| `config/strategies/pension.json` | `f5bbf6f22f20f87596c0aab6d045a301ebc4b9f4b59b465494986627a438f95b` |
| `config/strategies/retirement.json` | `98a3cd96a425fafad92b33f022fa0e2c1a14c6d1b0e066a4460ca09f0b7604f5` |
| `config/universe_kr_all.csv` | `ee4047581bffc7095301436cef5bc5d6e88ac81de31165fa5f74b813fdc21f98` |
| `config/universe_kr_all.meta.json` | `98520bf072641611cc44fff36e09f266fc71963e4313a3e09b367e7e36e1c4fa` |
| `config/universe_kr_pension.csv` | `d886e53918845effa9bdee99bf4f996844eb48296b137a6e42f6e64b98e31b3a` |
| `config/universe_us_sectors.csv` | `759d061f56faa692d89c12d73a6d93aaafc64968dc987271a646fe1b3f4f2f2b` |
| `data/prices_raw_069500_KS_153130_KS_KRW=X_SPY_2010-01-01_2026-10-07.csv` | `cc4cfa798871e05780fe636924e5e732120820ff472b0bf02703c14fa504ce79` |
| `data/prices_raw_069500_KS_153130_KS_KRW=X_SPY_2010-01-01_2026-10-07.csv.naver_fill.json` | `5f741124efcb4c3697c2517e7a52703ec602e164e21312f730033c602310ad4a` |
| `data/prices_raw_069500_KS_2010-01-01_2026-10-07.csv` | `e3aeaa23a776b75d4e378e3e6a935316b61b8eaf26b7c3dac3b390ccdc33c8cd` |
| `data/prices_raw_069500_KS_2010-01-01_2026-10-07.csv.naver_fill.json` | `e00e0ea2d2a45f1c4d0b343560fb03f6a06ee3dd32b468baa9411a98e25496e0` |
| `data/prices_raw_153130_KS_2010-01-01_2026-10-07.csv` | `47fa8e959e11c7610b038c774f61e8ab41455a281fded61342cdca6c934aaf2e` |
| `data/prices_raw_153130_KS_2010-01-01_2026-10-07.csv.naver_fill.json` | `33b3cac09b65bc4a6484d479b2ad82431932fcb22b51c67ff5b918d098799b68` |
| `data/prices_raw_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv` | `0709b2ccf6295ef45c9859b77e0b65018d7e0f2329d2d756561eef82c980dc1f` |
| `data/prices_raw_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv.naver_fill.json` | `93f4e9c89fd47f0d2cb081c48df1906888938bd24532f66853d74e0e01b88c5b` |
| `data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2010-01-01_2026-10-07.csv` | `36fe38ad3e50afad802158afd89efa790c6c976cddf1e00093640c7354204e5a` |
| `data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2010-01-01_2026-10-07.csv.missing.json` | `47b17b0d89a1938c293ced1d16f2668e4c2682dfb6790aa47234d9d567e039ee` |
| `data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2010-01-01_2026-10-07.csv.naver_fill.json` | `c29ecb2f106b50030aefddc75a00809f674b7d82b6a4b91723bafcabf254e6f5` |
| `data/prices_raw_BIL_CIBR_DIA_GLD_IBB_ICLN_IGV_ITA_IWM_IYT_KRE_QQQ_SMH_SOXX_SPY_TAN_URA_VNQ_XBI_XLB_XLC_XLE_XLF_XLI_XLK_XLP_XLRE_XLU_XLV_XLY_XME_XRT_2010-01-01_2026-10-07.csv` | `4131f72107ab3c9f3fe3fc26f7ad4d85da497f82f17ba5b17ae4e1d5a558dd2f` |
| `data/prices_raw_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv` | `7aca8716bd62d88f2092b78f11ba7cdbcfc7d67b341f564e895142e3f86453c3` |
| `data/prices_raw_KRW=X_2010-01-01_2026-10-07.csv` | `fe32ed2511a9ad520bd920f723e9c9c1c4136b99264a8d71d002f70c625fab8a` |
| `data/prices_raw_SPY_2010-01-01_2026-10-07.csv` | `a9404c87bc09eaec42cb89140ee0c7be6883fb55ece9821372d402ae0387f83b` |
| `data/traded_value_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv` | `f48d2ea1e80d2ff2703b551a832e32457c46edc2a060189018b589fdf3378d71` |
| `data/traded_value_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv` | `f7a1fb2eddc6c9d6ba9516b96410fe89745b9082d0fde74f924ce2d089d2f596` |
| `results/backtest/pension_fixed_top4_equal/equity_curve.csv` | `81e34b3c58c6bf6bfd03494fa520196a6450ca22e0a8b50185118d902fa73739` |
| `results/backtest/pension_fixed_top4_inverse_vol/equity_curve.csv` | `8199695633151863a082d78928d99bc67ec24fe4c058548ba65c5635e06d6673` |
| `results/walkforward/kr_all_pension_10eok_10bp_nogroup_tvoff/walkforward_equity.csv` | `4320733a1d2d259d035050aa212fad08f19cad69f9f37f7ffab6e32e64286c48` |
| `results/walkforward/kr_all_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv` | `5a883849207df41ab9bf8ebb78e00aa4d907f621fa9e4a27cc234e6035d2fe55` |
| `results/walkforward/kr_pension_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv` | `cfa5191430d2e2886deddb63ddc833691778d6530be2c9f05c05b8392a7b4753` |

## 부록 B. 현재 코드에서 실행한 재현 스크립트

### rank_gap.py

```python
import contextlib,io,json,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum import cli,data
from etf_momentum.signals import rank_momentum
out=Path('/private/tmp/etf-review-20261013/rank_gap');out.mkdir(exist_ok=True)
ix=cli._exchange_sessions('SPY');ix=ix[(ix>='2024-01-01')&(ix<='2025-09-30')]
f=pd.DataFrame({'A':100*np.exp(np.arange(len(ix))*.001),'SPY':100*np.exp(np.arange(len(ix))*.0002),'BIL':100.},index=ix)
gap=ix[-8:-1];f.loc[gap,:]=np.nan;f.loc[ix[-1],'A']=np.nan
response=f.copy();response.columns=pd.MultiIndex.from_product([['Close'],list(f.columns)])
u=out/'universe.csv';pd.DataFrame({'ticker':['A','SPY','BIL'],'name':['A','SPY','BIL'],'category':['equity','equity','cash'],'enabled':[1,1,1]}).to_csv(u,index=False)
results={}
for command in ['rank','weekly']:
 log=io.StringIO();dest=out/command
 argv=['etf-momentum',command,'--universe',str(u),'--benchmark','SPY','--cash','BIL','--top-n','1','--risk-on-benchmark','--lookback-years','2','--end','2025-10-01','--output-dir',str(dest),'--cache-dir',str(out/'cache'),'--refresh']
 if command=='weekly':
  argv.remove('--top-n');argv.remove('1');argv.remove('--risk-on-benchmark');argv+=['--no-save-history']
 with patch.object(sys,'argv',argv),patch.object(data,'_yf_download',return_value=response),contextlib.redirect_stdout(log):cli.main()
 (out/f'{command}.log').write_text(log.getvalue())
 row=pd.read_csv(dest/'latest_ranking.csv').iloc[0]
 assert row.ticker=='A' and bool(row.price_fresh) and bool(row.eligible)
 results[command]={'last_observed':row.last_observed,'price_fresh':bool(row.price_fresh),'eligible':bool(row.eligible)}
 if command=='rank':
  s=pd.read_csv(dest/'latest_selection.csv');selected=s.loc[s.status=='selected','ticker'].tolist();assert selected==['A'];results[command]['selected']=selected
raw=data._clean_raw_prices(f)
cli._require_fresh(raw.notna(),{'filter':'A'})
row=rank_momentum(f.ffill(),benchmark='SPY',candidate_tickers=['A'],observed=f.notna()).iloc[0]
assert not row.price_fresh and not row.eligible
results['control_calendar_preserved']={'price_fresh':bool(row.price_fresh),'eligible':bool(row.eligible)}
results['omitted_sessions']=list(gap.strftime('%Y-%m-%d'));results['actual_session_age']=8;results['required_filter_passed']=True
(out.parent/'rank_gap.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
```

### retry_gap.py

```python
import json,warnings
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum import data,naver
out=Path('/private/tmp/etf-review-20261013/retry_gap');out.mkdir(exist_ok=True)
ix=pd.to_datetime(['2024-01-02','2024-01-03','2024-01-04'])
def yahoo(x):
 f=pd.DataFrame(x,index=ix);f.columns=pd.MultiIndex.from_product([['Close'],list(x)]);return f
calls=[]
def fetch(t,start,end):return pd.Series([100.,110.,120.],index=ix)
with warnings.catch_warnings(record=True) as w,patch.object(data,'NAVER_FILL',True):
 warnings.simplefilter('always')
 with patch.object(data,'_yf_download',return_value=yahoo({'A.KS':[100.,110.,120.],'NEW.KS':[np.nan]*3})),patch.object(naver,'naver_closes',side_effect=fetch):
  first=data.download_prices(['A.KS','NEW.KS'],'2024-01-01','2024-01-05',cache_dir=out,raw=True,refresh=True)
 with patch.object(data,'_yf_download',side_effect=lambda ts,*a,**k:calls.append(ts) or yahoo({'NEW.KS':[100.,np.nan,120.]})),patch.object(naver,'naver_closes',side_effect=OSError('Naver temporarily unavailable')):
  second=data.download_prices(['A.KS','NEW.KS'],'2024-01-01','2024-01-05',cache_dir=out,raw=True)
 def no_download(*a,**k):raise AssertionError('unexpected new download')
 with patch.object(data,'_yf_download',side_effect=no_download),patch.object(naver,'naver_closes',side_effect=no_download):
  third=data.download_prices(['A.KS','NEW.KS'],'2024-01-01','2024-01-05',cache_dir=out,raw=True)
 cp=data._cache_path(['A.KS','NEW.KS'],'2024-01-01','2024-01-05',out,prefix=data.PRICE_CACHE_PREFIX)
 assert np.isnan(second.loc['2024-01-03','NEW.KS']) and np.isnan(third.loc['2024-01-03','NEW.KS'])
 assert not data._missing_record_path(cp).exists() and not data._needs_naver_fill(cp,['A.KS','NEW.KS'])
 with patch.object(data,'_yf_download',return_value=yahoo({'A.KS':[100.,110.,120.],'NEW.KS':[100.,np.nan,120.]})),patch.object(naver,'naver_closes',side_effect=fetch):
  repaired=data.download_prices(['A.KS','NEW.KS'],'2024-01-01','2024-01-05',cache_dir=out,raw=True,refresh=True)
 assert repaired.loc['2024-01-03','NEW.KS']==110.
 result={'retry_tickers':calls,'second_gap_is_nan':True,'third_gap_is_nan':True,'missing_record_exists':False,'needs_fill':False,'refresh_with_good_naver':110.,'warnings':[str(x.message) for x in w]}
(out.parent/'retry_gap.json').write_text(json.dumps(result,indent=2));print(json.dumps(result,indent=2))
```

### previous_e.py

```python
import json,warnings
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum import cli,data
from etf_momentum.backtest import BacktestConfig,run_backtest,StaleDataError
from etf_momentum.signals import MomentumConfig
out=Path('/private/tmp/etf-review-20261013')
ix=cli._exchange_sessions('SPY');ix=ix[(ix>='2024-01-02')&(ix<='2024-03-15')]
base=pd.DataFrame({'A':100*1.002**np.arange(len(ix)),'SPY':100*1.001**np.arange(len(ix)),'BIL':100.},index=ix)
conf=BacktestConfig(top_n=1,candidate_tickers=('A',),momentum=MomentumConfig(lookbacks=(1,2,3),skip_days=0,sma_window=2,volatility_window=2))
results={}
def download(p,name):
 f=p.copy();f.columns=pd.MultiIndex.from_product([['Close'],list(p.columns)])
 with patch.object(data,'_yf_download',return_value=f):return data.download_prices(list(p.columns),'2024-01-02','2024-03-16',cache_dir=out/name,raw=True,refresh=True)
for name,value in [('zero',0.),('negative',-1.),('infinite',np.inf),('negative_infinite',-np.inf)]:
 p=base.copy();p.loc['2024-02-05','A']=value
 with warnings.catch_warnings(record=True) as ws:
  warnings.simplefilter('always');raw=download(p,name)
 assert pd.isna(raw.loc['2024-02-05','A'])
 eq,pos,_=run_backtest(raw.ffill(),conf,observed=raw.notna(),sessions=ix)
 assert np.isfinite(eq.strategy).all() and (eq.strategy>0).all()
 results[name]={'observed':bool(raw.notna().loc['2024-02-05','A']),'finite_positive_curve':True,'warning':[str(w.message) for w in ws]}
for days in [5,6]:
 p=base.copy();gap=ix[(ix>='2024-02-05')][:days];p.loc[gap,:]=np.nan;raw=download(p,f'all_gap_{days}')
 try:eq,pos,_=run_backtest(raw.ffill(),conf,observed=raw.notna(),sessions=ix)
 except StaleDataError as e:
  assert days==6;results[f'gap{days}']={'run':'stopped','error':str(e)}
 else:
  assert days==5;results[f'gap{days}']={'run':'success'}
 assert len(p)-len(raw)==days
(out/'previous_e.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
```

### wf_cap.py

```python
import contextlib,io,json,sys
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
from etf_momentum import cli
out=Path('/private/tmp/etf-review-20261013/wf_cap');out.mkdir(exist_ok=True)
ix=cli._exchange_sessions('SPY');ix=ix[(ix>='2020-01-02')&(ix<='2022-12-30')]
p=pd.DataFrame({'A':100*1.002**np.arange(len(ix)),'SPY':100*1.001**np.arange(len(ix)),'BIL':100.},index=ix)
u=out/'u.csv';pd.DataFrame({'ticker':['A','SPY','BIL'],'name':['A','SPY','BIL'],'category':['equity','equity','cash'],'enabled':[1,1,1]}).to_csv(u,index=False)
captured=[];real=cli.run_backtest
common=['--universe',str(u),'--benchmark','SPY','--cash','BIL','--start','2020-01-02','--end','2023-01-01','--lookbacks','2,3,4','--skip-days','0','--sma-window','3','--volatility-window','3','--transaction-cost-bps','0','--cache-dir',str(out/'unused')]
def record(prices,config,**kw):captured.append(config.max_position_weight);return real(prices,config,**kw)
for cap in [1.,.25]:
 dest=out/str(cap);args=['etf-momentum','walkforward',*common,'--max-position-weight',str(cap),'--top-n-grid','1','--weighting-grid','equal','--rank-by-grid','weighted_momentum','--rebalance-grid','ME','--max-per-group-grid','0','--risk-on-grid','off','--relative-strength-grid','off','--train-years','1','--test-months','12','--output-dir',str(dest)]
 with patch.object(cli,'download_prices',side_effect=lambda *a,raw=False,**kw:p.copy() if raw else p.ffill()),patch.object(cli,'run_backtest',side_effect=record),patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):cli.main()
a=pd.read_csv(out/'1.0/walkforward_equity.csv',index_col=0,parse_dates=True);b=pd.read_csv(out/'0.25/walkforward_equity.csv',index_col=0,parse_dates=True)
assert captured==[1.,.25]
assert not np.allclose(a.walk_forward,b.walk_forward)
# Independent OOS growth: every selected position is A at full weight, no costs, continuous hold.
expected=p.loc[a.index[-1],'A']/p.loc[a.index[0],'A'];actual=a.walk_forward.iloc[-1]
assert abs(actual-expected)<1e-12
# Independent monthly buy-and-hold formula with A at .25 and cash at .75.
rebalances=p.index.to_series().groupby(p.index.to_period('M')).max().tolist()
manual=pd.Series(np.nan,index=p.index);manual.loc[rebalances[0]]=1.
for start,end in zip(rebalances[:-1],rebalances[1:]):
 dates=p.index[(p.index>start)&(p.index<=end)]
 manual.loc[dates]=manual.loc[start]*(.75+.25*p.loc[dates,'A']/p.loc[start,'A'])
manual_oos=manual.reindex(b.index)/manual.loc[b.index[0]]
assert np.allclose(manual_oos,b.walk_forward,atol=1e-12,rtol=1e-12)
r={'requested_caps':[1.,.25],'effective_caps':captured,'different_oos_curves':True,'capped_expected_ending':float(manual_oos.iloc[-1]),'capped_actual_ending':float(b.walk_forward.iloc[-1]),'oos_start':str(a.index[0].date()),'oos_end':str(a.index[-1].date()),'full_weight_price_ratio':expected,'actual_ending':actual}
(out.parent/'wf_cap.json').write_text(json.dumps(r,indent=2));print(json.dumps(r,indent=2))
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

OUT = Path('/private/tmp/etf-review-20261013')
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

OUT=Path('/private/tmp/etf-review-20261013')
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

### tail.py

```python
import json
from pathlib import Path
import numpy as np
import pandas as pd
from etf_momentum import cli
from etf_momentum.backtest import BacktestConfig,run_backtest
from etf_momentum.signals import MomentumConfig
out=Path('/private/tmp/etf-review-20261013');res=[]
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
out=Path('/private/tmp/etf-review-20261013');res={}
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
out=Path('/private/tmp/etf-review-20261013');out.mkdir(exist_ok=True)
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

### local_math.py

```python
import json
from pathlib import Path
import numpy as np
import pandas as pd
from etf_momentum.data import load_universe, account_candidates, splice_proxies, proxy_observations
from etf_momentum.signals import MomentumConfig, rank_momentum
from etf_momentum.backtest import BacktestConfig, select_holdings

raw=pd.read_csv('data/prices_raw_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv',index_col=0,parse_dates=True)
tv=pd.read_csv('data/traded_value_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv',index_col=0,parse_dates=True)
source=pd.read_csv('data/prices_raw_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv',index_col=0,parse_dates=True)
source_tv=pd.read_csv('data/traded_value_GLD_INDA_KRW=X_QQQ_SCHD_SMH_SOXX_SPY_2010-01-01_2026-10-07.csv',index_col=0,parse_dates=True)
members=load_universe('config/universe_kr_pension.csv')
proxies={m.ticker:source[m.proxy] for m in members if m.proxy and m.proxy in source and m.ticker in raw}
prices,starts=splice_proxies(raw.ffill(),proxies,source['KRW=X'])
observed=raw.notna().reindex(index=prices.index)
for ticker,first in starts.items():
    before=prices.index[prices.index<first]
    observed.loc[before,ticker]=proxy_observations(proxies[ticker],source['KRW=X'],before)
tv,_=splice_proxies(tv,{m.ticker:source_tv[m.proxy] for m in members if m.proxy and m.proxy in source_tv and m.ticker in tv},source['KRW=X'],scale=False)
config=MomentumConfig(min_avg_traded_value=1e9)
asof=pd.Timestamp('2026-09-30')
ranking=rank_momentum(prices,asof,benchmark='069500.KS',candidate_tickers=account_candidates(members,'pension','069500.KS','153130.KS'),config=config,traded_value=tv,observed=observed)
rule=BacktestConfig(top_n=4,cash_ticker='153130.KS',benchmark='069500.KS',risk_on_benchmark=True,momentum=config)
selection=select_holdings(ranking,prices,asof,rule,observed)
h=prices.loc[:asof]
independent=[]
for ticker in selection.tickers:
    row=ranking.set_index('ticker').loc[ticker]
    arr=h[ticker].to_numpy()
    score=sum(w*(arr[-22]/arr[-(k+22)]-1) for w,k in zip((.3,.3,.4),(63,126,252)))
    trend=arr[-1]/np.mean(arr[-200:])-1
    vol=np.std(arr[-64:][1:]/arr[-64:][:-1]-1,ddof=1)*np.sqrt(252)
    liquid=tv.loc[:asof,ticker].tail(63).mean()
    assert abs(row.weighted_momentum-score)<1e-12
    assert abs(row.trend_strength_vs_sma-trend)<1e-12
    assert abs(row.realized_volatility-vol)<1e-12
    assert abs(row.avg_traded_value-liquid)<1e-6
    independent.append({'ticker':ticker,'weighted_momentum':score,'trend_strength':trend,'realized_volatility':vol,'avg_traded_value':liquid})
print('data',raw.shape,raw.index[0],raw.index[-1], 'proxy_count',len(starts))
print('market_filter_direct',h['069500.KS'].iloc[-1]>h['069500.KS'].tail(200).mean(),'actual',selection.risk_on)
print(json.dumps(independent,ensure_ascii=False,indent=2))

Path("/private/tmp/etf-review-20261013/local_math.json").write_text(json.dumps(independent,ensure_ascii=False,indent=2))
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
from etf_momentum import cli, data
from etf_momentum.backtest import summarize_performance

out=Path('/private/tmp/etf-review-20261013/cached');out.mkdir(exist_ok=True)
sources=[(p,pd.read_csv(p,index_col=0,parse_dates=True)) for p in sorted(Path('data').glob('prices_raw*.csv'))]
tv=[(p,pd.read_csv(p,index_col=0,parse_dates=True)) for p in sorted(Path('data').glob('traded_value*.csv'))]
used=[]
def read(tickers,start=None,end=None,raw=False,**kw):
 eligible=[(p,f) for p,f in sources if set(tickers)&set(f.columns)]
 if not eligible:raise AssertionError(f'No local price input for {tickers}')
 p,f=min(eligible,key=lambda pf:(-len(set(tickers)&set(pf[1].columns)),len(pf[1].columns)))
 used.append(str(p))
 f=data._clean_raw_prices(f.loc[start:,[t for t in tickers if t in f.columns]].copy())
 if end:f=f.loc[f.index<pd.Timestamp(end)]
 f=f.dropna(how='all')
 return f if raw else f.ffill()
def value(tickers,start=None,end=None,**kw):
 eligible=[(p,f) for p,f in tv if set(tickers)&set(f.columns)]
 if not eligible:raise AssertionError(f'No local volume input for {tickers}')
 p,f=min(eligible,key=lambda pf:(-len(set(tickers)&set(pf[1].columns)),len(pf[1].columns)));used.append(str(p))
 f=f.loc[start:,[t for t in tickers if t in f.columns]].copy()
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
out=Path('/private/tmp/etf-review-20261013')
u=pd.read_csv('config/universe_kr_all.csv').fillna('')
m=json.loads(Path('config/universe_kr_all.meta.json').read_text())
res={'universe':{'etfs':len(u),'pension_allowed':int(u.pension_ok.sum()),'retirement_allowed':int(u.retirement_ok.sum()),'groups':int(u.loc[u.group!='','group'].nunique()),'grouped_etfs':int(u.group.ne('').sum()),'historical_group_input_available':False}}
assert u.ticker.is_unique and len(u)==m['etfs']
assert (u.risk_asset.eq(0)==u.ticker.eq('153130.KS')).all()
assert (u.retirement_ok.eq(0)==u['name'].str.contains('합성|선물')).all()
p=Path('data/prices_raw_788_tickers_1d20b3b9c5fefa8f_2010-01-01_2026-10-07.csv')
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
out=Path('/private/tmp/etf-review-20261013')
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

