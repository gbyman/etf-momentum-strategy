"""Third recheck review of 2026-10-11 (docs/review_2026-10-11_recheck3_codex.md)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import etf_momentum.data as data
from etf_momentum.backtest import BacktestConfig, StaleDataError, run_backtest
from etf_momentum.signals import MomentumConfig

SESSIONS = pd.bdate_range("2024-01-02", "2024-03-15")
SMALL = MomentumConfig(lookbacks=(1, 2, 3), skip_days=0, sma_window=2, volatility_window=2)
CONFIG = BacktestConfig(top_n=1, candidate_tickers=("A",), momentum=SMALL)


def panel() -> pd.DataFrame:
    n = len(SESSIONS)
    return pd.DataFrame({"A": 100 * 1.002 ** np.arange(n), "SPY": 100 * 1.001 ** np.arange(n), "BIL": 100.0}, index=SESSIONS)


def download(frame: pd.DataFrame, tmp_path, monkeypatch) -> pd.DataFrame:
    yahoo = frame.copy()
    yahoo.columns = pd.MultiIndex.from_tuples([("Close", t) for t in frame.columns])
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo)
    return data.download_prices(list(frame.columns), start="2024-01-02", end="2024-03-16", cache_dir=tmp_path, raw=True, refresh=True)


# E01: zero, negative and infinite closes are not real observations
@pytest.mark.parametrize("bad", [0.0, -1.0, np.inf, -np.inf])
def test_invalid_close_is_treated_as_missing(bad: float, tmp_path, monkeypatch) -> None:
    prices = panel()
    prices.loc["2024-02-05", "A"] = bad
    with pytest.warns(UserWarning, match="A"):
        raw = download(prices, tmp_path, monkeypatch)
    assert np.isnan(raw.loc["2024-02-05", "A"])
    equity, _, _ = run_backtest(raw.ffill(), CONFIG, observed=raw.notna(), sessions=SESSIONS)
    assert np.isfinite(equity["strategy"]).all() and (equity["strategy"] > 0).all()


def test_invalid_closes_in_a_cache_file_are_treated_as_missing(tmp_path, monkeypatch) -> None:
    cache = data._cache_path(["A", "SPY"], "2024-01-02", "2024-03-16", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    prices = panel()[["A", "SPY"]]
    prices.loc["2024-01-02":"2024-01-31", "A"] = -5.0  # e.g. Yahoo's negative adjusted closes for 381560.KS
    prices.to_csv(cache)
    monkeypatch.setattr(data.yf, "download", lambda **kw: pytest.fail("cache should be used"))
    raw = data.download_prices(["A", "SPY"], start="2024-01-02", end="2024-03-16", cache_dir=tmp_path, raw=True)
    assert raw.loc[:"2024-01-31", "A"].isna().all() and raw.loc["2024-02-01":, "A"].notna().all()


# E02: a gap every ticker shares (rows absent from the data) still counts in exchange sessions
def run_without(days: int):
    prices = panel()
    prices = prices.drop(SESSIONS[(SESSIONS >= "2024-02-05")][:days])  # the rows are gone, not NaN
    return run_backtest(prices, CONFIG, observed=prices.notna(), sessions=SESSIONS)


def test_six_missing_sessions_shared_by_every_ticker_stop_the_backtest() -> None:
    with pytest.raises(StaleDataError, match="more than 5"):
        run_without(6)


def test_five_missing_sessions_are_within_the_limit() -> None:
    equity, _, _ = run_without(5)
    assert not equity.empty
