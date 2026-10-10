# 전체 코드 리뷰 및 경계 조건 재현 — 2026-10-10

## 판단

ETF 선정 규칙을 전략 파일로 고정하고, 월말 선택·주간 관찰·백테스트를 연결하는 구조는 프로젝트 컨셉과 맞는다. 모멘텀, 보유 회계, 비용, 환산 수익률과 주요 성과 지표는 독립 검산과 일치했다. 기본 연금 전략의 실제 캐시 재실행도 이전 측정과 같았다.

이전 재검토의 **R01~R04는 해당 재현 조건에서 해결**되었다. 그러나 전체 경로와 인접 경계를 검토해 **추가 P2 5건**을 확인했다. 전체 테스트 **284개 통과**와 별개로 아래 입력에서는 잘못된 동작이 나타난다. 새로운 P1을 확정하거나 기본 전략의 정상 자료에서 선정 오류를 확인한 것은 아니다.

| ID | 등급 | 문제 | 발생 조건 |
|---|---|---|---|
| N01 | P2 | `next_close`가 벤치마크 비거래일 행에서 체결 | 같은 시장 가격표에 휴장일의 잘못된 행이 남음 |
| N02 | P2 | 과거 벤치마크 공백이 0% 일간 수익으로 변환 | 월중 장기 결측, 최신·월말 관측은 정상 |
| N03 | P2 | 오래된 프록시 거래대금이 신선한 유동성으로 판정 | 프록시 가격/FX는 갱신되고 거래대금만 중단 |
| N04 | P2 | 워밍업을 한 행 더 요구해 첫 월말 신호 누락 | 정확히 필요한 행 수가 월말에 충족됨 |
| N05 | P2 | 조합의 현금 자료 공백을 무제한 채움 | 현금 ETF가 중간에 끊기고 양의 현금 잔액을 보유 |

선정 품질에 직접 영향을 주는 N03, 체결 시점의 N01, 비교 성과의 N02/N05를 먼저 보완하고, N04의 시작 경계를 함께 고치는 것이 좋다. 이번 리뷰는 제품 코드를 수정하지 않았다.

## 범위·리비전·환경

- 시작 HEAD: `60a8cf65bf0a92c20464a6ced188bdc6c904fb4d`, 작업 트리 clean.
- **구현 실행 기준 HEAD: `8f839d3fa8915ce480235e4bf7e03632f0b529b2`.** 검토 중 8d401a1/8f839d3가 추가되었다. 상품명·문서·계좌 목록 운영 원칙 변경이며 구현과 테스트 코드는 시작 HEAD와 동일하다. 이후 4d58a48에 추가된 전체 ETF 유니버스 설계도 읽었다. 종료 확인 HEAD는 **64f0440**으로 구현 계획 문서가 추가된 상태다. 8f839d3 이후 차이는 이 두 문서뿐이고, 실행한 구현·설정·테스트에는 차이가 없다. 계획서는 목적·구성·제약·태스크 개요를 확인했으며 제시된 향후 구현 코드를 실행 검증한 것은 아니다.
- 초기 실제 캐시 실행은 리비전이 섞였으므로 최종 수치의 근거로 사용하지 않았다. 로컬 clone `/private/tmp/etf-review-20261010/snapshot`에서 최종 HEAD를 고정하고 전체 테스트·합성 검산·캐시 CLI를 다시 실행했다. `PYTHONPATH`로 snapshot의 src를 사용했으며 실제 import 경로도 확인했다.
- 원본 작업 트리의 기존 변경을 보존했다. 제품 코드·전략·유니버스·기존 테스트·운영 이력·공식 결과·원자료 캐시를 수정하거나 커밋하지 않았다. 새 리뷰 문서만 추가했다.
- 고정 clone의 `data`, `results`는 원본 자료를 읽기 위한 symlink다. Git이 이 링크를 untracked로 판단하여 실험 메타데이터의 `dirty=true`가 남는다. 구현 변경 때문은 아니다. 최종 캐시 실행 11개 메타데이터의 commit은 모두 위 구현 실행 기준 HEAD다. **공식 연구 결과 생성이 아닌 리뷰 실험**으로 구분한다.
- 읽은 영역: 모든 `src/etf_momentum` 모듈, CLI 호출 경계, `scripts/research_tables.py`, 전략/조합 JSON·유니버스, 테스트, README/CLAUDE/pyproject, 현행 설계와 이전 리뷰. 적용되는 별도 AGENTS.md는 작업 영역에서 발견하지 못했다.
- 적용 스킬: `.agents/skills/etf-momentum-code-review/SKILL.md`와 두 references. 독립 리뷰어 1명이 전체 구현을 읽고 합성 검산했다. 주 리뷰어의 예상 버그를 전달하지 않았고, 독립 지적은 주 리뷰어가 직접 재현했다. 독립 검토는 시작 HEAD에서 했으며 최종 HEAD와 구현이 같음을 확인했다.
- 실행 환경: Python 3.14.3, pandas 3.0.3, numpy 2.4.6, pytest 9.0.3, exchange-calendars 4.13.2, yfinance 1.3.0. 기존 가상환경을 사용하고 의존성을 설치하지 않았다.
- 네트워크 다운로드 없음. 합성 입력과 기존 로컬 CSV를 사용했다. CLI 다운로드만 reader/mock으로 대체하고 선정·회계·출력 함수는 실제 구현을 실행했다. 산출물은 `/private/tmp/etf-review-20261010`에 분리했다.

## 확인된 발견사항

### N01 — P2: 신호일만 거래일로 보정하고 next_close는 다음 가격표 행에서 체결한다

위치: `src/etf_momentum/backtest.py:78–90,108–115`.

최근 수정은 월말 신호일을 벤치마크의 실제 관측일로 고른다. 그러나 `next_close`의 `trade_pos`와 보유 종료 위치는 여전히 **전체 가격표 위치 +1**이다. 월말 다음 행이 벤치마크 비거래일인 경우에도 그 행에서 체결한다. 한 시장만 허용하는 CLI 검증으로는 같은 시장의 잘못된 날짜 행을 막지 못한다.

기대 동작의 근거는 `EXECUTION_CHOICES`의 다음 거래일 종가 체결 정의와 README의 휴장 행에서는 리밸런싱하지 않는 규칙이다. 신호일·체결일·최초 수익일이 같은 평가 달력에 따라야 한다.

재현: 국내 시장을 모사한 2025-01-02~2026-02-27 가격표에서 2026-01-01은 제거하고, 2025-12-31에는 A만 가격이 있는 잘못된 행을 남겼다. 벤치마크/현금 원자료는 그날 NaN이다. 짧은 워밍업 설정으로 `run_backtest(..., execution='next_close', observed=raw.notna())`를 실행했다.

| 항목 | 기대 | 실제 |
|---|---|---|
| 12월 신호일 | 2025-12-30 | 2025-12-30 |
| 다음 체결일 | 2026-01-02 | **2025-12-31** |

설치된 exchange-calendars 4.13.2의 XKRX에서 12-31이 세션이 아니고 01-02가 세션임을 별도로 확인했다. 외부 거래소 자료와 새로 대조한 것은 아니다. 합성 입력의 목적은 야후 휴장 행 처리 경계를 재현하는 것이다.

영향: 변경된 보유가 한 실제 거래일 먼저 수익을 얻거나 비용이 휴장일에 부과될 수 있다. 합성 재현은 체결 날짜 오류를 확인했으며, 특정 실거래 비용/성과 차이를 측정한 것은 아니다. 기본 실제 캐시의 이번 실행에서는 해당 날짜 오류를 확인하지 않았다.

수정 방향: 체결 지연과 다음 리밸런싱 체결 경계도 기준 시장의 거래일에서 계산한다. 평가 인덱스와 lookback에 휴장 행을 어떻게 처리할지도 일관되게 정한다. **벤치마크 결측인 실제 거래일**과 **시장 휴장일**은 구분해야 하므로, 결측을 무조건 휴장으로 삭제하는 방식은 피한다.

회귀 조건: 신호일 직후 휴장 행, 다음 신호일 직후 휴장 행, 마지막 가격표 끝의 휴장 행, 실제 거래일의 벤치마크만 결측. trade_date뿐 아니라 새 보유의 첫 수익일과 이전 보유의 종료일도 비교한다.

### N02 — P2: 벤치마크의 과거 장기 결측은 새 비교 곡선 검사를 통과한다

위치: `src/etf_momentum/backtest.py:71,76,161–165`, `src/etf_momentum/cli.py`의 `_require_fresh`, `_benchmark_baseline` 및 walkforward의 벤치마크 수익 생성 경로.

현재 필수 데이터 최신성 검사는 입력 마지막 날에 수행한다. 시장 필터는 선정일마다 검사하지만, 보유 중간 검사는 실제 보유 티커만 검사한다. 벤치마크가 월중 오래 끊겼다가 월말 전에 재개되면 백테스트는 그 구간을 ffill하고 일간 수익 0으로 계산한다.

워크포워드에 추가된 `_benchmark_baseline`도 NaN 수익률만 검사한다. 그 전에 가격을 채워 수익이 **유한한 0**이 되었으므로 원자료 공백을 탐지하지 못한다. README의 비교 곡선에서 빈 수익률을 0으로 채우지 않는다고 설명한 방어가 입력 단계까지 연결되지 않았다.

재현: 2023-01-02~05-31 상승 가격에서 SPY만 2023-03-06~03-20의 11행을 NaN으로 만들었다. A와 BIL은 정상, 최신·월말 SPY도 정상이다. 시장 필터를 켜고 짧은 lookback/SMA/변동성 설정으로 실행했다.

| 검사 | 실제 |
|---|---|
| `_require_fresh` | 통과 |
| `run_backtest`, risk_on_benchmark=True | 성공 |
| 원자료 SPY 공백 | 11행 |
| 결과 benchmark 일간 수익 | 해당 11행 모두 0.0 |
| 같은 수익률의 `_benchmark_baseline` | 통과 |

기대: 장기 원자료 공백을 오류 또는 명시적인 비교 불가로 처리해야 한다. 실제 미관측을 관측된 보합 수익과 구분해야 한다. 시장 필터가 매일 사용된다는 뜻은 아니며, 이 재현에서 월말 risk-on 판정 자체가 틀렸다고 주장하지 않는다.

영향: 결측 중 일간 변동성이 사라지고 재개일에 변화가 몰려 변동성·Sharpe·낙폭 경로를 왜곡할 수 있다. 시작/종료 가격이 같으면 CAGR은 같을 수 있으므로 전체 성장률까지 항상 틀린다고 일반화하지 않는다. 상대강도 계산의 앵커가 공백에 걸리는 별도 설정도 주의해야 한다.

수정 방향: 비교 곡선 생성 전에 벤치마크 원관측표를 검사한다. 짧은 휴장 허용과 장기 결측을 구분하고, 평가에 필요한 구간의 실제 데이터 유효성을 수익률 배열과 함께 전달한다. 원자료 검사 없이 NaN 수익 검사만 추가해서는 해결되지 않는다.

### N03 — P2: 프록시 거래대금은 원자료 관측일을 잃어 유동성 검사를 우회한다

위치: `src/etf_momentum/cli.py:1334–1337`, `src/etf_momentum/data.py`의 `splice_proxies`/`_asof_previous_day`, `src/etf_momentum/signals.py:235–239`.

가격 관측표의 최근 수정은 프록시 가격과 환율의 실제 새 관측을 보존한다. 하지만 **거래대금**은 같은 `splice_proxies(..., scale=False)`에서 무제한 as-of fill을 한다. `_average_traded_value`는 채워진 숫자를 실제 관측으로 보아 trailing_missing=0으로 계산한다. 가격이 신선하면 오래된 거래대금만으로 후보가 통과한다.

README의 거래대금 마지막 실제 관측이 5영업일 넘게 오래되면 유동성 미달이라는 규칙과 충돌한다. 프록시를 쓴다는 이유로 국내 실제 유동성을 확정할 수 없는 모델 한계와 별개다. 프록시 자료 자체의 오래된 관측도 신선한 것으로 만들면 안 된다.

재현 입력:

- 국내 A.KS는 2023-06-26부터 실제 가격/거래대금이 있다.
- US 프록시 가격과 KRW=X=1300은 계속 관측된다.
- US 거래대금은 2,000,000이지만 **2023-04-28 이후 NaN**이다.
- `_apply_proxies` → `_observations` → `rank_momentum`을 실제 경로로 실행한다. 유동성 창=10, 최소 거래대금=1e9, 평가일=2023-06-20.

| 항목 | 실제 |
|---|---|
| 마지막 실제 프록시 거래대금 | 2023-04-28 |
| 판정일 | 2023-06-20 |
| `avg_traded_value` | **2,600,000,000 = 2,000,000 × 1300** |
| `price_fresh`, `liquid`, `eligible` | **모두 True** |
| 기대 | 거래대금 최신성 미달, liquid=False/eligible=False |

영향: 유동성 필터를 켠 선정과 과거 백테스트가 끊긴 프록시 거래대금을 정상 유동성으로 취급할 수 있다. 기본 전략이 프록시와 유동성 필터를 함께 사용하므로 중요한 경계다. 이번 기본 캐시에 이 장기 거래대금 단절을 주입하지 않았고, 실제 기본 선정이 이 문제로 바뀌었다고 확인한 것은 아니다.

수정 방향: 가격과 별도로 거래대금 원천의 관측 날짜/유효 마스크를 유지한다. as-of 정렬 시 stale source를 NaN 또는 명시적 부적격으로 표시하고, 평균의 관측 수를 채운 행 수로 세지 않는다. 같은 US 거래대금 관측을 여러 local 행에 복제할 때 평균 표본 수의 의미도 정한다. 환율 원자료 최신성은 동일하게 보존한다.

회귀 조건: 가격 정상/거래대금만 중단, 거래대금 재개, 짧은 휴장, FX만 중단, 상장일 전후, 자료 완전 누락. 이 재현은 창 10으로 축소했지만 관측일 소실은 기본 창 63에서도 동일하다.

### N04 — P2: 충분한 이력의 첫 월말을 워밍업에서 한 번 더 제외한다

위치: `src/etf_momentum/backtest.py:91–96`, 비교 위치: `src/etf_momentum/signals.py:103–109`.

`min_history`는 필요한 **행 개수**다. `prices.index.get_loc(date)`는 0부터 시작하는 **위치**인데 `>= min_history`로 비교한다. 정확히 min_history행이면 마지막 위치는 min_history-1이므로 백테스트는 그 월말을 건너뛴다. 공통 신호 함수는 같은 데이터의 이력을 충분하다고 본다.

재현: 2023-01-24부터 04-28까지 영업일, 상승 A/BENCH, CASH=100. lookback=(2,3,4), skip=0, SMA=3, 변동성 창=3. 01-31까지 정확히 6행이고 요구 행 수도 6이다.

| 항목 | 기대 | 실제 |
|---|---|---|
| Jan31 `rank_momentum` eligible | True | True |
| 백테스트 첫 신호/성과 기준일 | 2023-01-31 | **2023-02-28** |

이력 준비의 하루 차이가 월말 신호 한 개와 한 달 평가 구간의 누락으로 커진다. 일반적인 시작일 모두에서 발생하는 것은 아니며, 이번 고정 기본 캐시의 시작 경계에는 해당하지 않았다. 설정·입력 시작일에 따라 발생하는 계산/명령 일관성 문제여서 P2로 분류했다.

수정 방향: 위치+1을 행 수와 비교하거나 `get_loc >= min_history-1`로 맞춘다. rank와 backtest의 준비 상태 정의를 함께 검토한다. 이 리뷰는 모멘텀 식 자체의 최소 수학적 표본 수를 바꾸라고 요구하는 것이 아니라, 현재 신호 함수가 허용한 이력과 백테스트 경계의 차이를 지적한다.

회귀 조건: 필요 행 수 -1/정확히/+1인 월말, SMA가 최대 창인 경우, 변동성 창이 최대인 경우, same_close/next_close.

### N05 — P2: portfolio 현금 ETF의 장기 공백은 보유 결측 검사를 받지 않는다

위치: `src/etf_momentum/cli.py:1044`의 cash_returns 생성, `src/etf_momentum/portfolio.py:178`의 현금 수익 fillna와 이후 reserve 이자 적용.

포트폴리오의 일반 same_day 슬리브는 원자료로 정렬하고 내부 결측을 거부한다. 그러나 cash_ticker는 처음의 합친 계산용 가격표에서 꺼내 다시 ffill/pct_change한다. 같은 평가 달력의 현금 ETF에 긴 공백이 있어도 마지막 가격을 무제한 연장하고 현금 잔액에 0% 수익을 적용한다. 시장 필터/백테스트의 held 검사와 별도 경로다.

README의 1배 미만 노출에서 현금 ETF 수익을 받는다는 기능을 정확히 평가하려면 현금 입력도 보유 자료로 검증해야 한다. 현금 ETF 없음에 대한 명시적 미투자 현금 가정과, 지정한 현금 ETF의 중간 결측을 섞어서는 안 된다.

실제 CLI 재현: CAL/GOOD/CASH가 2024-01-02~04-30의 86행을 갖는다. CASH만 02-01~03-15의 **32행을 NaN**으로 만들고 GOOD 비중 1, leverage=.5로 평가했다. 다운로드 함수만 합성 raw/ffill reader로 대체했다.

| 항목 | 실제 |
|---|---|
| `portfolio` 실행 | 성공, 곡선 86행 |
| 현금 원자료 결측 | 32행 |
| 적용된 현금 수익 | 그 32행 모두 0% |
| 기대 | 장기 현금 입력 공백을 오류/평가 불가로 표시 |

영향: 양의 reserve가 있는 시나리오의 수익 경로·월말 재조정·위험 지표가 미관측 현금 가격 가정에 의존한다. 현금 수익을 무위험 수익으로 쓰므로 reserve가 없는 시나리오에서도 Sharpe 입력에 영향을 줄 수 있다. 단순 보유의 최종 수익이 항상 달라진다는 주장이나 누락 기간의 진짜 수익을 추정한 것은 아니다.

수정 방향: cash_ticker도 자체 raw 날짜로 읽고 평가 달력에서 관측 유효성을 검사한다. 내부 장기 공백과 실제 휴장을 구분하고, 상장 전 현금을 허용한다면 별도 명시적 예외로 둔다. 더 큰 배분/계좌 한도 기능을 추가하라는 요구가 아니라 기존 현금 평가 기능의 입력 검증이다.

## 이전 발견사항 재검증

이전 [전체 리뷰](review_2026-10-09_full_codex.md)와 [재검토](review_2026-10-09_recheck_codex.md)의 결론을 현재 코드의 사실로 그대로 사용하지 않았다.

| 이전 항목 | 현재 결과와 증거 |
|---|---|
| R01 FX freshness 허용 중첩 | 해결. 새 FX 없이 5행=True, 6행=False, 10행=False를 직접 재현 |
| R02 잘린 신호 기록 append | 해결. 15필드 행은 ValueError, 오류 뒤 파일 바이트 불변. 완전한 행/끝 newline 없음 및 인용 필드 중단은 기존 추가 테스트 통과 |
| R03 0비중 슬리브 CLI 차단 | 해결. 모든 시나리오에서 0인 USD 슬리브에 없는 파일/없는 FX 설정을 넣어도 성공하며 곡선 완전 동일 |
| R04 중복 ticker 비중 손실 | 해결. 공백/소문자 중복 CSV는 행 번호와 함께 거부. 라이브러리 반복 후보 ('A','A')도 순위 1행만 생성 |
| F01/F04 혼합 시장 시점·행 수 | 선정 CLI에서 다운로드 전 거부. 직접 라이브러리 시장 분리는 호출자 책임이라는 제한 유지 |
| F02 프록시 가격 중단 | 평가 2020-02-28에 last_observed=01-16, fresh=False/eligible=False. N03의 거래대금은 별도 잔존 경로 |
| F03 rank cash 다운로드/검사 | MISSING도 요청하고 데이터 없으면 명시적 중단 |
| F05 보유 현금 공백 | 백테스트의 101행 공백은 StaleDataError. N05의 portfolio는 별도 미검증 경로 |
| F06 zero sleeve 평가 기간 | 라이브러리 A만/A=1,B=0 곡선 동일, R03 CLI도 해결 |
| F07 최초 조합 비용 | 무수익 2일·100bps: [1.0,.99], 측정 종료가치 .99 |
| F08 NaN 가중치 | 다운로드 전 종료 코드 2 |
| F09 선택된 테스트 수익 결측 | WIN의 64개 테스트 결측을 ValueError로 거부 |
| F10 사용자 임계값 문구 | 50%/30% 설정의 실제 임계값 표시. 해당 기존 재현 해결 |
| 추가 보완: 관측 없음 설명 | 실제 마지막 관측일 표시 경로 및 테스트 확인 |
| 추가 보완: 고정 비교 곡선 NaN | 부분 결측 설정 제외/전부 결측 오류 테스트 통과. 원가격 공백이 이미 0수익이 된 N02는 검사 밖 |
| 추가 보완: 벤치마크 월말 거래일 | 신호일 휴장 행 제외 테스트 통과. next_close의 N01은 별도 잔존 경계 |

## 독립 계산 검산

기대값은 제품 계산 함수를 재호출해서 만들지 않고 아래 수식으로 계산했다. 부동소수점 오차 허용은 절대 1e-12, 불변성은 Series/DataFrame 동일성 검사다.

| 항목 | 입력·기대식 | 현재 결과 |
|---|---|---|
| 모멘텀 | 700행 A=100×1.002^n, L=63/126/252, skip21. Σw×(P[t-21]/P[t-L-21]-1) | 기대/실제 0.38792207964425396 |
| 미래 정보 불변 | 평가일 뒤 A 가격 ×100 | 과거 순위표 동일 |
| buy-and-hold | .5/.5, A +10%,-10%, B=0. `.5×1.1×.9+.5` | .9950000000000001 |
| daily rebalanced | 위 입력, `1.05×.95` | .9974999999999999 |
| drift | 최종 A 비중 `.495/.995` | .49748743718592964 |
| 상한 | [.9,.1] cap.6 재분배 합1, A만 cap.6이면 합.6 | 두 조건 통과 |
| CAGR | [1,1.1,.99,1.2], 2020-01-01~2021-01-01. `1.2^(365.25/366)-1` | .19955175204308273 |
| MDD | `.99/1.1-1` | -.10000000000000009 |
| Sharpe | 수익 [.1,-.1,.2], rf .01. 초과수익 평균/표본표준편차×√252 | 기대 5.888972745734183 / 실제 5.888972745734187 |
| 환산/이전 날짜 | US +10%, FX +20%, `1.1×1.2-1` | .32000000000000006, 평가일 당일 극단값 미사용 |
| 주말 차입 | 금→월 3일, 2배, 자산+1%, 연 이율 .365. `2×1.01-(1+.365×3/365)` | 1.0170000000000001 |
| 현금 reserve | .5배, 자산+10%, cash+2%. `.5×1.1+.5×1.02` | 1.06 |
| 최초 매수비용 | 무수익,100bps | [1.0,.99], 비용 .01 |
| same_day 중간 NaN | [100,NaN,110] | 오류로 중단 |
| 월말 거래소 관측 누락 | 09-15/10-01만 있고 마지막 세션 09-30 | 오류로 중단 |
| 워크포워드 미래 불변 | 2022년 이후 수익 -.4로 변경 | 2021년 첫 선택 동일 |

독립 리뷰어의 별도 검산에서도 자산 둘이 차례로 +10%인 경우 buy-and-hold=1.1/daily-rebalanced=1.1025, 이전 US/FX의 21% 환산, 초기 비용을 확인했다. 모멘텀 분모에도 skip이 적용되므로 일반적 12-1과 자동으로 같은 식이라고 설명하지 않는다.

## 실제 캐시 측정

최종 HEAD의 로컬 clone에서 2010-01-01~`--end 2026-10-07`로 고정했다. end는 배제되며 마지막 관측은 2026-10-06이다. 원자료 중 요청 티커를 모두 가진 최소 열 수의 캐시를 읽고 기간/열을 자른다. raw 여부에 따라 ffill하는 reader로 실제 CLI 다운로드만 교체했다.

### 기본 연금 전략

전략 파일 pension/retirement 각각 rank·weekly·same_close backtest·next_close backtest를 실행했다. rank/weekly는 lookback-years20, end고정, weekly no-save-history다. backtest는 start2010-01-01, 비용10bps, buy-and-hold다. 출력은 임시 경로이며 운영 신호를 추가하지 않았다.

- 현재 rank/weekly 랭킹 CSV 완전 동일. 관찰 날짜와 직전 월말 선정 날짜를 혼동하지 않았다.
- 두 계좌의 2026-09-30 선정: **139260.KS,292150.KS,102780.KS,381180.KS**.
- 해당 월말 선택과 두 체결 방식 백테스트의 신호일 보유 티커 일치.
- 백테스트마다 equity 3,810행, positions188행. 187개 hold_until이 다음 trade_date와 모두 일치.

주 평가 기간 **2016-02-29~2025-02-28**의 측정:

| 계좌 | 체결 | CAGR | MDD |
|---|---|---:|---:|
| 연금저축 | same_close | 11.597384% | -24.776565% |
| 연금저축 | next_close | 10.303659% | -24.599838% |
| 퇴직연금 | same_close | 9.152717% | -17.541112% |
| 퇴직연금 | next_close | 8.283887% | -17.416585% |

이전 리뷰의 같은 입력·가정과 수치가 표시 정밀도에서 일치했다. 기본 캐시 정상 실행에서 새 버그가 성과를 바꿨다고 확인한 것은 아니다.

### 다른 CLI와 연구 경로

- US sector/SPY/BIL의 compare: top_n2/3, equal, weighted_momentum, ME, 상대강도 off, 비용5bps. 비교2행 성공.
- 같은 작은 워크포워드 그리드: group1/riskoff, 학습5년/테스트12개월. 설정2개·테스트창11개 성공.
- portfolio_pension.json: summary18행, equity4,091행 성공. 전략 슬리브는 기존 저장 곡선3개를 읽었으며 이번 리뷰가 그 원천 실행을 재생성한 것은 아니다. 생성 이력과 해시는 부록에 남겼다.
- research_tables.py의 기간 절단·기준값·무위험 수익률·표 생성은 정적 검토했다. 전체 연구 그리드와 모든 연구 표 재생성은 하지 않았다.

## 검증 범위와 제한

| 영역 | 수행한 확인 |
|---|---|
| 신호·선정 | 독립 수식, 미래 불변, 후보 중복, 임계값 설명, 동일 전략 CLI/백테스트 선정 비교, 전체 관련 테스트 |
| 원자료·캐시·유동성 | raw/ffill 분리 경로, 부분 실패/캐시 검사 구현과 테스트, 실제 캐시 reader 실행, 프록시 거래대금 결측 직접 재현 |
| 시점·달력 | strictly-before 환산, 월말 누락 오류, 실제 관측 신호일, 휴장 행 next_close 경계 직접 재현 |
| 회계·성과 | 수량/비중 drift, 상한, 비용, CAGR/MDD/Sharpe, 현금/차입 수식, portfolio 현금 공백 직접 재현 |
| 워크포워드 | 미래 불변, 선택된 테스트 결측, 고정/벤치마크 비교 검사, 작은 실제 그리드 실행 |
| 설정·기록 | strategy 필드/해시/충돌·계좌 후보·snapshot 경로 정적 검토 및 전체 테스트, 잘린 CSV/미사용 슬리브 직접 재현 |

전체 pytest는 시작 작업 트리에서 284 passed/26.36초, 최종 고정 clone에서 **284 passed/10.60초**였다. 반복은 검토 중 리비전 변경으로 최종 근거를 고정하기 위해 수행했다. 추가 경계 스크립트의 통과는 버그 없음이 아니라 **문서에 쓴 현재의 잘못된 동작을 assert하여 재현했다는 뜻**이다. 기존 테스트 개수에 추가 실험을 합치지 않았다.

최종 실행은 아래 환경과 경로를 사용했다.

```sh
# cwd: /private/tmp/etf-review-20261010/snapshot
env PYTHONPATH=/private/tmp/etf-review-20261010/snapshot/src \
    MPLCONFIGDIR=/private/tmp/etf-review-20261010/mpl \
    XDG_CACHE_HOME=/private/tmp/etf-review-20261010/cache \
    /Users/dc_macair/Documents/Research/etf_momentum_strategy/.venv/bin/python -m pytest -q
```

동일한 cwd/PYTHONPATH에서 `boundaries.py`, `normal_and_prior.py`, `arithmetic.py`, `snapshot_runs/cached_checks.py`를 실행했다. 증거 JSON·CLI log·CSV·메타데이터는 임시 디렉터리에 보관했다. 아래 부록 코드는 임시 파일 정리 후에도 핵심 경계를 다시 재현할 수 있도록 포함한다.

검증 제한과 현재 명세:

- 최신 CLAUDE/연구 문서에는 2025-03~2026-10 국내 자료를 네이버와 대조했고 139280/139290 상품명을 수정했다고 기록되어 있다. 그 변경을 읽고 반영했지만 **이번 리뷰에서 외부 시세를 독립적으로 다시 대조하지 않았다**. 과거의 미대조/미확인 문구를 현재 프로젝트의 미해결 사실로 반복하지 않는다.
- 최신 정책에 따라 증권사별 계좌 목록 대조는 필수 작업으로 제안하지 않는다. 연금저축은 유니버스 전체를 매수 가능으로 가정하고 실제 매수 불가 선정은 사용자가 건너뛴다. 퇴직연금은 규정상 파생형 제외다. 실제 보유와 연구 곡선이 달라질 수 있는 운영 가정이며, 별도 배분/주문 기능 요구로 바꾸지 않는다.
- 새 `docs/superpowers/specs/2026-10-10-full-etf-universe-design.md`와 이어서 추가된 구현 계획은 국내 전체 ETF 후보·필터·상관 그룹·v2 전략의 계획이다. 검토한 리비전에서는 builder/새 유니버스 구현이 아직 없으므로 향후 구현을 검증했다고 주장하지 않는다. 설계가 명시한 현재 목록의 생존 편향과 현재 상관 그룹의 사후 정보를 연구 비교용 제한으로 구분한다. 현 60개 결과를 대체하지 않는다는 계획은 현재 컨셉과 양립한다. 네이버 API 목록 수·분류의 외부 진실성은 이번에 확인하지 않았다.
- 프록시 이력은 실제 상품의 상장 전 성과나 국내 실제 유동성 증명이 아니다. 보수·추적오차·현재 상장일 연결 배율은 모델 가정이다.
- same_close는 종가 신호를 같은 종가에 체결하는 가정이다. 워크포워드 설정 전환의 추가 비용·보유 상태는 완전한 실거래 엔진처럼 연결되지 않는다. 명시된 모델 한계와 N01의 실제 날짜 오류는 구분한다.
- 실제 주문·호가·체결, 온라인 실패 상황, 동시 기록 쓰기, 지원하는 모든 Python/의존성 조합은 실측하지 않았다.
- 설정/유니버스 해시만으로 가격 자료가 고정되지 않는다. 실제 입력 파일 해시를 별도로 기록했다. 시세 진실성이나 실거래 적합성 전체를 승인한 것은 아니다.

컨셉에 필요한 핵심 구조는 구현되어 있다. 다음 보완은 기능 확대보다 **원자료 유효성이 신호·체결·비교 곡선·현금 회계까지 끊기지 않고 전달되게 만드는 것**에 집중하는 것이 좋다.

## 부록 A — 재현 코드

다음은 실행한 `boundaries.py` 원문이다. 저장소 루트에서 기존 가상환경으로 실행할 수 있다. 다운로드를 mock으로 대체하고 모든 출력은 임시 디렉터리로 보낸다. 같은 임시 경로를 다시 실행하면 리뷰용 파일만 덮어쓴다. N01~N05의 현재 잘못된 동작 및 이전 R01~R04의 수정 동작을 함께 assert한다.
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

OUT=Path('/private/tmp/etf-review-20261010')
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

## 부록 B — 실제 입력 해시와 생성 이력

고정 clone에서 최종 측정 시 읽은 내용의 SHA-256이다. 해시는 원자료 진실성 검증이 아니다.

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
| `config/strategies/pension.json` | `f366951f0e33bcdf726d6aa1ec4afd33b1c18f78702195ad7c071f81ccdf72a0` |
| `config/strategies/retirement.json` | `fd0d6648ab66585540e0a533f025ba70165336cec59226cac0d52c9595cdf42a` |
| `config/universe_kr_pension.csv` | `d886e53918845effa9bdee99bf4f996844eb48296b137a6e42f6e64b98e31b3a` |
| `config/universe_us_sectors.csv` | `759d061f56faa692d89c12d73a6d93aaafc64968dc987271a646fe1b3f4f2f2b` |
| `config/portfolio_pension.json` | `a5da6a8368ff533e5914466ed742f3d550267ef971813100b4f1201b22b2f13f` |
| `results/walkforward/kr_pension_pension_10eok_10bp_robustgrid_tvoff/walkforward_equity.csv` | `652d8effcb6f94ab0548c64d5ca1c5c29fdd2669f50125869d1f766d58f05e5c` |
| `results/backtest/pension_fixed_top4_equal/equity_curve.csv` | `ab1fa47ee4e91b3f3211e1d3b599ffa0f8b1ac6d57a1c973928a3c3a875c241d` |
| `results/backtest/pension_fixed_top4_inverse_vol/equity_curve.csv` | `23099c51e460392079182f31fcb9dbfe1ce763ae4d4bb6ac97640afffd815741` |

조합의 기존 전략 곡선 생성 이력(이번 리뷰에서 원천 실행은 재생성하지 않음):

| 슬리브 | 생성 시각 | code_revision |
|---|---|---|
| PEN_mom | 2026-10-09T11:56:07+09:00 | `{'commit': '60a8cf65bf0a92c20464a6ced188bdc6c904fb4d', 'dirty': False}` |
| PEN_fixed_equal | 2026-10-09T12:06:26+09:00 | `{'commit': '60a8cf65bf0a92c20464a6ced188bdc6c904fb4d', 'dirty': False}` |
| PEN_fixed_inverse_vol | 2026-10-09T12:06:40+09:00 | `{'commit': '60a8cf65bf0a92c20464a6ced188bdc6c904fb4d', 'dirty': False}` |

## 부록 C — 로컬 원자료 CLI 실험 코드

실행 당시 `snapshot_runs/cached_checks.py` 원문이다. 네트워크를 사용하지 않고 기존 원자료만 읽는다. 해당 원자료/전략 곡선이 없으면 명시적으로 실패한다. 출력 디렉터리의 부모(`/private/tmp/etf-review-20261010/snapshot_runs`)를 먼저 만든 뒤, 검토 리비전의 저장소 루트에서 실행한다. 원본 자료가 뒤에 바뀌면 부록 B 해시와 대조해야 한다.

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

out=Path('/private/tmp/etf-review-20261010/snapshot_runs/cached');out.mkdir(exist_ok=True)
sources=[(p,pd.read_csv(p,index_col=0,parse_dates=True)) for p in sorted(Path('data').glob('prices_raw*2010-01-01_2026-10-07.csv'))]
tv=[(p,pd.read_csv(p,index_col=0,parse_dates=True)) for p in sorted(Path('data').glob('traded_value*2010-01-01_2026-10-07.csv'))]
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
  rp=run(f'{account}_rank','rank','--strategy',s,'--lookback-years','20','--end','2026-10-07')
  wp=run(f'{account}_weekly','weekly','--strategy',s,'--lookback-years','20','--end','2026-10-07','--no-save-history')
  rr=pd.read_csv(rp/'latest_ranking.csv');wr=pd.read_csv(wp/'latest_ranking.csv')
  pd.testing.assert_frame_equal(rr,wr)
  selection=pd.read_csv(rp/'latest_selection.csv');selected=selection.loc[selection.status=='selected','ticker'].tolist()
  res[account]={'selection_date':str(selection.as_of.iloc[0]),'selected':selected,'ranking_weekly_equal':True,'executions':{}}
  for execution in ['same_close','next_close']:
   bp=run(f'{account}_{execution}','backtest','--strategy',s,'--start','2010-01-01','--end','2026-10-07','--transaction-cost-bps','10','--execution',execution)
   eq=pd.read_csv(bp/'equity_curve.csv',index_col=0,parse_dates=True);pos=pd.read_csv(bp/'positions.csv')
   held=pos.loc[pos.rebalance_date==selection.as_of.iloc[0],'tickers'].iloc[0].split(',')
   assert sorted(t for t in held if t!='153130.KS')==sorted(selected)
   assert (pos.hold_until.iloc[:-1].to_numpy()==pos.trade_date.iloc[1:].to_numpy()).all()
   main=eq.loc['2016-02-29':'2025-02-28'];met=summarize_performance(main/main.iloc[0])
   res[account]['executions'][execution]={'main_cagr':met['strategy_cagr'],'main_mdd':met['strategy_max_drawdown'],'full_rows':len(eq),'positions':len(pos),'monthend_selection_matches':True,'hold_until_matches_next_trade':True}
 # Exercise both grid commands through real cached data, with a bounded grid.
 common=['--universe','config/universe_us_sectors.csv','--benchmark','SPY','--cash','BIL','--start','2010-01-01','--end','2026-10-07','--top-n-grid','2,3','--weighting-grid','equal','--rank-by-grid','weighted_momentum','--rebalance-grid','ME','--relative-strength-grid','off']
 cp=run('us_compare','compare',*common,'--transaction-cost-bps','5')
 fp=run('us_walkforward','walkforward',*common,'--max-per-group-grid','1','--risk-on-grid','off','--train-years','5','--test-months','12')
 res['small_grid']={'comparison_rows':len(pd.read_csv(cp/'comparison.csv')),'wf_windows':len(pd.read_csv(fp/'walkforward_windows.csv')),'wf_grid_rows':len(pd.read_csv(fp/'walkforward_grid.csv'))}
 pp=run('portfolio','portfolio','--spec','config/portfolio_pension.json')
 res['portfolio']={'summary_rows':len(pd.read_csv(pp/'portfolio_summary.csv')),'curve_rows':len(pd.read_csv(pp/'portfolio_equity.csv'))}
res['source_files']=sorted(set(used))
res['versions']={k:importlib.metadata.version(k) for k in ['pandas','numpy','pytest','exchange-calendars','yfinance']}
(out/'results.json').write_text(json.dumps(res,indent=2,ensure_ascii=False))
print(json.dumps(res,indent=2,ensure_ascii=False))

```
