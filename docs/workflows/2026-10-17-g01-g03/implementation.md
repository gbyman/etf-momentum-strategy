# G01~G03 구현 확인·수정 보고 (Claude)

- 작성일: 2026-10-17 (Asia/Seoul)
- Orca: Run `run_2d8c6f88a708`, Task `task_760c6f74f201`, Dispatch `ctx_9208e5ea469e`
- 임시 증거: `/private/tmp/etf-orca-g01-g03-20261017/claude/`

## 1. 실행 주체와 모델 확인 근거

- 이 세션의 시스템 프롬프트에 "Opus 5.5, 모델 ID `claude-opus-5-5`"로 적혀 있다.
- 작업 직전 사용자가 `/model`을 실행했고, 출력은 "Kept model as `Opus 5.5`"였다. `/effort` 출력은 "Set effort level to high"였다.
- 위 두 가지는 모델의 자기 보고와 클라이언트 표시다. API 응답 헤더 같은 외부 증거로 모델을 독립 확인하지는 못했다.
- 추가 worker나 하위 에이전트는 만들지 않았다. 모든 확인과 수정은 이 세션에서 직접 했다.

## 2. 시작·종료 상태

| 항목 | 값 |
|---|---|
| 시작 HEAD | `e596368be6d76bd604361b3a1d84d8af9a978399` |
| 종료 HEAD | 같음 (커밋하지 않음) |
| 시작 `git status --short` | `?? docs/orca_multi_agent_workflow.md` (`initial_snapshot.json` 기준), 이후 GPT가 `docs/workflows/`를 생성 |
| 종료 `git status --short` | ` M src/etf_momentum/backtest.py`, ` M src/etf_momentum/cli.py`, `?? docs/orca_multi_agent_workflow.md`(손대지 않음), `?? docs/workflows/`(이 보고서 포함), `?? tests/test_g01_selection_sessions.py` |
| 환경 | `.venv/bin/python` (Python 3.14.3). 모든 실행에 `MPLCONFIGDIR`·`XDG_CACHE_HOME`을 임시 경로로 지정. 네트워크 미사용 |

커밋·푸시·공식 결과 생성은 하지 않았다. `data/`, `results/`, 신호·주간 이력, 기존 리뷰 문서도 수정하지 않았다. `find data results -newer docs/workflows/2026-10-17-g01-g03/task.md -type f`의 결과는 0건이다.

## 3. 요구사항별 확인 결과

### G01 — 상위 순위 경로 (HEAD에서 이미 해결, 수정 없음)

다음 경로는 e596368에서 이미 거래소 세션을 받는다.
- `observed_freshness(mask, index, sessions)`: 행 수와 세션 수 중 큰 쪽을 공백 길이로 센다.
- `rank_momentum(..., sessions)`
- CLI `_require_fresh(observed, required, sessions)`: 5곳에서 호출한다.
- CLI `rank`·`weekly`·`backtest`의 최신 순위, `_month_end_selection`의 `rank_momentum`

세션 수는 `index[last]`부터 `index[-1]`(평가일 행)까지만 센다. 평가일 뒤의 관측은 쓰지 않는다. `sessions=None`(라이브러리 직접 호출, 달력 없음)이면 행 수만 센다.

### G01 — 하위 호출 경로 (잔존 결함 확인 → 수정)

조정자 요청에 따라 `run_backtest → rank_momentum`, `select_holdings`의 시장 필터 최신성, CLI 최신·월말 경로를 끝까지 추적했다.

HEAD에서는 다음 세 곳이 `sessions`를 받지 않아, 행 수로만 최신성을 셌다.
- `run_backtest` 안의 `rank_momentum`
- `_select_weights` → `select_holdings`의 시장 필터 검사
- CLI `_month_end_selection`의 `select_holdings`

재현 스크립트는 `g01_paths.py`이고, 로그는 `g01_paths.before.log`(수정 전)와 `g01_paths.after.log`(수정 후)다. 입력은 XNYS 2025 자료다.
- 2025-09-25, 09-26, 09-29에는 어떤 티커의 행도 없다. 따라서 벤치마크·현금의 공백은 3세션이고, 보유·벤치마크 공백 검사(E02)는 멈추지 않는다.
- X는 09-23, 09-24, 09-30에도 종가가 없다. 09-22 마지막 관측 뒤 행은 3개지만 세션은 6개다.

| 경우 | 수정 전 (HEAD) | 판단 | 수정 후 |
|---|---|---|---|
| 1. 백테스트 후보: 8월까지 X는 평평해 보유하지 않았고, 9월에 급등 | 9/30 신호에서 오래된 X를 선택. 10월 보유 공백 검사가 `Held ticker(s) without a close ... X`로 중단 | 잘못된 선택이 결과로 남지는 않음(기존 공백 검사가 실행을 멈춤). 하지만 같은 월말에 `rank`는 B를 고르므로 정책이 일치하지 않음 | 9/30 신호 `B`. 중단 없음 |
| 2. 백테스트 시장 필터 X (후보는 B) | 멈추지 않음. 오래된 X 가격으로 투자 상태(risk-on)를 판단해 9/30 신호 `B` | **조용히 틀리는 실제 오류** (README "시장 필터 데이터가 끊기면 멈춥니다"와 불일치) | `StaleDataError: Market filter ticker(s) without a recent observation at 2025-09-30: X` |
| 3. CLI `rank`, `--risk-on-ticker X --end 2025-10-08` | 최신 행 10/07에서는 X가 최신이라 `_require_fresh` 통과. 월말 9/30 시장 필터 검사가 행 수로만 세어 오래된 필터로 B를 선택 | **실제 오류** | 월말 선택에서 같은 `StaleDataError`로 중단 |

비교: 같은 자료에서 `rank_momentum(..., sessions=XNYS)`은 X를 `price_fresh=False`로 판정한다. 행 수로만 세면 X는 `price_fresh=True`이고 1위다.

`g01_backtest_gap.py`(로그 `g01_backtest_gap.before.log`)는 X를 9월 내내 보유한 경우다. 이 경우는 수정 전에도 보유 공백 검사가 멈췄다. 이미 작동하는 공백 검사에 걸린 사례이므로 결함 근거로 쓰지 않았다.

### G02 — HEAD에서 이미 해결 (수정 없음)

`g02_check.py`(로그 `g02_check.log`)로 확인했다. Yahoo와 네이버는 mock했고, 캐시는 임시 폴더를 썼다.
- **재시도 중 네이버 실패:** NEW.KS만 다시 요청한다. 반환 자료와 캐시에는 A.KS만 남고, `.missing.json`에는 `["NEW.KS"]`가 유지된다. 경고는 "Naver gap fill failed … next run retries"와 "Still no prices data for NEW.KS (download not complete)" 두 개다.
- **다음 실행에서 네이버 정상:** NEW.KS를 다시 요청해 `[100, 110, 120]`으로 복구한다. `.missing.json`은 삭제되고, 보완 기록에 `NEW.KS: ["2024-01-03"]`이 합쳐진다. 그다음 호출은 다운로드 없이 캐시를 재사용한다.
- **`.retry`에 남은 CSV와 v2 sidecar:** 성공으로 오인하지 않고 위와 같은 결과가 나온다. 재시도 전에 남은 파일을 삭제하기 때문이다.
- **최신 조회(`end=None`)의 부분 실패:** 캐시 파일 0개. 경고는 "not cached, the next run retries"다.

### G03 — HEAD에서 이미 해결 (수정 없음)

`README.md:237`은 최신 조회의 부분 실패(캐시 안 함)와 고정 기간(받은 티커만 캐시, 빠진 티커만 재요청, 네이버 보완 실패 시 합치지 않음)을 구분해 설명한다. 이는 `data._cached_download`·`_retry_missing`의 동작과 위 G02 확인 결과와 일치한다. `README.md:232`의 네이버 실패 시 "캐시하지 않음"도 맞다.

이번 G01 수정은 `README.md:233`의 기존 설명("최신성·공백 판단(후보 ETF, 벤치마크·시장 필터·현금 ETF, 백테스트의 보유 종목)은 모두 행 수와 거래소 거래일 수 중 큰 쪽")에 코드를 맞춘 것이다. 그래서 문서는 고치지 않았다.

### 정상 회귀 (`normal_check.py`, 로그 `normal_check.log`)

기대값은 구현 결과가 아니라 직접 수식으로 계산했다.

| 항목 | 기대 | 실제 |
|---|---|---|
| 가중 모멘텀 (63/126/252, skip 21, 0.3/0.3/0.4, A=100×1.002ⁿ) | 0.38792207964425396 | 0.38792207964425396 (`price_fresh=True`) |
| buy-and-hold (A +10%→−10%, B 0, 각 0.5) | 0.995, A 종료 비중 0.49748743718592964 | 0.9950000000000001, 0.49748743718592964 |
| daily-rebalanced | 0.9975 | 0.9974999999999999 |
| 조합 최초 비용 100bp | [1.0, 0.99] | [1.0, 0.99] |
| 백테스트 A 전량, 비용 0, XNYS 2024-01~06 | 1.3614324600940368 | 1.3614324600940364 |

추가로 `test_backtest_without_gaps_is_the_same_with_or_without_sessions`에서, 공백이 없는 자료는 `sessions` 유무와 관계없이 곡선과 포지션이 같음을 확인했다.

### 실제 자료 (`real_cache_flips.py`, 로그 `real_cache_flips.log`)

고정 캐시 `data/prices_raw_*_2010-01-01_2026-10-07.csv` 12개를 읽기만 했다. 모든 월말과 마지막 행에서, 행 수 기준과 max(행, 세션) 기준의 최신성 판정이 갈리는 경우는 **0건**이다.
- 가격표에 행이 없는 세션은 KR·US 선정 패널 모두 0개다(788 유니버스 캐시 포함).
- 환율(KRW=X) 단독 캐시만 행 없는 XNYS 세션이 7개다. 이 캐시는 선정 최신성 경로를 거치지 않는다.

따라서 이번 수정으로 저장된 백테스트·워크포워드·조합 결과가 바뀌지 않을 것으로 판단한다. 다만 이는 위 판정 대조에 근거한 추론이며, 공식 결과는 다시 생성하지 않았다.

## 4. 변경 파일

| 파일 | 내용 | SHA-256 (종료 시) |
|---|---|---|
| `src/etf_momentum/backtest.py` | `run_backtest`가 `sessions`를 `rank_momentum`과 `_select_weights`에 전달. `_select_weights`·`select_holdings`에 `sessions=None` 인자를 추가하고 시장 필터 `observed_freshness`에 전달 | `96a49e0f863b2a11a45b7552ffff0f9daa458dfa2faa0f988f44250a75235b54` |
| `src/etf_momentum/cli.py` | `_month_end_selection`의 `select_holdings`에 `sessions=_exchange_sessions(args.benchmark)` 전달 | `25e14f080bfdf2f98e982836d51a238b74c4214601dfd48f93220abaac256b33` |
| `tests/test_g01_selection_sessions.py` (신규) | 회귀 테스트 6개 (아래) | `def9aef91fd83b9742c096d8e983bb178894a97588e3392aaa3dcea75aea99c9` |
| `docs/workflows/2026-10-17-g01-g03/implementation.md` (신규) | 이 보고서 | — |

전체 diff는 `/private/tmp/etf-orca-g01-g03-20261017/claude/source.diff`에 있다. 제품 코드는 9줄 추가, 5줄 삭제다.

`compare`와 `walkforward`는 이미 `run_backtest(..., sessions=_exchange_sessions(args.benchmark))`를 호출하므로(`cli.py:709`, `:849`) 별도 수정 없이 이번 수정이 적용된다. `select_holdings` 호출은 `backtest.py:401`과 `cli.py:412` 두 곳뿐이다.

## 5. 테스트 (실제 명령과 결과)

공통 접두어: `env MPLCONFIGDIR=/private/tmp/etf-orca-g01-g03-20261017/claude/mpl XDG_CACHE_HOME=/private/tmp/etf-orca-g01-g03-20261017/claude/cache .venv/bin/python -m pytest -q -p no:cacheprovider`

| 단계 | 대상 | 결과 | 로그 |
|---|---|---|---|
| 수정 전 (새 테스트 작성 직후) | `tests/test_g01_selection_sessions.py` | **4 failed, 2 passed** (실패 이유: 보유 공백 검사 중단, 기대한 StaleDataError 미발생 ×2, `select_holdings()`의 `sessions` 인자 없음) | `pytest_new_red.log` |
| 수정 후 | 같은 파일 | **6 passed** | `pytest_new_green.log` |
| 관련 | `test_g01_selection_sessions.py test_review_2026_10_13.py test_raw_observations.py test_selection.py test_review_2026_10_11_recheck3.py test_naver_fill.py test_minor_followups_2026_10_11.py` | **81 passed in 3.02s** | `pytest_related.log` |
| 전체 | (인자 없음) | **374 passed in 11.42s** (HEAD 368 + 신규 6) | `pytest_full.log` |

새 테스트 6개:
1. `test_backtest_does_not_pick_a_candidate_stale_in_sessions`: 경우 1의 9/30 신호가 B
2. `test_backtest_picks_the_same_candidate_when_it_is_observed`: 대조군. 관측이 있으면 X를 선택
3. `test_backtest_market_filter_stale_in_sessions_stops`: 경우 2
4. `test_select_holdings_counts_sessions_and_falls_back_to_rows_without_a_calendar`: 세션을 주면 중단, 달력이 없으면 행 수 기준으로 B 선택
5. `test_backtest_without_gaps_is_the_same_with_or_without_sessions`: 정상 자료는 결과 불변
6. `test_rank_month_end_market_filter_stale_in_sessions_stops`: 경우 3, CLI `rank` 전체 경로. Yahoo는 mock, 캐시·출력은 `tmp_path`

## 6. 제한

- 룩백·SMA·변동성 창의 **행 수 압축**은 바꾸지 않았다. 리뷰 문서가 영향으로만 언급했고 범위 밖이다. 5세션을 넘는 전체 공백은 벤치마크 검사에서 실행이 멈추므로, 압축은 최대 5행이다.
- 시장 필터가 오래됐을 때 CLI `rank`는 기존과 같이 `StaleDataError` traceback으로 멈춘다. 행 수 기준으로 오래된 경우의 동작과 같고, 오류 표시 방식은 바꾸지 않았다.
- 거래소 달력 범위 밖이나 `sessions=None`이면 행 수만 센다. 문서화된 fallback이다.
- KRX 임시 휴장일은 `cli.KRX_HOLIDAYS_MISSING_FROM_CALENDAR`에 지정한 두 날짜(2026-06-03, 2026-07-17)만 제외한다. 이번 작업에서 외부 휴장 공고나 시세를 조회하지 않았다.
- 전체 788 유니버스의 거래대금 캐시가 없어, 788 유니버스 기준 유동성 포함 선정·성과는 다시 실행하지 않았다(미검증). 실제 자료 확인은 가격 관측표의 최신성 판정 대조에 한정된다.
- 이 보고서와 테스트 파일은 미커밋 상태이며, 소스 동결 뒤 Gemini의 독립 검증을 받는다.
