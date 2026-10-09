# 원자료 관측 보존과 거래소 달력 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 가격 캐시에 실제 관측 여부를 남겨 후보·시장 필터·현금 ETF의 최신성을 관측일로 판단하고, 직전 월말을 거래소 달력의 마지막 거래일로 정한다.

**Architecture:** `data.py`가 채우지 않은 종가를 `prices_raw_*` 캐시에 저장하고 `download_prices(raw=...)`로 원자료와 채운 가격을 모두 제공한다. 관측표(`observed`, 날짜×티커 bool)를 `rank_momentum`·`select_holdings`·`run_backtest`가 선택 인자로 받는다(없으면 기존 규칙). CLI가 관측표를 만들고, 실행 전 최신성 검사와 거래소 달력(`exchange_calendars`) 기반 월말 판단을 한다.

**Tech Stack:** Python 3.11+, pandas, yfinance, exchange_calendars, argparse, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-10-09-raw-observations-and-exchange-calendar-design.md`

## Global Constraints

- 원자료 캐시 접두어: `prices_raw_`(상수 `PRICE_CACHE_PREFIX = "prices_raw"`). 기존 `prices_*` 캐시는 읽지도 지우지도 않는다.
- 최신성: 판단일 기준 마지막 관측 이후 지난 거래일 행 수가 `PRICE_MAX_STALE_DAYS`(5)를 넘으면 오래된 데이터.
- 거래소: 벤치마크가 `.KS`/`.KQ`로 끝나면 `XKRX`, 아니면 `XNYS`.
- 관측표를 넘기지 않는 호출은 기존 "가격이 5거래일 넘게 그대로" 규칙을 쓴다(하위 호환).
- 사용자 출력은 한국어, 커밋 메시지는 영어 + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- 테스트는 네트워크 없이 돈다(`cli.download_prices`, `data.yf.download` monkeypatch).
- CLAUDE.md: 코드를 커밋한 뒤 결과를 만들고, 결과를 만드는 동안 추적 파일을 고치지 않는다.

## Review Focus

1. **관측표에 없는 후보 티커**(일부 다운로드 실패로 열이 없음): 오류 없이 오래된 데이터로 보고 후보에서 제외해야 한다 → Task 2 테스트.
2. **벤치마크에 휴장일 행이 섞인 경우**(야후가 12/31에 행을 줌): 거래소 달력의 마지막 거래일(12/30)이 데이터에 있으면 그날을 월말로 써야 한다 → Task 3 테스트.
3. **거래소 달력이 모르는 기간**(예: 달력 범위 밖 미래 달): 기존 규칙으로 판단하고 그 사실을 출력해야 한다 → Task 4 테스트.
4. **백테스트 초반에 시장 필터 티커가 아직 관측된 적이 없는 경우**: 투자 상태로 넘어가지 않고 무엇이 문제인지 적힌 오류로 멈춰야 한다 → Task 3 테스트.
5. **프록시로 채운 상장 전 기간**: 그 기간의 후보가 "관측 없음"으로 제외되면 안 된다 → Task 4 테스트.

---

## File Structure

| 파일 | 변경 |
|---|---|
| `src/etf_momentum/data.py` | 원자료 캐시, `download_prices(raw=...)`, `PRICE_CACHE_PREFIX` |
| `src/etf_momentum/signals.py` | `observed_freshness`, `rank_momentum(observed=...)`, `last_observed` 컬럼 |
| `src/etf_momentum/backtest.py` | `select_holdings(observed=...)`, `run_backtest(observed=...)`, `previous_month_end(sessions=...)` |
| `src/etf_momentum/cli.py` | 관측표 생성, 실행 전 최신성 검사, 거래소 달력, 포트폴리오 원자료 슬리브 |
| `pyproject.toml`, `uv.lock` | `exchange_calendars` 의존성 |
| `tests/test_raw_observations.py` | 새 테스트 |
| `tests/test_fixes.py`, `tests/test_review_concept_2026_10_07.py`, `tests/test_selection.py`, `tests/test_strategy_cli.py`, `tests/test_remaining_features.py`, `tests/test_review_2026_10_08.py` | 캐시 접두어·`previous_month_end` 대역 함수 시그니처 맞춤 |
| `README.md`, `CLAUDE.md`, `docs/research_summary_2026-10-06.md` | 문서, 재계산 수치 |

---

### Task 1: 원자료 캐시와 `download_prices(raw=...)`

**Files:**
- Modify: `src/etf_momentum/data.py`
- Modify: `tests/test_fixes.py` (lines 68, 80), `tests/test_review_concept_2026_10_07.py` (line 187)
- Test: `tests/test_raw_observations.py`

**Interfaces:**
- Produces: `PRICE_CACHE_PREFIX: str = "prices_raw"`; `download_prices(tickers, start=None, end=None, cache_dir="data", refresh=False, max_age_hours=..., raw: bool = False) -> pd.DataFrame`

- [ ] **Step 1: Write the failing tests**

`tests/test_raw_observations.py`:

```python
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import etf_momentum.data as data


def raw_download(close: list[float]):
    index = pd.bdate_range("2021-01-04", periods=len(close))
    frame = pd.DataFrame({"Close": close}, index=index)
    return lambda **kwargs: frame


# Task 1: the cache keeps raw closes; callers choose filled (default) or raw
def test_raw_gap_survives_in_cache_and_raw_mode(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(data.yf, "download", raw_download([100.0, np.nan, 110.0, 111.0]))
    filled = data.download_prices(["X"], start="2021-01-01", end="2021-01-09", cache_dir=tmp_path)
    raw = data.download_prices(["X"], start="2021-01-01", end="2021-01-09", cache_dir=tmp_path, raw=True)
    assert filled["X"].tolist() == [100.0, 100.0, 110.0, 111.0]
    assert np.isnan(raw["X"].iloc[1]) and raw["X"].iloc[2] == 110.0
    cache = data._cache_path(["X"], "2021-01-01", "2021-01-09", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    assert cache.exists() and pd.read_csv(cache, index_col=0)["X"].isna().sum() == 1


def test_old_filled_price_cache_is_not_reused(tmp_path, monkeypatch) -> None:
    old = data._cache_path(["X"], "2021-01-01", "2021-01-09", tmp_path, prefix="prices")
    pd.DataFrame({"X": [1.0]}, index=pd.to_datetime(["2021-01-04"])).to_csv(old)
    monkeypatch.setattr(data.yf, "download", raw_download([100.0, 101.0]))
    prices = data.download_prices(["X"], start="2021-01-01", end="2021-01-09", cache_dir=tmp_path)
    assert prices["X"].tolist() == [100.0, 101.0]
    assert old.exists()  # left alone
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --offline pytest tests/test_raw_observations.py -q`
Expected: FAIL — `TypeError: download_prices() got an unexpected keyword argument 'raw'` and `AttributeError: ... 'PRICE_CACHE_PREFIX'`.

- [ ] **Step 3: Implement**

In `src/etf_momentum/data.py`:

1. Below `STALE_PRICE_DAYS = 4` add:

```python
# Price caches hold raw closes (no forward fill), so days without an observation stay visible.
PRICE_CACHE_PREFIX = "prices_raw"
```

2. Replace `download_prices`:

```python
def download_prices(
    tickers: Iterable[str],
    start: str | None = None,
    end: str | None = None,
    cache_dir: str | Path = "data",
    refresh: bool = False,
    max_age_hours: float = LATEST_CACHE_MAX_AGE_HOURS,
    raw: bool = False,
) -> pd.DataFrame:
    """Download adjusted close prices and cache them by ticker set.

    The cache keeps raw closes. By default the result is forward-filled for calculations; with
    ``raw=True`` days without an observation stay NaN (same cache, no extra download).
    A cache with an explicit ``end`` never changes, so it is reused as is. A ``latest`` cache is
    re-downloaded once it is older than ``max_age_hours``; if that download fails, the stale cache
    is used with a warning.
    """
    frame = _cached_download(tickers, start, end, cache_dir, refresh, max_age_hours, kind="prices")
    return frame if raw else _fill_prices(frame)
```

3. In `_cached_download`, replace

```python
    clean = _clean_prices if kind == "prices" else _clean_traded_value

    cache_path = _cache_path(ticker_list, start, end, Path(cache_dir), prefix=kind)
```

with

```python
    clean = _clean_raw_prices if kind == "prices" else _clean_traded_value
    prefix = PRICE_CACHE_PREFIX if kind == "prices" else kind

    cache_path = _cache_path(ticker_list, start, end, Path(cache_dir), prefix=prefix)
```

4. Replace `_clean_prices` with:

```python
def _clean_raw_prices(prices: pd.DataFrame) -> pd.DataFrame:
    """Sorted, without all-empty tickers or days; gaps stay NaN."""
    return prices.sort_index().dropna(axis=1, how="all").dropna(axis=0, how="all")


def _fill_prices(prices: pd.DataFrame) -> pd.DataFrame:
    return prices.ffill()


def _clean_prices(prices: pd.DataFrame) -> pd.DataFrame:
    return _fill_prices(_clean_raw_prices(prices))
```

(`_clean_prices` stays for existing callers and tests.)

5. Update the three cache tests that write a price cache by hand so they use the new prefix:
   - `tests/test_fixes.py` lines 68 and 80: `data._cache_path(["AAA"], "2020-01-01", None, tmp_path)` → `data._cache_path(["AAA"], "2020-01-01", None, tmp_path, prefix=data.PRICE_CACHE_PREFIX)`
   - `tests/test_review_concept_2026_10_07.py` line 187: add `, prefix=data.PRICE_CACHE_PREFIX` to the `_cache_path` call.

- [ ] **Step 4: Run tests**

Run: `uv run --offline pytest tests/test_raw_observations.py tests/test_fixes.py tests/test_review_concept_2026_10_07.py -q`
Expected: all pass

Run: `uv run --offline pytest -q`
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add src/etf_momentum/data.py tests/test_raw_observations.py tests/test_fixes.py tests/test_review_concept_2026_10_07.py
git commit -m "Cache raw closes and let callers ask for raw or filled prices

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 관측 기준 후보 최신성

**Files:**
- Modify: `src/etf_momentum/signals.py`
- Test: `tests/test_raw_observations.py`

**Interfaces:**
- Produces: `observed_freshness(mask: pd.Series | None, index: pd.DatetimeIndex) -> tuple[pd.Timestamp | None, bool]`; `rank_momentum(..., observed: pd.DataFrame | None = None)`; ranking column `last_observed`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_raw_observations.py`)

```python
# Task 2: candidate freshness from real observations
from etf_momentum.signals import observed_freshness, rank_momentum  # noqa: E402


def trending(days: int = 350) -> pd.DataFrame:
    index = pd.bdate_range("2024-01-01", periods=days)
    steps = np.arange(days)
    return pd.DataFrame({"A": 100 * 1.001**steps, "BENCH": 100 * 1.0005**steps}, index=index)


def test_unobserved_candidate_is_stale_even_though_prices_are_filled() -> None:
    prices = trending()
    observed = prices.notna()
    observed.loc[prices.index[-60]:, "A"] = False  # no real observation for 60 days
    row = rank_momentum(prices, benchmark="BENCH", candidate_tickers=("A",), observed=observed).iloc[0]
    assert not row["price_fresh"] and not row["eligible"]
    assert pd.Timestamp(row["last_observed"]) == prices.index[-61]


def test_observed_flat_price_is_fresh() -> None:
    prices = trending()
    prices.loc[prices.index[-10]:, "A"] = prices.loc[prices.index[-11], "A"]  # really unchanged for 10 days
    row = rank_momentum(prices, benchmark="BENCH", candidate_tickers=("A",), observed=prices.notna()).iloc[0]
    assert row["price_fresh"]  # observed every day, so not stale


def test_candidate_missing_from_observation_table_is_stale_not_an_error() -> None:
    prices = trending()
    row = rank_momentum(prices, benchmark="BENCH", candidate_tickers=("A",), observed=prices[["BENCH"]].notna()).iloc[0]
    assert not row["price_fresh"]


def test_observed_freshness_counts_trading_rows() -> None:
    index = pd.bdate_range("2024-01-01", periods=10)
    mask = pd.Series([True] * 4 + [False] * 6, index=index)
    assert observed_freshness(mask, index) == (index[3], False)  # 6 rows without an observation
    assert observed_freshness(mask.iloc[:9], index[:9])[1]  # 5 rows: still fresh
    assert observed_freshness(None, index) == (None, False)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --offline pytest tests/test_raw_observations.py -q`
Expected: `ImportError: cannot import name 'observed_freshness'`

- [ ] **Step 3: Implement** (in `src/etf_momentum/signals.py`)

1. In `RANKING_COLUMNS`, after `"last_price_change",` add `"last_observed",`.
2. `rank_momentum` signature: add `observed: pd.DataFrame | None = None,` after `traded_value`. Update its docstring line to:

```python
    """Rank candidate ETFs. The benchmark only feeds relative strength unless it is itself a candidate.

    `observed` (date x ticker, True where a real close exists) makes price freshness come from real
    observations; without it, a price unchanged for more than PRICE_MAX_STALE_DAYS days counts as stale."""
```

3. Replace

```python
        last_change, price_fresh = _price_freshness(history[ticker])
```

with

```python
        last_change, price_fresh = _price_freshness(history[ticker])
        last_observed = None
        if observed is not None:
            mask = observed[ticker] if ticker in observed.columns else None
            last_observed, price_fresh = observed_freshness(mask, history.index)
```

4. In the row dict, after `"last_price_change": last_change,` add `"last_observed": last_observed,`.
5. Add the function after `_price_freshness`:

```python
def observed_freshness(mask: pd.Series | None, index: pd.DatetimeIndex) -> tuple[pd.Timestamp | None, bool]:
    """Last date in `index` with a real observation, and whether at most PRICE_MAX_STALE_DAYS rows
    of `index` follow it. A ticker with no observation table column is stale."""
    if mask is None:
        return None, False
    seen = mask.reindex(index, fill_value=False).to_numpy(dtype=bool)
    positions = np.flatnonzero(seen)
    if positions.size == 0:
        return None, False
    last = int(positions[-1])
    return index[last], len(index) - 1 - last <= PRICE_MAX_STALE_DAYS
```

- [ ] **Step 4: Run tests**

Run: `uv run --offline pytest tests/test_raw_observations.py -q` → all pass
Run: `uv run --offline pytest -q` → all pass

- [ ] **Step 5: Commit**

```bash
git add src/etf_momentum/signals.py tests/test_raw_observations.py
git commit -m "Judge candidate freshness from real observations when available

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 시장 필터 최신성, `run_backtest(observed=...)`, 거래소 달력 월말

**Files:**
- Modify: `src/etf_momentum/backtest.py`
- Test: `tests/test_raw_observations.py`

**Interfaces:**
- Consumes: `observed_freshness` (Task 2)
- Produces: `select_holdings(ranking, prices, rebalance_date, config, observed=None)`; `run_backtest(prices, config, traded_value=None, observed=None)`; `previous_month_end(dates, today=None, sessions: pd.DatetimeIndex | None = None)`

- [ ] **Step 1: Write the failing tests** (append)

```python
# Task 3: market filter freshness, backtest pass-through, exchange-calendar month-end
from etf_momentum.backtest import BacktestConfig, previous_month_end, run_backtest, select_holdings  # noqa: E402
from etf_momentum.signals import MomentumConfig  # noqa: E402

ELIGIBLE_A = pd.DataFrame({"ticker": ["A"], "eligible": [True]})


def test_stale_market_filter_is_an_error() -> None:
    prices = trending(650)
    observed = prices.notna()
    observed.loc[prices.index[-60]:, "BENCH"] = False  # review repro: frozen, unobserved benchmark
    with pytest.raises(ValueError, match="BENCH"):
        select_holdings(ELIGIBLE_A, prices, prices.index[-1], BacktestConfig(top_n=1, benchmark="BENCH", risk_on_benchmark=True), observed=observed)


def test_one_stale_filter_among_several_is_an_error() -> None:
    prices = trending(650).assign(US=lambda f: f["BENCH"])
    observed = prices.notna()
    observed.loc[prices.index[-30]:, "US"] = False
    config = BacktestConfig(top_n=1, benchmark="BENCH", risk_on_benchmark=True, risk_on_tickers=("BENCH", "US"))
    with pytest.raises(ValueError, match="US"):
        select_holdings(ELIGIBLE_A, prices, prices.index[-1], config, observed=observed)


def test_filter_never_observed_yet_is_an_error_not_risk_on() -> None:
    prices = trending(650)
    observed = prices.notna()
    observed["BENCH"] = False
    with pytest.raises(ValueError, match="BENCH"):
        select_holdings(ELIGIBLE_A, prices, prices.index[300], BacktestConfig(top_n=1, benchmark="BENCH", risk_on_benchmark=True), observed=observed)


def test_run_backtest_excludes_unobserved_candidate() -> None:
    index = pd.bdate_range("2020-01-01", periods=600)
    steps = np.arange(600)
    prices = pd.DataFrame({"A": 100 * 1.002**steps, "B": 100 * 1.001**steps, "BENCH": 100.0, "CASH": 100.0}, index=index)
    observed = prices.notna()
    observed.loc[index[300]:, "A"] = False  # A's data stopped; its filled price still trends up
    _, positions, _ = run_backtest(
        prices,
        BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", candidate_tickers=("A", "B"),
                       momentum=MomentumConfig(skip_days=0, sma_window=100)),
        observed=observed,
    )
    late = positions[pd.to_datetime(positions["rebalance_date"]) > index[320]]
    assert "A" not in ",".join(late["tickers"])


SESSIONS = pd.bdate_range("2026-08-01", "2026-10-31").drop(pd.to_datetime(["2026-09-24", "2026-09-25"]))


def test_month_end_gap_then_next_month_is_an_error_with_sessions() -> None:
    with pytest.raises(ValueError, match="2026-09-30"):
        previous_month_end(pd.to_datetime(["2026-09-15", "2026-10-01"]), pd.Timestamp("2026-10-08"), sessions=SESSIONS)


def test_month_end_is_the_last_session() -> None:
    dates = pd.bdate_range("2026-09-01", "2026-10-07")
    assert previous_month_end(dates, pd.Timestamp("2026-10-08"), sessions=SESSIONS) == pd.Timestamp("2026-09-30")


def test_holiday_row_in_data_does_not_move_the_month_end() -> None:
    sessions = pd.bdate_range("2025-12-01", "2026-01-31").drop(pd.to_datetime(["2025-12-25", "2025-12-31", "2026-01-01"]))
    dates = sessions.union(pd.to_datetime(["2025-12-31"]))  # a stray row on the 12-31 closure
    assert previous_month_end(dates, pd.Timestamp("2026-01-05"), sessions=sessions) == pd.Timestamp("2025-12-30")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run --offline pytest tests/test_raw_observations.py -q`
Expected: `TypeError: select_holdings() got an unexpected keyword argument 'observed'` (and similar for `run_backtest`, `previous_month_end`)

- [ ] **Step 3: Implement** (in `src/etf_momentum/backtest.py`)

1. Import: `from etf_momentum.signals import MomentumConfig, observed_freshness, rank_momentum`.
2. `select_holdings` signature and filter block:

```python
def select_holdings(
    ranking: pd.DataFrame,
    prices: pd.DataFrame,
    rebalance_date: pd.Timestamp,
    config: BacktestConfig,
    observed: pd.DataFrame | None = None,
) -> Selection:
```

and after the existing `missing` check (`raise ValueError(f"Market filter ticker(s) without price data: ...")`) add:

```python
        if observed is not None:
            history = prices.loc[:rebalance_date].index
            stale = [
                t for t in filters
                if not observed_freshness(observed[t] if t in observed.columns else None, history)[1]
            ]
            if stale:
                raise ValueError(
                    f"Market filter ticker(s) without a recent observation at {pd.Timestamp(rebalance_date).date()}: "
                    f"{', '.join(stale)}"
                )
```

3. `run_backtest` signature: add `observed: pd.DataFrame | None = None,` after `traded_value`. In its loop, pass `observed=observed` to `rank_momentum(...)` and change `weights = _select_weights(ranking, prices, rebalance_date, config)` to `weights = _select_weights(ranking, prices, rebalance_date, config, observed)`.
4. `_select_weights`: add parameter `observed: pd.DataFrame | None = None` and call `select_holdings(ranking, prices, rebalance_date, config, observed)`.
5. `previous_month_end`:

```python
def previous_month_end(
    dates: pd.DatetimeIndex, today: pd.Timestamp | None = None, sessions: pd.DatetimeIndex | None = None
) -> pd.Timestamp:
    """Last trading day of the month before `today`'s month, checked to be really in the data.

    With exchange `sessions`, that day is the month's last session and it must be in `dates`.
    Without them, the month counts as complete when its last date in `dates` is the month's last
    weekday or the data continues into the next month. A missing month never falls back to an older one.
    """
    today = pd.Timestamp(today or pd.Timestamp.today()).normalize()
    dates = pd.DatetimeIndex(dates).sort_values()
    month = today.to_period("M") - 1
    if sessions is not None:
        in_month_sessions = pd.DatetimeIndex(sessions)[pd.DatetimeIndex(sessions).to_period("M") == month]
        if len(in_month_sessions):
            last_session = in_month_sessions[-1].normalize()
            if last_session not in dates.normalize():
                raise ValueError(f"{month} last trading day {last_session.date()} is missing from the data")
            return last_session
    in_month = dates[dates.to_period("M") == month]
    if in_month.empty:
        raise ValueError(f"No price data for {month} (the month before {today:%Y-%m})")
    last = in_month[-1]
    last_weekday = pd.bdate_range(month.start_time, month.end_time)[-1]
    complete = last.normalize() >= last_weekday.normalize() or bool((dates > month.end_time).any())
    if not complete:
        raise ValueError(f"{month} month-end data is incomplete: last observation {last.date()}")
    return last
```

- [ ] **Step 4: Run tests**

Run: `uv run --offline pytest tests/test_raw_observations.py -q` → all pass
Run: `uv run --offline pytest -q` → all pass

- [ ] **Step 5: Commit**

```bash
git add src/etf_momentum/backtest.py tests/test_raw_observations.py
git commit -m "Check market filter freshness, pass observations through backtests, month-end by exchange sessions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: CLI 연결 (관측표, 실행 전 검사, 거래소 달력, 포트폴리오 원자료)

**Files:**
- Modify: `src/etf_momentum/cli.py`, `pyproject.toml`, `uv.lock`
- Modify tests whose `previous_month_end` stand-in takes `(dates, today=None)`: `tests/test_selection.py:125`, `tests/test_strategy_cli.py:60`, `tests/test_remaining_features.py:101`, `tests/test_review_2026_10_08.py:57` → add `**kw` to each lambda, e.g. `lambda dates, today=None, **kw: backtest.previous_month_end(dates, pd.Timestamp("2026-10-07"))`
- Test: `tests/test_raw_observations.py`

**Interfaces:**
- Consumes: Tasks 1-3
- Produces: `cli._observations(tickers, start, args, prices) -> pd.DataFrame`; `cli._require_fresh(observed, required) -> None`; `cli._exchange_sessions(benchmark) -> pd.DatetimeIndex | None`; `args.proxy_starts: dict[str, pd.Timestamp]`

- [ ] **Step 1: Add the dependency**

Run: `uv add "exchange_calendars>=4.5"`
Expected: `pyproject.toml` dependencies gain `exchange_calendars>=4.5`; `uv.lock` updated.

- [ ] **Step 2: Write the failing tests** (append)

```python
# Task 4: CLI wiring
import argparse  # noqa: E402

import etf_momentum.cli as cli  # noqa: E402


def test_required_ticker_without_recent_observation_stops() -> None:
    index = pd.bdate_range("2026-01-01", periods=40)
    observed = pd.DataFrame({"BENCH": True, "CASH": True}, index=index)
    observed.loc[index[-8]:, "CASH"] = False
    with pytest.raises(SystemExit, match="cash CASH"):
        cli._require_fresh(observed, {"benchmark": "BENCH", "cash": "CASH"})
    cli._require_fresh(observed, {"benchmark": "BENCH", "cash": None})


def test_proxy_filled_history_counts_as_observed(monkeypatch) -> None:
    index = pd.bdate_range("2026-01-01", periods=10)
    raw = pd.DataFrame({"K": [np.nan] * 5 + [10.0] * 5, "BENCH": 1.0}, index=index)
    monkeypatch.setattr(cli, "download_prices", lambda tickers, **kw: raw[[t for t in tickers if t in raw]])
    prices = raw.copy()
    prices.loc[index[:5], "K"] = 9.0  # spliced from a proxy
    args = argparse.Namespace(end=None, cache_dir="data", refresh=False, proxy_starts={"K": index[5]})
    observed = cli._observations(["K", "BENCH"], None, args, prices)
    assert observed["K"].all()


def test_exchange_sessions_follow_the_benchmark_suffix() -> None:
    kr = cli._exchange_sessions("069500.KS")
    us = cli._exchange_sessions("SPY")
    assert pd.Timestamp("2025-12-31") not in kr and pd.Timestamp("2025-12-31") in us  # KRX year-end closure
    assert pd.Timestamp("2023-09-28") not in kr  # Chuseok


def test_month_outside_the_calendar_falls_back_with_a_note(capsys, monkeypatch) -> None:
    monkeypatch.setattr(cli, "_exchange_sessions", lambda benchmark: pd.bdate_range("2010-01-01", "2026-06-30"))
    sessions = cli._month_end_sessions("069500.KS", pd.Timestamp("2026-10-08"))
    assert sessions is None and "거래소 달력" in capsys.readouterr().out


def test_portfolio_ticker_sleeve_with_a_raw_gap_stops(tmp_path, monkeypatch) -> None:
    import json
    import sys

    index = pd.bdate_range("2023-01-02", periods=300)
    raw = pd.DataFrame({"KR": np.linspace(100, 130, 300), "CASH": 100.0}, index=index)
    raw.loc[index[150], "KR"] = np.nan  # the review's repro: a missing close inside the series

    def fake(tickers, raw=False, **kw):
        frame = pd.DataFrame(raw_frame[[t for t in tickers if t in raw_frame]])
        return frame if raw else frame.ffill()

    raw_frame = raw
    monkeypatch.setattr(cli, "download_prices", fake)
    spec = {"base_currency": "KRW", "calendar_ticker": "CASH", "cash_ticker": "CASH", "fx": {},
            "sleeves": {"KR": {"ticker": "KR", "currency": "KRW"}}, "scenarios": [{"name": "kr", "weights": {"KR": 1.0}}]}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    monkeypatch.setattr(sys, "argv", ["etf-momentum", "portfolio", "--spec", str(tmp_path / "spec.json"), "--output-dir", str(tmp_path / "o")])
    with pytest.raises(SystemExit, match="NaN"):
        cli.main()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run --offline pytest tests/test_raw_observations.py -q`
Expected: `AttributeError: module 'etf_momentum.cli' has no attribute '_require_fresh'` (and the other helpers)

- [ ] **Step 4: Implement** (in `src/etf_momentum/cli.py`)

1. Imports: `import functools`; `from etf_momentum.signals import observed_freshness` (extend the existing signals import).
2. Helpers (next to `_require_tickers`):

```python
def _observations(tickers: list[str], start: str | None, args: argparse.Namespace, prices: pd.DataFrame) -> pd.DataFrame:
    """Date x ticker table of real closes (same raw cache as the prices, no extra download).

    Days filled from a proxy before listing count as observed: the proxy was observed then."""
    raw = download_prices(tickers, start=start, end=args.end, cache_dir=args.cache_dir, refresh=args.refresh, raw=True)
    observed = raw.reindex(index=prices.index, columns=prices.columns).notna()
    for ticker, first_real in getattr(args, "proxy_starts", {}).items():
        if ticker in observed.columns:
            before = observed.index < first_real
            observed.loc[before, ticker] = prices.loc[before, ticker].notna()
    return observed


def _require_fresh(observed: pd.DataFrame, required: dict[str, str | None]) -> None:
    """Stop when a benchmark, market filter or cash ETF has no recent real observation."""
    stale = []
    for role, ticker in required.items():
        if not ticker:
            continue
        last, fresh = observed_freshness(observed[ticker] if ticker in observed.columns else None, observed.index)
        if not fresh:
            stale.append(f"{role} {ticker} (last observation {last.date() if last is not None else 'none'})")
    if stale:
        raise SystemExit(f"Stale data for required ticker(s): {', '.join(stale)}. Rerun with --refresh or check the ticker")


@functools.lru_cache(maxsize=4)
def _exchange_sessions(benchmark: str) -> pd.DatetimeIndex | None:
    """Trading days of the benchmark's exchange: XKRX for .KS/.KQ tickers, otherwise XNYS."""
    import exchange_calendars as xc

    code = "XKRX" if benchmark.upper().endswith((".KS", ".KQ")) else "XNYS"
    return pd.DatetimeIndex(xc.get_calendar(code, start="2005-01-01").sessions).tz_localize(None)


def _month_end_sessions(benchmark: str, today: pd.Timestamp) -> pd.DatetimeIndex | None:
    """Exchange sessions when the calendar covers the month before `today`; otherwise None with a note."""
    sessions = _exchange_sessions(benchmark)
    month = pd.Timestamp(today).to_period("M") - 1
    if sessions is None or not ((sessions.to_period("M") == month).any() and sessions[-1] >= month.end_time.normalize()):
        print(f"거래소 달력이 {month}을(를) 다루지 않아 기존 월말 규칙으로 판단합니다")
        return None
    return sessions
```

3. `_apply_proxies`: at its top add `args.proxy_starts = {}`; after `prices, starts = splice_proxies(...)` add `args.proxy_starts = starts`.
4. In each of `run_rank_command`, `run_weekly_command`, `run_backtest_command`, `run_compare_command`, `run_walkforward_command`, directly after the line `prices, traded_value = _apply_proxies(...)` add

```python
    observed = _observations(tickers, <start>, args, prices)
```

using the same `<start>` expression that command passes to `_apply_proxies` (`start` in rank/weekly, `args.start` elsewhere). Then directly after the existing `_require_tickers(...)` line add `_require_fresh(observed, <same dict passed to _require_tickers>)`.
5. Pass `observed=observed` to every `rank_momentum(...)` call in those commands and in `_month_end_selection` (add an `observed` parameter to `_month_end_selection` and pass it from `run_rank_command`), and to every `run_backtest(...)` call.
6. In `_month_end_selection`, replace

```python
        as_of = previous_month_end(prices.index, today=_selection_today(args))
```

with

```python
        today = _selection_today(args) or pd.Timestamp.today().normalize()
        benchmark_days = observed.index[observed[args.benchmark]] if args.benchmark in observed.columns else prices.index
        as_of = previous_month_end(benchmark_days, today=today, sessions=_month_end_sessions(args.benchmark, today))
```

and pass `observed=observed` to the `select_holdings(...)` call there.
7. Portfolio `own_dates`: pass `raw=True` to its `download_prices(...)` call so the calendar and `same_day` sleeves use raw closes.
8. Update the four test stand-ins for `previous_month_end` listed under **Files** to accept `**kw`.

- [ ] **Step 5: Run tests**

Run: `uv run --offline pytest tests/test_raw_observations.py -q` → all pass
Run: `uv run --offline pytest -q` → all pass

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/etf_momentum/cli.py tests/
git commit -m "Wire observations, freshness checks and exchange calendars into the CLI

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: 실제 데이터 확인, 연구 수치 재계산, 문서

**Files:**
- Modify: `README.md`, `CLAUDE.md`, `docs/research_summary_2026-10-06.md`

- [ ] **Step 1: Smoke run (network)**

Run: `uv run etf-momentum rank --strategy config/strategies/pension.json --top 3 --output-dir "$SCRATCH/raw-smoke"` (`$SCRATCH` = the session scratchpad directory)
Expected: downloads into new `prices_raw_*` caches; prints the 2026-09-30 selection (same 4 ETFs unless the fresh data differs) and no stale-data error.

- [ ] **Step 2: Snapshot the current research numbers**

Run: `cp results/research_tables.md "$SCRATCH/research_tables_before.md"`

- [ ] **Step 3: Regenerate every research result (clean tree, background, long timeout)**

First confirm `git status --short` prints nothing. Then run this script with `run_in_background` and a 2-hour timeout (it takes about 10-15 minutes plus the one-time re-download):

```bash
R=results/walkforward
common="--start 2010-01-01 --end 2026-10-07 --top-n-grid 3,4 --max-per-group-grid 1"
KR="--universe config/universe_kr_etfs.csv --benchmark 069500.KS --cash 153130.KS --transaction-cost-bps 10 --min-avg-traded-value 1e9"
US="--universe config/universe_us_sectors.csv --benchmark SPY --cash BIL --transaction-cost-bps 5 --min-avg-traded-value 2e7"
PEN="--universe config/universe_kr_pension.csv --benchmark 069500.KS --cash 153130.KS --transaction-cost-bps 10 --min-avg-traded-value 1e9 --use-proxies"
run() { name=$1; shift; mkdir -p $R/$name; uv run etf-momentum walkforward $common "$@" --output-dir $R/$name > $R/$name/log.txt 2>&1 & }
# prewarm the new raw caches once, sequentially, so parallel runs don't race
uv run etf-momentum walkforward $common $PEN --account pension --top-n-grid 4 --weighting-grid equal --rank-by-grid weighted_momentum --risk-on-grid on --relative-strength-grid off --output-dir /tmp/prewarm-pen > /dev/null 2>&1
uv run etf-momentum walkforward $common $KR --top-n-grid 4 --weighting-grid equal --rank-by-grid weighted_momentum --risk-on-grid on --relative-strength-grid off --output-dir /tmp/prewarm-kr > /dev/null 2>&1
uv run etf-momentum walkforward $common $US --top-n-grid 4 --weighting-grid equal --rank-by-grid weighted_momentum --risk-on-grid on --relative-strength-grid off --output-dir /tmp/prewarm-us > /dev/null 2>&1
run us_20m_5bp_robustgrid_tvoff $US
run kr_10eok_10bp_robustgrid_tvoff $KR
run kr_10eok_10bp_robustgrid_tv15 $KR --target-vol 0.15
run kr_10eok_10bp_robustgrid_tv10 $KR --target-vol 0.10
run kr_pension_pension_10eok_10bp_robustgrid_tvoff $PEN --account pension
wait
run kr_pension_retirement_10eok_10bp_robustgrid_tvoff $PEN --account retirement
run kr_pension_pension_10eok_10bp_robustgrid_tvoff_filter_kr $PEN --account pension --risk-on-grid on
run kr_pension_pension_10eok_10bp_robustgrid_tvoff_filter_us $PEN --account pension --risk-on-grid on --risk-on-ticker 360750.KS
run kr_pension_pension_10eok_10bp_robustgrid_tvoff_filter_kr_or_us $PEN --account pension --risk-on-grid on --risk-on-ticker 069500.KS,360750.KS
run kr_pension_pension_10eok_10bp_robustgrid_tvoff_filter_none $PEN --account pension --risk-on-grid off
run kr_pension_pension_10eok_10bp_robustgrid_tvoff_nextclose $PEN --account pension --execution next_close
wait
for w in equal inverse_vol; do
  uv run etf-momentum backtest --strategy config/strategies/pension.json --start 2010-01-01 --end 2026-10-07 --transaction-cost-bps 10 --weighting $w --output-dir results/backtest/pension_fixed_top4_$w > /dev/null 2>&1
  uv run etf-momentum backtest --strategy config/strategies/pension.json --start 2010-01-01 --end 2026-10-07 --transaction-cost-bps 10 --weighting $w --execution next_close --output-dir results/backtest/pension_fixed_top4_${w}_nextclose > /dev/null 2>&1
done
uv run etf-momentum portfolio --spec config/portfolio_pension.json > /dev/null 2>&1
uv run etf-momentum portfolio --spec config/portfolio_execution.json --output-dir results/portfolio_execution > /dev/null 2>&1
uv run etf-momentum portfolio --spec config/portfolio_spy_kr.json --output-dir results/portfolio_spy_kr > /dev/null 2>&1
uv run python scripts/research_tables.py --end 2025-02-28 > /dev/null 2>&1
grep -l Traceback $R/*/log.txt || echo "no errors"
```

Expected: `no errors`; every `*_run_config.json` written by this run shows the new commit with `"dirty": false`.

- [ ] **Step 4: Compare**

Run: `diff "$SCRATCH/research_tables_before.md" results/research_tables.md`
Expected: either no difference, or small differences to report row by row.

- [ ] **Step 5: Update docs**

- `README.md` "데이터 확인" section: replace the price-freshness bullet with: 가격 캐시는 앞 값으로 채우지 않은 종가(`data/prices_raw_*`)를 저장합니다. 후보 ETF는 마지막 실제 관측 후 5거래일이 넘으면 제외하고(`price_fresh`, `last_observed`), 벤치마크·시장 필터·현금 ETF가 그렇게 오래됐으면 실행을 멈춥니다. 실제로 가격이 그대로인 날은 오래된 것으로 보지 않습니다. And in the month-end paragraph: 직전 월말은 거래소 달력(`exchange_calendars`, `.KS`면 KRX, 그 외 NYSE)의 마지막 거래일이며, 그날 벤치마크 데이터가 없으면 멈춥니다.
- `CLAUDE.md` data pitfalls: add "가격 캐시는 원자료(`prices_raw_*`)다. 앞 값 채우기는 계산 직전에만 한다."
- `docs/research_summary_2026-10-06.md`: update every changed number from Step 4 and add one line to the header noting the 2026-10-09 re-download (raw caches) and whether numbers changed.

- [ ] **Step 6: Commit**

```bash
git add README.md CLAUDE.md docs/research_summary_2026-10-06.md
git commit -m "Document raw price observations and exchange-calendar month-ends; refresh research numbers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
