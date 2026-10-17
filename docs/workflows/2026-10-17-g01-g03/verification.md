# G01–G03 독립 검증 보고서 (Gemini Independent Verification)

- **검증 주체 / 모델**: Antigravity 세션 (`c4407755-4cb1-44d2-a890-7c4dda70d690`), **Gemini 4 Argon (Medium)**
- **Orca 컨텍스트**: Worker `term_567c60e9-acee-4353-8693-5ad4003f6187`, Coordinator `term_b7a2c688-ab8b-4c3b-90d0-fa9c735ff49b`, Run `run_2d8c6f88a708`, 초기 검증 `task_b995ed9f7a8d` (`ctx_85a9dca99d98`), 보고서 보정 `task_0d2be70977fe` (`ctx_e44ee3f1fde1`), 최종 사실관계 교정 `task_b3671211161d` (`ctx_f81df92406ff`)
- **환경 / 격리 경로**: macOS `Darwin 25.4.0 arm64`, `.venv/bin/python` (`Python 3.14.3`, `pandas 3.0.3`, `numpy 2.4.6`, `exchange-calendars 4.13.2`, `pytest 9.0.3`), 임시 증거 경로 `/private/tmp/etf-orca-g01-g03-20261017/gemini/`
- **판정**: **제한 범위 내 PASS (검증한 G01–G03 및 정상 회귀 항목에서 제품 결함 0건)**

## 1. 독립성 경계 및 보정 경위

1. **초기 독립 검증**: Claude가 작성한 구현 보고서(`implementation.md`), 패치 요약(`source.diff`), 세션 로그를 읽지 않고, `task.md`, `design.md`, `source_snapshot.json`, 작업 트리의 원시 `git diff` 및 제품 코드·테스트, 기존 리뷰 문서(`docs/review_2026-10-13_full_codex.md`)만 확인한 뒤 `/private/tmp/etf-orca-g01-g03-20261017/gemini/verify_all.py`를 작성·실행해 `verification_results.json`을 생성했다.
2. **보고서 보정 사유 (하네스/문서 오류와 제품 결함 구분)**: 초기 실행 중 `verify_all.py`의 소형 백테스트 행 수(워밍업 4행 부족) 및 인자명(`traded_values` 오타 등) 하네스 오류를 수정해 12개 `matches_expected` 묶음이 모두 `True`임을 확인했으나, 초안 작성 과정에서 일부 파일 해시·심볼 위치·예외 문구·함수명·유니버스 구성을 실제 소스와 다르게 적은 문서 오류가 있었다. 본 수정본은 제품 코드나 실험 결과값 변경 없이 실제 소스 및 저장된 증거(`source_snapshot.json`, `verify_all.py`, `verification_results.json`, `pytest_*.log`)와 정확히 일치하도록 교정했다.

## 2. 동결 소스 스냅샷 검증 (`source_snapshot.json` 대조)

- **집계 기준**: `HEAD` = `e596368be6d76bd604361b3a1d84d8af9a978399`, 대상 파일 `68`개, `git_diff_sha256` = `a37c657463af9a7cbd93f2f9b43c69477dd6ed3f3c25e2a824d61ece1f0111e8`, `manifest_sha256` = `a9c2e85fda59d1d572421f93b37b738652c560b38fdb3f208c06800f8070c049` (`sha256(json.dumps(files, sort_keys=True).encode())`).
- **전후 확인**: `snapshot_before.json`과 `snapshot_after.json` 모두 68개 개별 파일 해시 불일치 `0`건(`mismatches: {}`), `HEAD` 및 `git_diff_sha256` 동일함을 확인했다. (`snapshot_before.json`의 `manifest_sha256` `0b6e7712...`는 `separators=(",", ":")` 직렬화 차이로 인한 것이며, 기본 `json.dumps(files, sort_keys=True)` 직렬화를 사용한 `snapshot_after.json`에서 기준 `manifest_sha256` `a9c2e85f...`와 정확히 일치함을 재확인했다.)

| 파일 경로 (`source_snapshot.json` 및 실제 바이트 기준) | SHA-256 | 전후 일치 |
| :--- | :--- | :--- |
| `src/etf_momentum/backtest.py` | `96a49e0f863b2a11a45b7552ffff0f9daa458dfa2faa0f988f44250a75235b54` | 일치 |
| `src/etf_momentum/cli.py` | `25e14f080bfdf2f98e982836d51a238b74c4214601dfd48f93220abaac256b33` | 일치 |
| `src/etf_momentum/signals.py` | `dd4dd2171874b26207c0a36501db8a3d15807ca726b5cb12189489a7fa0f05cb` | 일치 |
| `src/etf_momentum/data.py` | `31b900f87ab0ddacc15fc7792ac829059caf7eb00f7a4ac5980d473c6d2d9b91` | 일치 |
| `src/etf_momentum/naver.py` | `4af158151d3144384cd98e1d9ac6a26c6d4035e830bd003da433831735929484` | 일치 |
| `tests/test_g01_selection_sessions.py` | `def9aef91fd83b9742c096d8e983bb178894a97588e3392aaa3dcea75aea99c9` | 일치 |
| `README.md` | `489a1bbfb907bcab61a0ece21dc1be12b77ed5887681d7849babdfcfd5300470` | 일치 |
| `CLAUDE.md` | `debed8d6cc25b324884a4c3d7fa23465e7465fc15f642776e8d9a51744e1c62d` | 일치 |

## 3. 요구사항별 코드 대조 및 독립 실험 결과 (`verify_all.py` → `verification_results.json`)

### 3.1 G01: 거래소 세션 기준 최신성 및 호출 경로 일관성
- **코드 구조**:
  - `signals.py`의 `observed_freshness`: `age = len(index) - 1 - last`와 거래소 세션 거리 `ordered.searchsorted(index[-1], side="right") - ordered.searchsorted(index[last], side="right")` 중 최댓값(`max`)이 `PRICE_MAX_STALE_DAYS`(`5`) 이하인지 판정(`sessions=None`이면 행 수만 사용).
  - `backtest.py`: `run_backtest`가 리밸런싱 시점마다 `rank_momentum(..., sessions=sessions)`과 `_select_weights(..., sessions=sessions)`를 각각 호출하고, `_select_weights` → `select_holdings`를 거쳐 시장 필터 자산의 `observed_freshness(..., sessions)`를 검사한다(`select_holdings`는 계산된 `ranking`을 받으며 `rank_momentum`을 직접 호출하지 않음). 시장 필터가 오래되면 `StaleDataError(f"Market filter ticker(s) without a recent observation at {as_of.date()}: ...")`를 발생시킨다.
  - `cli.py`: `_require_fresh`(`cli.py:1500`)는 필수 자산(`benchmark`, `cash`, 시장 필터)이 오래되면 `SystemExit`(`"Stale data for required ticker(s): ..."`)을 발생시킨다. `_month_end_selection`은 `rank_momentum`과 `select_holdings` 모두에 `sessions=_exchange_sessions(args.benchmark)`를 전달한다. `run_compare_command`·`run_walkforward_command`는 코드 읽기 기준 `run_backtest(..., sessions=_exchange_sessions(args.benchmark))` 호출을 확인했다.
  - **휴장 보정 심볼**: `KRX_HOLIDAYS_MISSING_FROM_CALENDAR = pd.DatetimeIndex(["2026-06-03", "2026-07-17"])`는 `cli.py:1516`에만 선언되어 `cli._exchange_sessions`에서 XKRX 세션 차집합에 사용된다(외부 휴장 공고는 별도 조회하지 않음).
- **독립 실험 수치 (`verification_results.json` → `g01`)**:
  - **5/6/8 세션 경계 및 당일 관측** (`XNYS`, 평가일 `2025-09-30`, 중간 세션 행을 제거해 `indep_row_age = 1` 고정):
    - `gap_5_sessions` (마지막 관측 `2025-09-23`): `indep_session_age = 5` → `actual_fresh_with_sessions = True`, `rank_eligible = True`, `selected_tickers = ["A"]`.
    - `gap_6_sessions` (`2025-09-22`): `indep_session_age = 6` → `actual_fresh_with_sessions = False`, `rank_eligible = False`, `selected_tickers = ["B"]`, 반면 `sessions=None` 폴백은 `True`.
    - `gap_8_sessions` (`2025-09-18`): `indep_session_age = 8` → `actual_fresh_with_sessions = False`, `selected_tickers = ["B"]`.
    - `normal_current_observation_on_as_of` (`2025-09-30`): `fresh_actual = True`.
  - **필수 자산 및 행>세션 역전**: `cli._require_fresh`는 5세션 공백 시 통과(`actual_pass = True`), 6세션 공백 시 `SystemExit` 포착(`actual_pass = False`). 비세션 주말 행을 섞어 `row_age = 8, session_age = 5`인 경우 `actual_fresh = False`.
  - **한국 추석 연휴 vs 미국 세션 및 휴장 보정일**: `2025-10-02` 관측 후 `2025-10-10` 평가 시 XKRX 경과 세션은 `1`(`kr_fresh_actual = True`), XNYS 경과 세션은 `6`(`us_fresh_actual = False`). `2026-06-03`, `2026-07-17` 두 날짜가 `cli._exchange_sessions("069500.KS")`에서 제외됨(`kr_special_holidays_excluded = True`).
  - **명령 간 전파**: `2025-09-30`에 후보 `X`가 6세션(3행) 공백일 때 `run_backtest`(`sessions=xnys`)는 `"B"`, `sessions=None` 폴백은 `"X"`를 선택. CLI `rank`(`--end 2025-10-08`)의 `latest_selection.csv`는 `["B"]`를 선택하고 `X`를 `"최근 관측 없음 (마지막 2025-09-22)"`로 제외. CLI `weekly`(`--end 2025-10-01`)의 `latest_ranking.csv`는 `X`를 `price_fresh = False, eligible = False`로 기록.

### 3.2 G02: 고정 종료일 부분 캐시 재시도 실패 보존, `.retry` 잔여물 정리 및 복구
- **코드 구조**: `data.py`의 `_cached_download`는 `fill_gaps` 예외 시 경고 후 미캐시 반환한다. 최신 조회(`end is None`)는 누락 티커가 있으면 `"not cached, the next run retries"` 경고 후 캐시 파일을 쓰지 않고 부분 DataFrame을 반환한다(현재 코드에 `PartialPriceDataError`나 `--allow-missing`은 없음). `_retry_missing`은 실행 전 `.retry` 내 CSV·`.naver_fill.json`·`.missing.json`을 삭제하고, `not retry_cache.exists() or _needs_naver_fill(...)`이면 병합하지 않는다.
- **독립 실험 수치 (`verification_results.json` → `g02.pinned_partial_retry_and_latest`)**:
  - `step1_initial_partial_cache_ok = True`: `["069500.KS", "A.KS", "LATE1.KS", "LATE2.KS"]` 고정 구간(`2024-02-01`~`2024-02-09`)에서 앞 2종만 캐시되고 `.missing.json`에 `["LATE1.KS", "LATE2.KS"]` 기록.
  - `step2_naver_fail_and_leftover_rejected_ok = True`: `.retry`에 `999.0` 오염 파일과 sidecar를 심은 뒤 재시도에서 `naver_closes`가 `OSError`를 내도록 모킹하면, 잔여물이 삭제되고 본 캐시(`["069500.KS", "A.KS"]`)와 `.missing.json`(`["LATE1.KS", "LATE2.KS"]`)이 그대로 보존됨.
  - `step3_recovery_and_partial_merge_ok = True`: 다음 실행에서 `LATE1.KS`만 Naver 보강 성공 시 조정배율 `0.5`가 적용된 기대 종가 `[500.0, 520.0, 540.0, 550.0]`과 실제값 `[500.0, 520.0, 540.0, 550.0]`이 일치하며, `.naver_fill.json`의 `filled["LATE1.KS"] == ["2024-02-06", "2024-02-07"]`, `.missing.json`은 `["LATE2.KS"]`로 갱신됨.
  - `step4_latest_partial_not_cached_ok = True`: `end=None` 부분 다운로드 시 반환 열은 `["069500.KS", "A.KS"]`이고 생성된 캐시 파일 수는 `0`개.

### 3.3 G03: 문서(`README.md`, `CLAUDE.md`) 정합성
- `README.md` 및 `CLAUDE.md`를 읽고 대조한 결과, 최신 조회(`end=None`) 부분 실패 시 미캐시 반환, 고정 종료일(`--end`) 부분 캐시 및 `.missing.json` 누락분 재시도, 재시도 중 네이버 보강 실패 시 미병합·재시도 유지, 최신성·공백 판정의 `max(행 수, 거래소 세션 수)` 5세션 기준이 현재 코드와 일치한다.

### 3.4 정상 회귀 산술 및 로컬 캐시 확인 (`verification_results.json` → `normal_and_local`)
1. **모멘텀·추세·위험조정 점수 수식 (`momentum_formula`)**:
   - 설정: `pd.bdate_range("2024-01-01", periods=300)`, `A = 100 * 1.0018^n`, `MomentumConfig(lookbacks=(63, 126, 252), weights=(0.3, 0.3, 0.4), skip_days=21, sma_window=200, volatility_window=63, volatility_floor=0.05)`.
   - 결과: 가중 모멘텀 기대 `0.3416173147485835` / 실제 `0.3416173147485835` (차이 `0.0`), 추세강도 기대·실제 `0.18952568194666108`, 위험조정 점수 기대·실제 `6.83234629497167`.
2. **보유 회계 및 거래비용 (`holding_and_cost_accounting`)**:
   - 설정: 8행(`2023-09-29`~`2024-03-29`, `lookbacks=(1,2,3), skip_days=0, sma_window=3, volatility_window=3`), `2024-01-31`(`A=0.5, B=0.5` 진입, 초기 비용 `10 bps = 0.001`) 및 `2024-02-29`(`A=0.5, BIL=0.5` 리밸런싱, 비용 `0.001`).
   - 결과: `buy_and_hold` 기대·실제 `[1.0, 1.049, 1.053995238095238, 1.0529412428571427]`, `daily_rebalanced` 기대·실제 `[1.0, 1.049, 1.049, 1.0479509999999999]`, `total_transaction_cost` 기대·실제 `0.002`.
3. **로컬 60종 캐시 스칼라 산술 검산 (`local_data_limits`)**:
   - `config/universe_kr_pension.csv`는 총 `66`행이며, 활성(`enabled=1`) 멤버는 벤치마크 `069500.KS`와 현금 `153130.KS`를 포함해 `60`종이다(`account_candidates(members, "pension", "069500.KS", "153130.KS")` 기준 정상 후보는 `58`종이며, `357870.KS`는 해당 CSV에 존재하지 않는다). 로컬 `prices_raw_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv`와 `traded_value_60_tickers_...csv`는 이 `60`개 열을 가진다.
   - `verify_all.py` 하네스는 CSV에 없는 `357870.KS`만 제외한 채 표준 `account_candidates`(계좌·벤치마크·현금) 필터를 적용하지 않았고, 실제 `cli._download`의 `raw.ffill()`(및 `_observations` 마스크) 대신 `raw_60.ffill(limit=5)`를 사용했다. 따라서 이 검산은 표준 전략 선정 검증이 아니라 로컬 캐시에서 `2026-09-30` 기준 `139260.KS`의 가중 모멘텀 스칼라 수식(`0.3*m63 + 0.3*m126 + 0.4*m252` = 기대·실제 `1.3268633006764898`, 차이 `0.0`)만 확인한 캐시 산술 검산이다.

## 4. 실행 명령 및 저장된 증거 파일

- **독립 검증 스크립트**:
  `env PYTHONPATH=src MPLCONFIGDIR=/private/tmp/etf-orca-g01-g03-20261017/gemini/mpl XDG_CACHE_HOME=/private/tmp/etf-orca-g01-g03-20261017/gemini/cache .venv/bin/python /private/tmp/etf-orca-g01-g03-20261017/gemini/verify_all.py` → `/private/tmp/etf-orca-g01-g03-20261017/gemini/verification_results.json`
- **관련 테스트 로그**:
  `env MPLCONFIGDIR=/private/tmp/etf-orca-g01-g03-20261017/gemini/mpl XDG_CACHE_HOME=/private/tmp/etf-orca-g01-g03-20261017/gemini/cache .venv/bin/python -m pytest tests/test_review_2026_10_13.py tests/test_g01_selection_sessions.py tests/test_naver_fill.py tests/test_raw_observations.py -q` → **55 passed in 2.38s** (`pytest_related.log`)
- **전체 테스트 로그**:
  `env MPLCONFIGDIR=/private/tmp/etf-orca-g01-g03-20261017/gemini/mpl XDG_CACHE_HOME=/private/tmp/etf-orca-g01-g03-20261017/gemini/cache .venv/bin/python -m pytest -q` → **374 passed in 12.67s** (`pytest_full.log`)

## 5. 검증 한계 및 미실행 범위 (Coverage Limits)

1. **788종 전체 유니버스 거래대금 캐시 부재**: `config/universe_kr_all.csv`(788종)의 가격 캐시(`787`열 + `.missing.json` 내 `["0246X0.KS"]`)는 있으나 `traded_value_788_*` 캐시가 없어(60종 거래대금 캐시와 59종만 겹치고 `729`종 누락), 네트워크 없이 788종 유동성 필터 포함 전체 선정·백테스트는 실행하지 못했다.
2. **행 기반 룩백·이동평균·변동성 창 압축 미변경**: 룩백·SMA·변동성 창이 세션 수가 아닌 행 인덱스로 계산되어 공백 시 창이 압축되는 동작은 변경되지 않았다. 여러 번의 짧은 공백이나 워밍업 구간의 공백이 누적되면 생략된 총 행 수는 5행을 넘을 수 있으며, 본 작업은 과거 세션을 재구성하지 않는다.
3. **외부 시세·공고 미대조 및 일부 경로 정적 확인**: 네트워크 미사용 제약에 따라 외부 실제 시세나 KRX 휴장 공고를 조회하지 않았으며, `compare`·`walkforward`의 세션 전달은 코드 읽기 및 기존 스위트로만 확인했다.

## 6. 독립 판정 이후 `implementation.md` 사후 교차 대조 (Post-Assessment Cross-Check)

독립 검증과 `verification_results.json` 기록을 마친 뒤 보정 단계에서 `docs/workflows/2026-10-17-g01-g03/implementation.md`를 열람해 교차 대조했다. Claude 보고서의 수정 파일 3개(`backtest.py`, `cli.py`, `tests/test_g01_selection_sessions.py`)와 SHA-256 해시, G01의 3개 하위 경로(`run_backtest → rank_momentum`, `select_holdings` 시장 필터, `_month_end_selection → select_holdings`) 수정 범위, G02·G03 기해결 판단 및 한계 사항이 본 독립 검증 결과와 부합함을 확인했다.
