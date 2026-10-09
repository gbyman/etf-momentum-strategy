# 전체 코드 재검토 — 2026-10-09

## 판단

현재 프로젝트는 **연금 계좌에서 보유할 ETF를 월말 규칙으로 선정하고, 주간 리더십 변화를 관찰하는 도구**라는 컨셉에 맞게 구성되어 있다. 전략 JSON으로 선정 규칙을 고정하고, `rank`와 백테스트가 공통 선정 함수를 쓰며, 계좌 후보 제한·그룹 제한·현금 슬롯·신호 revision을 관리한다. 실제 투자 비중과 주문은 사용자가 결정한다는 범위를 유지한다.

현재 HEAD에서 전체 테스트는 **266 passed, 10.94초**였다. 기본 연금 전략의 캐시 재실행에서도 선정·회계·성과가 이전 측정과 일치했다. 그러나 추가 합성 재현으로 **P2 4건**을 확인했다. 이번 검토에서 새로운 P1을 확정하지 않았으며, 정상 자료의 기본 전략이 잘못 선정한다고 확인한 것도 아니다.

| ID | 등급 | 확인된 문제 | 영향 조건 |
|---|---|---|---|
| R01 | P2 | 프록시 환율의 5거래일 최신성 허용이 두 번 적용됨 | 프록시는 계속 갱신되지만 환율 관측이 중단됨 |
| R02 | P2 | 잘린 신호 기록에 추가하면 CSV 행이 결합됨 | 마지막 기록이 줄바꿈 없이 중단된 상태 |
| R03 | P2 | 모든 시나리오에서 미사용인 슬리브도 CLI 실행을 차단함 | 0비중 슬리브의 내부 날짜 누락 등 |
| R04 | P2 | 중복 티커를 두 번 선정하고 비중을 덮어씀 | enabled 티커 중복 + 그룹 제한 해제 + 동일 비중 |

R01과 R04는 선정·백테스트 계산에 직접 영향을 주므로 먼저 보완하는 것이 좋다. R02는 기록 보존, R03은 포트폴리오 명령의 선언된 동작과 관련된다. 기존 리뷰 문서는 보존하고, 이번 결과는 별도 문서에 기록했다.

## 검토 범위와 환경

- 검토 대상: 현재 작업 트리 전체. HEAD `2d53bab0c9a8344170dee2886e833094c2abc476`.
- 시작 상태: `git status --short` 출력 없음. 제품 코드의 미커밋 변경 없음. 변경 비교 기준은 이전 리뷰 HEAD `99abcb4085cfb55618c8c29031ac47681e89bbf4`지만, 변경분만 검토하지 않았다.
- 읽은 구현: `src/etf_momentum/` 전체 모듈과 `scripts/research_tables.py`. CLI→전략/유니버스→원자료/캐시→프록시/관측표→신호/선정→보유 회계/성과→워크포워드/조합→기록/출력을 추적했다.
- 기준 문서: `CLAUDE.md`, `README.md`, `pyproject.toml`, 전략/기록 설계, 원자료/달력 설계, 컨셉 보완 문서와 이전 전체 리뷰. 적용되는 별도 `AGENTS.md`는 작업 영역에서 발견하지 못했다.
- 적용 스킬: `.agents/skills/etf-momentum-code-review/SKILL.md` 및 checklist/report-format. 독립 리뷰어 1명을 사용했고, 예상 문제를 전달하지 않았다. 반환된 4건은 주 리뷰어가 별도 스크립트로 모두 재현했다.
- 환경: Python 3.14.3, pandas 3.0.3, numpy 2.4.6, pytest 9.0.3, exchange-calendars 4.13.2, yfinance 1.3.0.
- 네트워크 다운로드 없음. 합성 자료와 기존 로컬 원자료 캐시를 사용했다. 실제 캐시 실험은 다운로드 함수만 로컬 CSV reader로 교체하고 실제 CLI를 실행했다.
- 출력·재현 파일: `/private/tmp/etf-review-20261009-recheck/`. 제품 코드·설정·유니버스·테스트·운영 신호 기록·캐시·공식 결과는 수정하지 않았다. 커밋하지 않았다.
- 캐시 실험 11개 실행 메타데이터는 모두 위 HEAD, `dirty=false`였다. 보고서 생성 전 실행했기 때문이다. 본 문서는 리뷰 실험이며 공식 연구 결과를 새로 생성한 것으로 취급하지 않는다.

## 확인된 발견사항

### R01 — P2: 프록시 환율 최신성에 5거래일 허용을 두 번 적용한다

위치: `src/etf_momentum/data.py:111–116`, `src/etf_momentum/signals.py:141–143,220`. 연결 경로: CLI `_apply_proxies` → `proxy_observations` → `_observations` → `rank_momentum` → `observed_freshness`.

`proxy_observations`는 프록시의 새 종가가 있고 환율의 마지막 새 관측 이후 **5행 이하**면 관측표에 True를 쓴다. 환율이 갱신되지 않아도 그 5행 동안 프록시 관측일은 앞으로 이동한다. `observed_freshness`는 이렇게 만든 마지막 True 이후 **다시 5행**을 허용한다. 결과적으로 환율 원자료의 공백이 최대 10거래일이어도 후보가 fresh로 남는다.

기대 동작의 출처는 README 232행의 실제 관측 후 5거래일 규칙과 398행의 프록시·환율 실제 관측일 기준이다. 프록시와 환율의 최신성을 각각 확인해야 하며, 최신성을 한 번 통과한 합성 관측에 허용 기간을 추가해서는 안 된다.

재현 입력은 local 달력 `bdate_range('2020-01-02', periods=25)`, 매일 상승하는 프록시, 첫 14개 US 날짜까지만 있는 환율이다. 국내 A는 마지막 날에만 실제 가격이 있어 앞부분은 `splice_proxies`로 채운다. 계산을 짧게 만들기 위해 lookback=(1,2,3), skip=0, SMA=2, 변동성 창=2를 사용했다. 기본 창에서도 충분한 워밍업 뒤 같은 관측표 문제는 발생한다.

| 항목 | 재현값 |
|---|---|
| 마지막 환율 원자료 날짜 | 2020-01-20 |
| 한국 달력에서 그 관측이 처음 반영되는 날짜 | 2020-01-21 |
| 평가일 | 2020-02-04 |
| 이후 새 환율 없이 지난 local 행 | 10 |
| 합성 관측표의 `last_observed` | 2020-01-28 |
| 실제 판정 | `price_fresh=True`, `eligible=True` |
| 기대 판정 | 환율이 5거래일 넘게 미관측이므로 후보 제외 |

영향: 환율이 오래된 원화 프록시를 정상 후보로 사용할 수 있고, 같은 관측표를 사용하는 보유 결측 검사도 늦어진다. 이번 기본 캐시 실행에서 이 조건 때문에 선택이 바뀌었다고 확인한 것은 아니다.

수정 방향: 프록시 가격과 환율의 원천 관측 나이를 별도로 유지하고, 판단 시점에 각 나이가 허용 범위 안인지 확인한다. 환율에 대해 관측표 생성과 소비 단계에서 허용 기간을 중첩하지 않는다. 원천 둘의 휴장일이 달라도 정상 자료를 불필요하게 제외하지 않도록 테스트한다.

회귀 조건: 환율 나이 5/6/10/11행, 프록시만 중단/환율만 중단, 정상 휴장, 관측 재개, 상장일 전후. 기대는 5행까지 허용하고 6행부터 제외하는 것이다.

### R02 — P2: 중단된 신호 기록 뒤에 추가하면 두 CSV 행이 붙는다

위치: `src/etf_momentum/records.py:62–77`. 경로: `record_signal` → `_read` → `_append`.

`_read`는 pandas가 파일을 읽을 수 있고 헤더 순서가 맞으면 통과시킨다. pandas는 마지막 행의 부족한 필드를 빈 값으로 읽을 수 있다. `_append`는 파일 끝에 줄바꿈이 있는지 확인하지 않고 `mode='a'`로 다음 기록을 쓴다. 기존 마지막 행과 새 행이 물리적으로 이어져 CSV 구조가 깨진다.

기대 동작은 README 60행의 기존 행 보존과 전략/기록 설계의 손상된 기록에서 조용히 계속 쓰지 않는 원칙이다. 파일 바이트를 덮어쓰지 않는 것만으로 논리적 기록 보존이 보장되지는 않는다.

재현: 정상 2026-08-31 `AAA` 선정 기록을 임시 CSV에 만든 다음 마지막 3바이트를 잘라 `slots` 및 줄바꿈이 없는 상태를 모사했다. 이름·그룹은 빈 문자열이다. `_read`는 이 행을 `slots=''`로 받아들인다. 같은 config_hash의 2026-09-30 기록을 추가하면 다음 결과가 나온다.

| 항목 | 실제 결과 |
|---|---|
| 추가 반환값 | `action='added'`, revision=1 |
| 기존 파일 바이트 prefix 보존 | True |
| 추가 후 물리적 줄 수 | 2: 헤더 + 결합된 데이터 행 |
| 후속 `pd.read_csv`의 signal_date | `['2026-09-30']` |
| 기대 | 추가 전 손상을 보고 중단하고 파일을 그대로 보존 |

기존 8월 신호는 정상 데이터 행으로 읽히지 않는다. pandas의 초과 필드 해석 때문에 헤더만 맞는 파일이 잘못된 구조로 받아들여진다. 증거 파일은 임시 `truncated_signals.csv`이며 운영 `signals.csv`를 건드리지 않았다.

수정 방향: 추가 전 CSV의 논리적 행 필드 수·구조와 기록 필드의 유효성을 확인한다. 마지막 행 경계가 정상인지 확인하고, 불완전한 파일에는 오류로 멈춘다. 완전한 CSV가 단지 마지막 줄바꿈만 없는 경우도 새 행과 결합되지 않게 처리해야 한다. 손상 여부를 판단하지 않고 줄바꿈만 추가해 불완전한 기록을 정상화해서는 안 된다.

회귀 조건: 정상 재실행 불변, revision 추가, 완전한 마지막 행/줄바꿈 없음, 마지막 필드 중단, 인용 필드 중단. 오류 시 기존 바이트가 변하지 않아야 한다. 동시 쓰기 안전성은 이번에 재현하지 않았다.

### R03 — P2: 미사용 슬리브를 계산하기 때문에 0비중 수정이 CLI에는 완전히 적용되지 않았다

위치: `src/etf_momentum/cli.py:963–964,989–1005`. 비교 위치: `src/etf_momentum/portfolio.py:172–178`.

`run_portfolio`는 양의 비중 슬리브만 골라 공통 날짜를 계산하도록 수정되었다. 그러나 CLI는 시나리오 비중을 보기 전에 spec의 **모든** 슬리브를 다운로드·입력 검사·달력 정렬한다. 어느 시나리오에서도 쓰지 않는 슬리브의 오류가 실행 전체를 중단시킨다.

README 363행은 사용하지 않는 슬리브가 spec에 있어도 시나리오 결과가 바뀌지 않는다고 설명한다. 0비중 슬리브를 넣은 것만으로 정상 시나리오가 실행되지 않으면 이 약속과 맞지 않는다.

재현은 실제 `portfolio` CLI에서 다운로드 함수만 합성 원자료로 교체했다. CAL과 GOOD은 2024-01-02~05의 4영업일이 모두 있고, UNUSED만 01-03 가격이 없다. 시나리오는 `good=1.0`; 미사용 슬리브를 추가할 때 `unused=0.0`도 명시했다.

| spec | 실제 결과 |
|---|---|
| GOOD만 존재 | 성공, 조합 곡선 출력 |
| GOOD + UNUSED, 비중 good=1/unused=0 | `SystemExit: Sleeve unused ... missing 1 calendar date(s), first 2024-01-03` |
| 기대 | 미사용 슬리브가 없는 경우와 동일하게 성공하고 같은 곡선 생성 |

수정 방향: 모든 시나리오에서 양의 비중으로 쓰는 슬리브의 합집합을 먼저 정하고, 필요한 입력·환율만 준비한다. 다른 시나리오가 실제로 사용하는 슬리브는 계속 검증해야 한다. 모든 시나리오에서 0인 슬리브의 파일 누락·내부 결측·환율 설정 부재가 실행을 막지 않도록 한다.

회귀 조건: 라이브러리 테스트에 더해 전체 CLI에서 정상/누락 파일/내부 날짜 누락을 비교한다. 양의 비중 시나리오에서 같은 결측이 생기면 여전히 오류로 멈춰야 한다.

### R04 — P2: enabled 중복 티커가 선정 슬롯을 채우고 실제 비중은 사라진다

위치: `src/etf_momentum/data.py:47–64,86`, `src/etf_momentum/signals.py:119`, `src/etf_momentum/backtest.py:390–398,374–379`.

유니버스 loader는 enabled 티커 중복을 거부하지 않는다. 다운로드의 `unique_tickers`는 열을 중복 제거하지만 `account_candidates`는 원래 중복을 그대로 반환한다. 순위표에 같은 티커가 두 번 생기고, 그룹 제한이 없으면 두 행 모두 선정 슬롯을 차지한다. 같은 이름으로 실제 비중을 대입할 때는 뒤의 값이 앞의 값을 덮어쓴다.

재현 CSV:

```csv
ticker,name,category,enabled
A,Alpha,X,1
A,Alpha,X,1
CASH,Cash,Cash,1
```

상승하는 A와 가격 100인 CASH/BENCH, `top_n=2`, 동일 비중, `max_per_group=None`을 사용했다. CLI의 `--max-per-group 0`에 해당한다.

| 항목 | 실제 값 |
|---|---|
| 후보 | `('A','A')` |
| 선정 | `['A','A']` |
| A 비중 / CASH 비중 | 0.5 / 0.0 |
| 최종 비중 합 | 0.5 |
| 기대 | 중복 입력을 명확히 거부하거나, 서로 다른 ETF 1개 + 빈 슬롯 현금 1개 |

중복 선정 Series의 합은 1이므로 현금 잔여분도 0으로 계산되지만, 최종 고유 티커 비중 합은 0.5다. buy-and-hold 회계는 부족한 나머지를 미투자 현금으로 처리한다. 선정은 두 자리를 채웠다고 표시하면서 실제로는 절반만 투자되는 성과 왜곡이다. 현금 ETF의 수익도 그 나머지에 반영되지 않는다.

현재 저장된 3개 유니버스 CSV에서 enabled 중복은 **0개**였다. 기본 전략의 그룹당 1개 제한도 이 정확한 재현을 막는다. 그러나 유니버스 수정과 그룹 제한 해제는 지원 기능이므로 입력 경계에서 방어해야 한다. 다른 비중 방식의 중복 동작까지 동일한 수치라고 일반화하지 않는다.

수정 방향: 정규화한 enabled 티커의 중복을 loader에서 거부하고 행 번호를 표시한다. 동일 티커에 상충하는 계좌·그룹 설정이 있으면 조용히 하나를 택하지 않는다. 공통 선정 함수에서도 중복 ticker를 방어하고 최종 비중/현금 슬롯 불변식을 검사한다.

## 이전 리뷰 항목 재검증

이전 문서: [review_2026-10-09_full_codex.md](review_2026-10-09_full_codex.md). 이전 지적을 그대로 반복하지 않고 현재 코드의 동작을 확인했다.

| 이전 ID | 현재 판단 | 현재 증거 |
|---|---|---|
| F01 한국 신호에서 같은 날짜 US 시장 필터 사용 | 선정 CLI 경로 해결 | 혼합 시장은 다운로드 전 SystemExit. 직접 US 티커 대신 국내 상장 티커를 쓰는 규칙으로 문서 변경 |
| F02 프록시 중단도 계속 관측으로 처리 | 기존 사례 해결, 환율만 중단하는 경계 잔존 | 마지막 US 관측 2020-01-15, 평가 02-28: last_observed=01-16, fresh=False/eligible=False. 별도 R01 확인 |
| F03 rank의 cash 요청·필수 검사 누락 | 해결 | 요청 목록에 MISSING 포함, 데이터 없으면 `No price data ... cash MISSING`으로 중단 |
| F04 다른 시장 날짜가 lookback 변경 | 선정 CLI 경로 해결 | 혼합시장 차단. KR/US를 합친 인덱스에서 직접 라이브러리를 호출하면 시장 분리는 호출자 책임 |
| F05 과거 보유 현금 데이터 단절을 0%로 계산 | 해결 | 최신성 사전 검사는 통과해도 중간 101행 현금 공백에서 StaleDataError. 3행 휴장·상장 전 현금은 기존 회귀 테스트 통과 |
| F06 0비중 슬리브가 평가 기간 축소 | 라이브러리 해결, CLI 경계 잔존 | A만/A=1,B=0의 곡선 완전 동일. CLI는 R03처럼 미사용 입력을 먼저 검사 |
| F07 조합 최초 매수 비용이 기준값에서 소거 | 해결 | 무수익 2일·100bps 비용: 곡선 `[1.0,0.99]`, 측정 종료가치 0.99, 비용 0.01 |
| F08 NaN 모멘텀 가중치를 현금 신호로 처리 | 해결 | `rank --weights nan,0,1`은 다운로드 전 종료 코드 2. inf/0합/길이/lookback 경계 기존 추가 테스트 통과 |
| F09 선택된 테스트 수익 결측을 0% 처리 | 해결 | WIN의 테스트 64개 결측을 ValueError로 중단. 테스트를 보고 다른 설정으로 바꾸지 않음 |
| F10 사용자 임계값과 제외 설명 불일치 | 기존 사례 해결 | 모멘텀 50%/SMA거리 30% 설정에서 실제 임계값을 문구에 표시 |

원래의 F02/F06 최소 재현은 수정되었지만, 더 넓은 조건까지 완전히 해결되었다고 결론 내리지 않는다. 이번 R01/R03이 그 차이를 보여준다.

## 계산과 정상 경계 검산

합성 검산은 제품 함수를 기대값 계산에 재사용하지 않고 수식을 별도로 계산했다. 수치 비교 허용 오차는 원칙적으로 절대 `1e-12`이며, 미래 불변성·0비중 불변성은 DataFrame/Series 비교로 확인했다.

| 검산 | 입력 / 별도 기대값 | 현재 결과 |
|---|---|---|
| 모멘텀 | A=100×1.002^n, 700행. 각 항 `P[t-21]/P[t-L-21]-1`, L=63/126/252, w=.3/.3/.4 | 기대·실제 모두 0.38792207964425396 |
| 과거 신호 불변 | 평가일 뒤 A 가격만 ×100 | 과거 순위표 완전 동일 |
| buy-and-hold | A/B=.5/.5, A 수익 +10%,-10%, B=0. 최종 `.5×1.1×.9+.5` | 0.9950000000000001 |
| daily-rebalanced | 위 입력, 매일 .5/.5. `1.05×.95` | 0.9974999999999999 |
| 보유 비중 drift | 최종 A 가치 .495 / 전체 .995 | 0.49748743718592964 |
| 개별 상한 | [.9,.1], cap=.6은 재분배 후 합1; A만 있으면 합.6 | 두 조건 통과, 남는 금액 현금 처리 가능 |
| CAGR | 곡선 [1,1.1,.99,1.2], 2020-01-01~2021-01-01: `1.2^(365.25/366)-1` | 0.19955175204308273 |
| MDD | 위 곡선, `.99/1.1-1` | -0.10000000000000009 |
| Sharpe | 수익 [.1,-.1,.2], 무위험 .01. 초과수익 평균/표본표준편차×√252 | 기대 5.888972745734183 / 실제 5.888972745734187 |
| 이전 US 종가·FX | US +10%, FX +20%: `1.1×1.2-1`. 평가일 같은 날짜 값은 999/9999로 변경 | 0.32000000000000006, 당일 미래 값 미사용 |
| 주말 차입이자 | 금→월 3일, leverage=2, 자산 +1%, 연 차입 .365: `2×1.01-(1+.365×3/365)` | 1.0170000000000001 |
| 0.5배 노출 현금 | 자산 +10%, 현금 +2%: `.5×1.1+.5×1.02` | 1.06 |
| 최초 비용 | 비용만 100bps, 2일 | [1.0,.99], 측정 수익 -1% |
| same_day 내부 공백 | [100,NaN,110] | ValueError, 결측을 수익 0으로 숨기지 않음 |
| 월말 필수 관측 | 09-15/10-01만 있고 거래소 마지막 세션 09-30 | 누락 오류로 중단 |
| 워크포워드 미래 불변 | 2022년 테스트 이후 모든 수익을 -.4로 변경 | 2021년 첫 선택 동일 |

모멘텀 식의 분모도 skip을 적용하므로 일반적인 12-1 공식과 자동으로 동일하지 않다. 기존 테스트에는 가격 정체/원자료 관측, 유동성, 계좌 후보, 그룹 제한, 변동성 하한·목표, 위험자산 상한, 체결 경계, revision, 설정 해시 등의 검증도 포함된다. 테스트 통과를 모든 입력 조합의 완전 검증으로 해석하지 않는다.

## 로컬 원자료로 실제 호출 경로 재실행

캐시는 고정 다운로드 구간 `2010-01-01`부터 `--end 2026-10-07`까지의 `prices_raw_*`/`traded_value_*`를 사용했다. end는 배제되므로 출력 마지막 관측은 2026-10-06이다. 요청 티커를 모두 포함하는 캐시 중 열 수가 가장 적은 것을 고르고, 요청 열/기간을 자른 뒤 가격은 raw 여부에 따라 ffill했다. 실제 서비스 다운로드·캐시 갱신을 실행한 것은 아니다.

### 기본 전략 두 개

`config/strategies/pension.json`, `retirement.json`으로 각각 다음 호출을 실행했다. 경로와 cache_dir는 임시 디렉터리로 바꿨으며 rank는 고정 end로 실행하여 운영 신호를 추가하지 않았다.

```text
rank        --strategy <file> --lookback-years 20 --end 2026-10-07
weekly      --strategy <file> --lookback-years 20 --end 2026-10-07 --no-save-history
backtest    --strategy <file> --start 2010-01-01 --end 2026-10-07
            --transaction-cost-bps 10 --execution same_close
backtest    --strategy <file> --start 2010-01-01 --end 2026-10-07
            --transaction-cost-bps 10 --execution next_close
```

- rank/weekly의 **현재 랭킹 파일**은 완전 동일했다. weekly의 관찰 날짜와 rank의 월말 선정 날짜를 혼동하지 않고 비교했다.
- 두 계좌 모두 2026-09-30의 선정은 `139260.KS, 292150.KS, 102780.KS, 381180.KS`였다.
- 그 월말 선택과 두 체결 방식 백테스트의 해당 신호일 보유 티커가 일치했다.
- 백테스트마다 곡선 3,810행, positions 188행. 마지막 행을 제외한 187개 `hold_until`이 다음 `trade_date`와 모두 일치했다.

동일한 평가 기간 **2016-02-29~2025-02-28**, 10bps 비용, buy-and-hold 회계의 측정이다.

| 계좌 | 체결 | 전략 CAGR | 전략 MDD |
|---|---|---:|---:|
| 연금저축 | same_close | 11.597384% | -24.776565% |
| 연금저축 | next_close | 10.303659% | -24.599838% |
| 퇴직연금 | same_close | 9.152717% | -17.541112% |
| 퇴직연금 | next_close | 8.283887% | -17.416585% |

위 4개 수치는 이전 전체 리뷰의 같은 입력·가정 측정과 표시 정밀도에서 일치했다. 최신성·입력 방어 수정으로 정상 자료의 기본 성과가 달라졌다고 확인하지 않았다. 이는 시세의 외부 진실성이나 실거래 성과를 확인한 결과가 아니다.

### 비교·워크포워드·포트폴리오

- US sector 유니버스/SPY/BIL, top_n=2/3, equal, weighted_momentum, 월말, 상대강도 off의 작은 compare 그리드 2개 설정을 실행했다. 비용 5bps.
- 같은 기본 그리드의 walkforward는 그룹당 1개, 시장 필터 off, 학습 5년/테스트 12개월로 실행했다. 그리드 2개, 테스트 창 11개 출력 성공.
- `config/portfolio_pension.json`의 실제 portfolio CLI를 실행했다. summary 18행, 조합 곡선 4,091행 출력 성공.
- 조합의 전략 곡선 3개는 기존 results 파일을 입력으로 읽었다. 입력 메타데이터의 생성 commit은 모두 현재 HEAD, dirty=false였지만, 이번 리뷰가 그 3개 원천 실행을 독립적으로 다시 생성한 것은 아니다. 파일 해시는 부록에 고정했다.
- `scripts/research_tables.py`의 표 생성·기간·무위험 수익률 경로는 정적 검토했다. 이번 재검토에서 연구 전체 그리드와 모든 연구 표를 다시 생성하지 않았다.

## 실행 명령과 증거

저장소 루트에서 기존 가상환경으로 실행했다.

```sh
env MPLCONFIGDIR=/private/tmp/etf-review-20261009/mpl .venv/bin/python -m pytest -q
# 266 passed in 10.94s

env MPLCONFIGDIR=/private/tmp/etf-review-20261009-recheck/mpl \
    XDG_CACHE_HOME=/private/tmp/etf-review-20261009-recheck/cache \
    .venv/bin/python /private/tmp/etf-review-20261009-recheck/reproduce.py
# R01/R02/R03/R04의 실제 잘못된 동작을 assert하여 모두 재현

env MPLCONFIGDIR=/private/tmp/etf-review-20261009-recheck/mpl \
    .venv/bin/python /private/tmp/etf-review-20261009-recheck/normal_and_prior.py
# 독립 계산 및 이전 주요 지적의 수정 동작 확인

env MPLCONFIGDIR=/private/tmp/etf-review-20261009-recheck/mpl \
    .venv/bin/python /private/tmp/etf-review-20261009-recheck/cached_checks.py
# 실제 CLI, 로컬 원자료 reader 사용
```

추가 재현은 기존 pytest에 편입하거나 테스트 통과 개수에 합치지 않았다. `reproduce.py`의 성공은 버그가 없다는 뜻이 아니라, 문서에 적은 현재의 잘못된 동작이 실제로 나타났다는 뜻이다.

임시 증거: `reproduce.json`, `normal_and_prior.json`, `additional_arithmetic_and_inputs.json`, `cached/results.json`, 각 실행 log/CSV/메타데이터. 일부 그래프 생성에서 fontconfig의 사용자 cache 쓰기 경고가 있었지만 실행과 CSV 산출은 성공했다. 추가 다운로드나 의존성 설치는 하지 않았다.

## 제한과 컨셉에 대한 결론

확인된 구현 버그는 R01~R04다. 다음은 서로 구분해야 할 설계·검증 범위다.

- mixed KR/US 입력은 선정 CLI에서 거부하지만 직접 라이브러리는 시장 달력·정보 시점을 강제하지 않는다. 직접 연구 호출은 동일 시장 입력과 관측표를 준비해야 한다.
- 과거 프록시는 상장 전 가상 이력이며 현재 ETF의 보수·추적오차·실제 국내 유동성을 검증하지 않는다. 현재 상장일 연결 배율을 쓰는 것은 모델 가정이다.
- same_close는 종가로 신호를 만들고 같은 종가에 체결하는 가정이다. next_close와의 차이를 이번 실측에서도 확인했다. 실제 체결가·호가·주문 수량은 검토하지 않았다.
- 워크포워드는 설정 변경 시 실제 보유의 전환 비용·상태를 완전히 재현하는 실거래 엔진이 아니다. 이 부분은 명시된 모델 한계이며 이번에 새 버그로 분류하지 않았다.
- 설정/유니버스 해시가 가격 원자료까지 고정하는 것은 아니다. 데이터 파일 해시를 별도로 적었다.
- 외부 제공자·거래소 시세 대조는 하지 않았다. 특히 2025-03 이후 국내 가격의 외부 검증 부재라는 프로젝트 제약은 남는다. 139280/139290 상품명 확인도 이번에 해결하지 않았다.
- Python 3.14.3과 현재 설치 의존성 조합으로 실행했다. 지원하는 모든 Python/의존성 버전·모든 사용자 설정을 실행하지 않았다. 온라인 장애와 동시 기록 쓰기는 실측하지 않았다.

핵심 신호와 회계는 이번 독립 검산 및 정상 캐시 경로에서 컨셉대로 작동했다. 다음 작업은 전략 기능 확대보다 **원천 관측의 최신성, 유니버스 중복, 기록 파일 경계, 미사용 입력 처리**를 단단히 만드는 것이다. 투자 비중 자동 결정이나 주문 기능을 컨셉 보완의 필수 요건으로 제안하지 않는다.

## 부록 A — 네 문제의 자체 실행 가능한 재현 코드

아래 코드는 실행 당시 `reproduce.py`의 원문이다. 임시 디렉터리 자료가 정리되어도 복사해 재실행할 수 있도록 포함했다. 저장소 루트에서 기존 가상환경으로 실행한다. 제품 코드와 운영 기록은 수정하지 않고 다운로드는 mock으로 대체한다. 첫 실행 전 `/private/tmp/etf-review-20261009-recheck` 디렉터리를 만든다. 고정된 임시 파일은 재실행 시 덮어쓴다.

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
from etf_momentum.data import load_universe, account_candidates, proxy_observations, splice_proxies
from etf_momentum.signals import MomentumConfig, rank_momentum
from etf_momentum.backtest import BacktestConfig, _select_weights, select_holdings
from etf_momentum.records import record_signal, _read

OUT = Path('/private/tmp/etf-review-20261009-recheck')
results = {}

# FX outage, while the proxy itself still has daily observations.
index = pd.bdate_range('2020-01-02', periods=25)
us = pd.bdate_range('2020-01-01', periods=26)
proxy = pd.Series(100 * 1.01**np.arange(len(us)), index=us)
fx = pd.Series(1000.0, index=us[:14])
mask = proxy_observations(proxy, fx, index, 5)
raw_local = pd.DataFrame({'A': np.nan}, index=index)
raw_local.iloc[-1, 0] = 200.0
prices, starts = splice_proxies(raw_local, {'A':proxy}, fx)
observed = cli._observations(raw_local, argparse.Namespace(proxy_observed={'A':mask.loc[index<starts['A']]}), prices)
mc = MomentumConfig(lookbacks=(1,2,3), skip_days=0, sma_window=2, volatility_window=2)
row = rank_momentum(prices, index[23], config=mc, observed=observed).iloc[0]
assert bool(row.price_fresh) and bool(row.eligible)
results['R01'] = {'fx_last_source_date':str(fx.index[-1].date()), 'fx_last_local_observation':str(index[13].date()),
                  'as_of':str(index[23].date()), 'missing_local_rows':10,
                  'last_observed':str(row.last_observed.date()), 'price_fresh':bool(row.price_fresh), 'eligible':bool(row.eligible)}

# Explicit no-final-newline / partial-last-field append behavior.
selection = pd.DataFrame([{'status':'selected', 'rank_in_selection':1, 'ticker':'AAA', 'name':'', 'group':'', 'slots':1,'market_filter':'on'}])
def meta(date):
    return {'signal_date':date,'recorded_at':'2026-10-09T12:00:00','strategy_id':'s','version':1,'config_hash':'h','code_commit':'abc','code_dirty':False,'price_last_date':'2026-10-07'}
p=OUT/'truncated_signals.csv'
if p.exists():
    p.unlink()
record_signal(p, selection, meta('2026-08-31'))
p.write_bytes(p.read_bytes()[:-3])
before = p.read_bytes()
accepted_before = _read(p).to_dict('records')
added = record_signal(p, selection, meta('2026-09-30'))
after = pd.read_csv(p, dtype=str, keep_default_na=False)
assert added.action == 'added' and after.signal_date.tolist() == ['2026-09-30']
results['R02']={'before_missing_fields':accepted_before,'action':added.action,'after_signal_dates':after.signal_date.tolist(),
               'preserved_prefix':p.read_bytes().startswith(before), 'line_count_after':len(p.read_bytes().splitlines()),
               'artifact':str(p)}

# User CSV with a repeated enabled ticker reaches account_candidates unchanged.
p=OUT/'duplicate_universe.csv'
p.write_text('ticker,name,category,enabled\nA,Alpha,X,1\nA,Alpha,X,1\nCASH,Cash,Cash,1\n')
members=load_universe(p)
candidate=account_candidates(members,'all','BENCH','CASH')
panel=prices.assign(CASH=100., BENCH=100.)
ranking=rank_momentum(panel, config=mc, candidate_tickers=candidate, observed=panel.notna())
cfg=BacktestConfig(top_n=2,cash_ticker='CASH',benchmark='BENCH',momentum=mc,candidate_tickers=candidate,max_per_group=None)
chosen=select_holdings(ranking,panel,index[-1],cfg,panel.notna())
weights=_select_weights(ranking,panel,index[-1],cfg,panel.notna())
assert chosen.tickers == ['A','A'] and np.isclose(weights.sum(),.5)
results['R04']={'candidates':candidate,'selected':chosen.tickers,'weights':weights.to_dict(),'total_weight':float(weights.sum())}

# Full portfolio CLI: unused same-day curve must not determine a scenario's success.
cal=pd.bdate_range('2024-01-02',periods=4)
raw=pd.DataFrame({'GOOD':[100.,101.,102.,103.],'CAL':100.,'UNUSED':[100.,np.nan,101.,102.]},index=cal)
def download(tickers, raw=False, **kw):
    frame=globals()['raw'][list(tickers)].dropna(how='all')
    return frame if raw else frame.ffill()
def portfolio_case(extra):
    spec={'base_currency':'KRW','calendar_ticker':'CAL','start':'2024-01-02','end':'2024-01-06',
          'sleeves':{'good':{'ticker':'GOOD','currency':'KRW'}},'scenarios':[{'name':'only-good','weights':{'good':1.0}}]}
    if extra:
        spec['sleeves']['unused']={'ticker':'UNUSED','currency':'KRW'}
        spec['scenarios'][0]['weights']['unused']=0.0
    path=OUT/f'portfolio_{extra}.json'
    path.write_text(json.dumps(spec))
    with patch.object(cli,'download_prices',download),patch.object(sys,'argv',['etf-momentum','portfolio','--spec',str(path),'--output-dir',str(OUT/f'portfolio_{extra}')]),contextlib.redirect_stdout(io.StringIO()):
        try:
            cli.main()
        except SystemExit as e:
            return str(e)
    return 'success'
baseline=portfolio_case(False)
extra=portfolio_case(True)
assert baseline=='success' and 'missing 1 calendar date' in extra
results['R03']={'without_unused':baseline,'with_zero_weight_unused':extra}
(OUT/'reproduce.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
print(json.dumps(results,ensure_ascii=False,indent=2))

```

## 부록 B — 캐시 실험 입력 SHA-256

측정 시점의 파일 내용이다. 가격 공급자의 진실성을 검증한 해시는 아니다. 각 파일은 기존 자료를 읽기만 했다.

| 입력 파일 | SHA-256 |
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
| `config/portfolio_pension.json` | `a5da6a8368ff533e5914466ed742f3d550267ef971813100b4f1201b22b2f13f` |
| `results/walkforward/kr_pension_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv` | `652d8effcb6f94ab0548c64d5ca1c5c29fdd2669f50125869d1f766d58f05e5c` |
| `results/backtest/pension_fixed_top4_equal/equity_curve.csv` | `ab1fa47ee4e91b3f3211e1d3b599ffa0f8b1ac6d57a1c973928a3c3a875c241d` |
| `results/backtest/pension_fixed_top4_inverse_vol/equity_curve.csv` | `23099c51e460392079182f31fcb9dbfe1ce763ae4d4bb6ac97640afffd815741` |

조합 입력 생성 이력은 실행 메타데이터에서 다음처럼 읽었다. 이번 리뷰에서 원천 실행을 재생성하지 않았다는 제한은 유지한다.

| 슬리브 | 기존 입력 생성 시각 | code_revision |
|---|---|---|
| PEN_mom | 2026-10-09T11:01:22+09:00 | `{'commit': '2d53bab0c9a8344170dee2886e833094c2abc476', 'dirty': False}` |
| PEN_fixed_equal | 2026-10-09T11:11:28+09:00 | `{'commit': '2d53bab0c9a8344170dee2886e833094c2abc476', 'dirty': False}` |
| PEN_fixed_inverse_vol | 2026-10-09T11:11:42+09:00 | `{'commit': '2d53bab0c9a8344170dee2886e833094c2abc476', 'dirty': False}` |
