# C17-01·C17-02 수정과 재검증, 리뷰 문서 사실관계 보완

- 작성일: 2026-10-17 (Asia/Seoul), 작성: Claude (이 세션의 시스템 프롬프트상 모델 Opus 5.5, 자기 보고이며 외부 확인은 하지 않음)
- 대상 리뷰: `docs/review_concept_2026-10-17_codex.md` (검토 HEAD `9315f284c7e11ae4ed953de856265aa7890df8ef`)
- 이 보고서 작성 시 HEAD: 같음. 수정은 **미커밋 작업 트리**에 있다(커밋·푸시·네트워크·공식 결과 재생성 없음).
- 새 실험 경로: `/private/tmp/etf-c17-claude/` (원 리뷰 경로 `/private/tmp/etf-concept-review-20261017/`는 읽기만 함)

## 1. 보존한 원본

| 대상 | 위치 | SHA-256 |
|---|---|---|
| 리뷰 원문(수정 전) | `/private/tmp/etf-c17-claude/backup/review_concept_2026-10-17_codex.original.md` | `23f47e0262bc5fd582ef91224f3e9ebec163c885fb79c3070b093224450b5f5d` |
| 원 리뷰 실험 파일 181개 지문 | `/private/tmp/etf-c17-claude/backup/original_experiment_sha256.txt` | (파일별 기록) |
| `reproduce.py` / `boundaries.py` / `local_check.py` | 원 경로 그대로 | `cf0dbd55…552d` / `30b94829…b43b0` / `151b1af6…3333` (리뷰 부록 기재값과 일치) |

원 스크립트는 출력 경로가 원 실험 폴더로 고정돼 있다. 그래서 경로만 바꾼 사본을 새 실험 폴더(`rerun_head/`, `rerun_after/`, `independent_rerun/`)에서 실행했다. 원 결과 파일은 덮어쓰지 않았다.

## 2. 수정 전 재현 (HEAD 9315f28, 제품 코드 변경 전)

`rerun_head/reproduce.py`·`boundaries.py`(경로만 치환)를 실행한 결과, 원 리뷰의 `results.json`과 `boundary_results.json`이 environment 항목을 제외하고 **완전히 같았다**.
- C17-01: 압축된 거래대금 표에서 A의 평균 20억, `liquid=True`로 A가 선택된다. NaN 행 대조군에서는 B가 선택된다. 실제 CLI도 압축 표에서 A, NaN 행 대조군에서 B를 선택한다.
- C17-02: `rank`가 `as_of=2025-09-30, status=cash, ticker=BIL`을 정상 저장한다. 백테스트는 `StaleDataError`로 멈춘다.
- 행이 모두 있는 대조군의 5/6/8세션 유동성: `2e9 / NaN / NaN`

따라서 두 지적은 HEAD에서 **재현됨(사실)**으로 판정했다.

## 3. 판정과 수정

### C17-01 — 거래대금 최신성 (수정)

- **원인:** `signals._average_traded_value`의 최신성은 (표에서 뒤쪽 결측 행 수) + (표의 마지막 날 이후 영업일 수)다. 표에 날짜 행이 아예 없으면 그 날은 세지 않는다. `rank_momentum`은 `sessions`를 받지만 이 함수에 넘기지 않았다.
- **실제 자료 대조 (읽기만 함, 로그 `tv_cache_index.log`):** 원 리뷰는 운영 캐시에서 발생했다고 주장하지 않았지만, 인덱스 불일치 자체는 운영 캐시에 실제로 있다.
  - 국내 거래대금 캐시: 60종은 KRX 세션 26개, 34종은 32개의 행이 없다(예: 2017-09-22, 2023-07-03~05). 같은 날 가격 캐시에는 행이 있다. 야후가 거래대금을 주지 않은 날이고, 가격만 네이버로 채웠기 때문이다.
  - 미국 32종 캐시: 빠진 세션 0개
- **수정 범위 결정 (`tv_impact.py`, 로그 `tv_impact_full.log`·`tv_impact_freshness.log`):** 실제 캐시의 모든 월말(069500.KS 거래일 기준)과 마지막 날에서 10억 기준으로 두 후보를 비교했다.
  - (a) 63행 창과 최소 관측 수까지 세션 달력으로 옮기면: 60종 캐시에서 평균값 1,638건이 바뀌고(최대 상대 변화 70.6%), 유동성 판정이 11건 뒤집힌다(예: 2023-07-31 139220.KS 13.5억→8.7억). 34종 캐시는 6건이다. 공식 연구 수치가 바뀌는 설계 변경이고 이번 요청에는 결과 재생성이 포함되지 않으므로 **채택하지 않았다**.
  - (b) 최신성만 세션으로도 세면: 변화 0건 / 0건. 이것을 채택했다.
- **수정 내용:**
  - `_average_traded_value(..., sessions=None)`: 세션이 주어지면 종목별 마지막 관측 이후 판단일까지의 세션 수도 5를 넘지 않아야 최신으로 본다. 기존 행·영업일 기준과 AND로 결합하므로, 이전보다 느슨해지는 경우는 없다.
  - `rank_momentum`이 `sessions`를 넘긴다. CLI의 최신·월말 순위와 `run_backtest`(9315f28에서 이미 `sessions`를 넘김)가 모두 이 경로를 쓴다.
  - 평균 창(표의 최근 63행)과 최소 관측 수(42)는 그대로다.
- **유지한 동작:** `sessions=None`(라이브러리 직접 호출, 달력 없음)이면 이전과 똑같이 계산한다. KR/US 휴장일은 각 거래소 세션에 없으므로 공백으로 세지 않는다(테스트로 확인).

### C17-02 — 월말 현금 대기 자산 (수정)

- **명세 구분:**
  - 기존 좁은 명세(원자료 설계 표의 "실행 전, 가격 데이터의 마지막 날 기준" 필수 검사, `_require_fresh`)는 그대로 둔다. 구현은 이 명세를 어기지 않았다는 리뷰 해석에 동의한다.
  - 새로 메운 것은 설계 공백 하나다. 현금 자리가 생긴 월말 선택에서, 그 월말 기준으로 현금 ETF가 관측됐는지 확인하는 검사가 없었다.
- **수정 내용:** `cli._month_end_selection`에서 `select_holdings` 결과에 빈 자리가 있고(시장 필터 꺼짐 또는 후보 부족) `--cash`가 ETF이면 `_require_month_end_cash`를 실행한다.
  - 이 함수는 월말까지의 관측표로 `observed_freshness(..., sessions)`를 계산한다. 오래됐으면 출력과 신호 기록 전에 `SystemExit`로 멈춘다.
- **보존한 정책:**
  - `--cash none`(미투자 현금)은 검사하지 않는다.
  - 그 월말까지 현금 ETF의 관측이 하나도 없으면(상장 전) 백테스트와 같이 무수익 현금으로 보고 통과시킨다.
  - 빈 자리가 없는 월말에는 현금 ETF를 검사하지 않는다. 이 경우 현금 ETF는 선정에 쓰이지 않기 때문이다.
- **실제 자료 (`c17_after.py`):** 153130.KS(60종 가격 캐시)와 BIL 단독 캐시 모두, 202개 월말 중 상장 후 오래된 월말이 0개다. 따라서 이 검사가 기존 운영 실행을 멈추게 했을 사례는 이 캐시에서 없었다.

### 변경 파일 (미커밋)

| 파일 | 변경 | SHA-256 |
|---|---|---|
| `src/etf_momentum/signals.py` | `_average_traded_value`에 `sessions`, `rank_momentum`에서 전달 | `82dea498e9bdca9ac2d2d76b30c55503c24ac33387b8c81b72bd998061e45344` |
| `src/etf_momentum/cli.py` | `_require_month_end_cash` 추가, `_month_end_selection`에서 호출 | `335a2afeb63675d551654608ec3c56e17e1d8233f83e677bbd499da5ab23f27e` |
| `tests/test_review_concept_2026_10_17.py` (신규) | 회귀 테스트 13개 | `c02d589a26ecda56e97dec37f86da7815c1cf74385e92541804687129c599ba0` |
| `README.md` | 유동성 최신성의 거래일 기준, 63행 창과 행 없는 날 설명, 월말 현금 검사 | `37a64ce0e71cec178d378506e83a221b3c9b89b1df3d2edf6ed0f446866c60d7` |
| `CLAUDE.md` | 거래대금 최신성 기준과 국내 거래대금 캐시의 행 누락 | `d8f86cdd049cecbff5309ef31193d71c497ba35ec97071d09493536584dbd55b` |
| `docs/superpowers/specs/2026-10-09-raw-observations-and-exchange-calendar-design.md` | 필수 현금 검사 행에 2026-10-17 추가 사항 주석 | `70cab01998003ff678829f8c6110d094027af40dbb48484127ac5a50c1c372a0` |
| `docs/review_concept_2026-10-17_codex.md` | `〔보완〕` 주석만 추가. 본문 판정·수치·부록은 그대로 | — |
| 이 보고서 (신규) | | — |

전체 diff는 `/private/tmp/etf-c17-claude/source.diff`에 있다. 제품 코드는 31줄 추가, 2줄 삭제다.

## 4. 수정 후 재검증

| 시나리오 | 수정 전 (HEAD) | 수정 후 |
|---|---|---|
| C17-01 압축 표, 직접 함수 | A 평균 2e9, liquid, 선택 A | A 평균 NaN, liquid=False, 선택 B |
| C17-01 NaN 행 대조군 | 선택 B | 선택 B (불변) |
| C17-01 실제 CLI `rank` (압축 / NaN 행) | A / B | B / B |
| C17-02 리뷰 사례 `rank` | cash BIL 정상 저장 | `Stale data for cash BIL at the month end 2025-09-30 (last observation 2025-09-22)…`로 중단. `latest_selection.csv` 미작성 |
| C17-02 독립 리뷰어 사례 (평일 달력, 후보 부족, BIL 09-16~10-01 결측) | cash BIL 정상 저장 | 같은 메시지로 중단 (last observation 2025-09-15) |
| C17-02 같은 입력 백테스트 | `StaleDataError` (10-01~10-07 BIL 공백) | 같음 (불변) |
| 실제 캐시 월말·마지막 날 평균 거래대금, 세션 유무 비교 (KR 60·34종, US 32종) | — | 변화 0건 |

로그: `c17_after.log`, `after/after_results.json`, `independent_rerun/after.log`.

원 `reproduce.py` 사본(`rerun_after/`)은 수정 후 `assert compressed['selected']==['A'] …`에서 실패한다. 이 assert는 수정 전 동작을 고정한 것이므로 의도된 결과다. 원 스크립트와 리뷰 부록의 기대값은 고치지 않았다.

### 테스트

공통: `env MPLCONFIGDIR=/private/tmp/etf-c17-claude/mpl XDG_CACHE_HOME=/private/tmp/etf-c17-claude/cache .venv/bin/python -m pytest -q -p no:cacheprovider`

| 단계 | 대상 | 결과 | 로그 |
|---|---|---|---|
| 수정 전 | `tests/test_review_concept_2026_10_17.py` | **8 failed, 5 passed** | `pytest_red.log` |
| 수정 후 | 같은 파일 | **13 passed in 2.19s** | `pytest_green.log` |
| 전체 | 인자 없음 | **387 passed in 11.61s** (기존 374 + 신규 13) | `pytest_full.log` |

- 수정 전 실패 8개의 원인:
  - 압축 표 유동성(liquid가 True로 나옴), 5/6 경계
  - `_average_traded_value`에 `sessions` 인자가 없음(KRX 휴장 테스트, 창 테스트)
  - 백테스트·CLI가 A를 선택
  - C17-02 두 사례에서 기대한 `SystemExit`가 발생하지 않음
- 수정 전에도 통과한 5개는 보존할 정책의 대조군이다: 달력 없는 fallback, 월말 현금 정상, 현금 자리 없음, 미투자 현금, 상장 전 현금.
- 경계 테스트 구성:
  - 5세션 통과·6세션 거부 (XNYS, 표에 행 없음)
  - KRX 2025-10-03, 10-06~09 휴장을 넘는 4·5세션 통과와 6세션 거부. 4세션 경우는 평일로 세면 9일이다.
  - 가격 표와 거래대금 표의 인덱스 불일치 (가격 행은 있고 거래대금 행은 없음)
  - 63행 창이 바뀌지 않았음을 확인하는 수치 검산 ((62×3e9+2e9)/63)
  - 백테스트 9/30 신호, CLI 전체 경로
  - CLI 테스트는 `cli.download_prices`·`download_traded_value`를 교체한다. 그래서 `data.py`의 다운로드·정리 단계는 거치지 않는다(아래 5절의 보완 2와 같은 범위).

## 5. 리뷰 문서 사실관계 대조

원 리뷰의 줄 번호는 모두 HEAD 9315f28과 일치했다(`signals.py:107/119/235–250`, `cli.py:270–272/392–412/430–434/1245–1263`, `backtest.py:338–355/529–545`). 수치는 원 실험 JSON과 현재 파일로 다시 확인했다.
- 모멘텀 0.3883952069331815, 성장 1.1 / 1.1025, same_close 1.1 / next_close 1.0, 5/6/8 신선도
- proxy [1000, 4000, 200]
- 60종 선택 4종, 보유 구간 23개, 후보 58개, 1.3268633006764898
- 저장 곡선 CAGR·MDD·Sharpe
- 입력 SHA-256 3개
- config_hash `559a153fc6ea` / `506cf0bb4576`, 후보 786 / 726
- 추적 파일 125개, 초기 python3 3.8.2
- 메타의 `csv_sha256` 부재, `next_improvements.md`의 next_close 미구현 문구

모두 문서와 같았다. 리뷰 원문에는 다음만 `〔보완 2026-10-17〕` 주석으로 추가했다.

1. **C17-01 재현 조건의 "09-30에는 B만 관측된다":** 실제 입력에서 09-30에는 A만 결측이고 B·SPY·BIL은 관측된다(`reproduce.py`의 `tv.loc[tv.index>last,'A']=np.nan`). 결론에는 영향이 없다.
2. **"공급자 반환만 mock했고":** 두 CLI 재현은 `cli.download_prices`/`cli.download_traded_value` 자체를 교체했다. `data.py`의 캐시·정리·네이버 보완 단계는 실행되지 않았다. CLI 이후 선정 경로는 실제 코드다.
3. **C17-01 영향의 "실제 운영 캐시에서 … 주장은 아니다":** 행 누락 자체는 실제 국내 거래대금 캐시에 있다(26/32세션). 다만 월말 판정을 바꾼 사례는 없었다. "63행이 63거래일보다 길어질 수 있다"는 실제 캐시에서 일어난다.
4. **독립 리뷰어 59개 테스트 보고:** 원 경로 `independent/pytest/`에는 테스트별 임시 폴더만 있고 실행 개수·결과 로그는 없다. 보존 자료로 개수와 결과를 확인할 수 없다.
5. **독립 리뷰어 C17-02 입력:** 평일 달력(`pd.bdate_range`), 시장 필터 없음, 후보 부족 사례다. 원 리뷰의 "다른 입력" 설명과 모순되지 않아 보충만 했다.

원 리뷰의 판정(C17-01·C17-02 P2, 설계 한계 1~7)은 바꾸지 않았다. 2절 표의 "C17-01 예외 존재", "C17-02 예외 존재"는 수정 전 상태 기록으로 남겼고, 수정 후 상태는 이 보고서에만 적었다.

## 6. 남은 제한

- 전체 788종 유니버스의 거래대금 캐시가 로컬에 없다. 그래서 기본 전략(788종, 유동성 10억)의 실제 유동성 판정 영향은 **미검증**이다. 실제 자료 확인은 60·34종 KR, 32종 US 캐시에 한정된다.
- 평균 창은 여전히 거래대금 표의 최근 63행이다. 국내 캐시처럼 행이 빠진 기간에는 창이 63거래일보다 길어진다. 세션 달력으로 옮기면 실제 판정 11건(60종)이 바뀌므로, 옮길지는 연구 결과 재생성과 함께 사용자가 정할 설계 결정으로 남겼다(README에 현재 동작을 명시).
- C17-02로 멈출 때 `latest_ranking.csv`(최신 순위)와 `rank_run_config.json`은 이미 저장된 뒤다. 월말 선택 파일과 `signals.csv` 기록은 만들어지지 않는다. 최신 순위 자체는 최신 자료로 검증된 결과라 그대로 두었다.
- `weekly`는 월말 선택을 하지 않으므로 C17-02와 무관하다. 백테스트의 현금 공백은 기존 보유 공백 검사가 계속 담당한다.
- 거래대금 경고(`cli._traded_value`)는 여전히 표 전체의 마지막 날 기준이다. 종목별 판정이 수정됐으므로 경고 문구는 바꾸지 않았다.
- 외부 시세·휴장 공고·네트워크 조회는 하지 않았다. `data/`·`results/`·신호·주간 이력은 수정하지 않았다(`find data results -newer <백업>` 결과 0건).
- 수정은 미커밋 상태다. 공식 연구 결과(백테스트·워크포워드)는 다시 만들지 않았다. 위 판정 대조로 보면 60종 기준 저장 결과는 바뀌지 않을 것으로 예상하지만, 재생성으로 확인한 것은 아니다.
