"""Full review of 2026-10-13 (docs/review_2026-10-13_full_codex.md)."""

from __future__ import annotations

import contextlib
import io
import json
import sys

import numpy as np
import pandas as pd
import pytest

import etf_momentum.cli as cli
import etf_momentum.data as data
import etf_momentum.naver as naver
from etf_momentum.backtest import BacktestConfig, StaleDataError, run_backtest
from etf_momentum.signals import MomentumConfig, observed_freshness, rank_momentum


# G01: sessions missing from the data altogether still count toward a ticker's staleness
SESSIONS = cli._exchange_sessions("SPY")
SESSIONS = SESSIONS[(SESSIONS >= "2024-01-01") & (SESSIONS <= "2025-09-30")]
GAP = SESSIONS[-8:-1]  # 2025-09-19 .. 2025-09-29: no ticker has a close


def gapped_panel() -> pd.DataFrame:
    n = np.arange(len(SESSIONS))
    frame = pd.DataFrame({"A": 100 * np.exp(n * 0.001), "SPY": 100 * np.exp(n * 0.0002), "BIL": 100.0}, index=SESSIONS)
    frame.loc[GAP, :] = np.nan
    frame.loc[SESSIONS[-1], "A"] = np.nan  # only the benchmark and cash come back on 9/30
    return frame.dropna(how="all")  # what the download keeps: the gap days have no row


def test_freshness_counts_sessions_without_a_row() -> None:
    raw = gapped_panel()
    assert observed_freshness(raw["A"].notna(), raw.index)[1]  # one row after 9/18
    last, fresh = observed_freshness(raw["A"].notna(), raw.index, sessions=SESSIONS)
    assert last == pd.Timestamp("2025-09-18") and not fresh  # eight sessions after it
    assert observed_freshness(raw["SPY"].notna(), raw.index, sessions=SESSIONS)[1]


def test_freshness_allows_five_sessions_after_the_last_close_but_not_six() -> None:
    index = SESSIONS[-30:]
    for age, fresh in ((5, True), (6, False)):
        rows = index[: len(index) - age].append(index[-1:])  # last close, then only the final session has a row
        mask = pd.Series(True, index=rows)
        mask.iloc[-1] = False
        assert observed_freshness(mask, rows, sessions=SESSIONS)[1] is fresh


def test_freshness_without_a_calendar_counts_rows() -> None:
    raw = gapped_panel()
    assert observed_freshness(raw["A"].notna(), raw.index, sessions=None)[1]


def test_rank_excludes_a_candidate_stale_in_sessions() -> None:
    raw = gapped_panel()
    row = rank_momentum(raw.ffill(), benchmark="SPY", candidate_tickers=["A"], observed=raw.notna(), sessions=SESSIONS).iloc[0]
    assert not row["price_fresh"] and not row["eligible"]


def test_required_ticker_stale_in_sessions_stops() -> None:
    raw = gapped_panel()
    with pytest.raises(SystemExit, match="filter A"):
        cli._require_fresh(raw.notna(), {"filter": "A"}, SESSIONS)
    cli._require_fresh(raw.notna(), {"benchmark": "SPY", "cash": "BIL"}, SESSIONS)


def test_backtest_stops_on_sessions_without_a_row() -> None:
    # The benchmark misses those sessions too, so the held-gap check (E02) already stops the run.
    raw = gapped_panel()
    config = BacktestConfig(
        top_n=1,
        candidate_tickers=("A",),
        risk_on_benchmark=True,
        risk_on_tickers=("A",),
        momentum=MomentumConfig(lookbacks=(21, 42, 63), skip_days=0, sma_window=20, volatility_window=20),
    )
    with pytest.raises(StaleDataError, match="SPY"):
        run_backtest(raw.ffill(), config, observed=raw.notna(), sessions=SESSIONS)


def test_rank_command_does_not_select_a_candidate_stale_in_sessions(tmp_path, monkeypatch) -> None:
    raw = gapped_panel().reindex(SESSIONS)  # Yahoo's response before cleaning
    response = raw.copy()
    response.columns = pd.MultiIndex.from_product([["Close"], list(raw.columns)])
    monkeypatch.setattr(data.yf, "download", lambda **kw: response)
    universe = tmp_path / "universe.csv"
    pd.DataFrame(
        {"ticker": ["A", "SPY", "BIL"], "name": ["A", "SPY", "BIL"], "category": ["equity", "equity", "cash"], "enabled": [1, 1, 1]}
    ).to_csv(universe, index=False)
    argv = ["etf-momentum", "rank", "--universe", str(universe), "--benchmark", "SPY", "--cash", "BIL", "--top-n", "1",
            "--lookback-years", "2", "--end", "2025-10-01", "--output-dir", str(tmp_path / "out"), "--cache-dir", str(tmp_path / "cache")]
    monkeypatch.setattr(sys, "argv", argv)
    with contextlib.redirect_stdout(io.StringIO()):
        cli.main()
    row = pd.read_csv(tmp_path / "out" / "latest_ranking.csv").iloc[0]
    assert row["ticker"] == "A" and not row["price_fresh"] and not row["eligible"]
    selection = pd.read_csv(tmp_path / "out" / "latest_selection.csv")
    assert "A" not in selection.loc[selection["status"] == "selected", "ticker"].tolist()


# G02: a retried ticker whose Naver fill failed stays missing and is retried again
DAYS = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
TICKERS = ["A.KS", "NEW.KS"]


def yahoo(columns: dict[str, list[float]]) -> pd.DataFrame:
    frame = pd.DataFrame(columns, index=DAYS)
    frame.columns = pd.MultiIndex.from_product([["Close"], list(columns)])
    return frame


def naver_ok(ticker, start, end):
    return pd.Series([100.0, 110.0, 120.0], index=DAYS)


def naver_down(ticker, start, end):
    raise OSError("Naver temporarily unavailable")


def pinned(tmp_path):
    return data.download_prices(TICKERS, start="2024-01-01", end="2024-01-05", cache_dir=tmp_path, raw=True)


def cache_path(tmp_path):
    return data._cache_path(TICKERS, "2024-01-01", "2024-01-05", tmp_path, prefix=data.PRICE_CACHE_PREFIX)


def start_with_new_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(data, "NAVER_FILL", True)
    monkeypatch.setattr(naver, "naver_closes", naver_ok)
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo({"A.KS": [100.0, 110.0, 120.0], "NEW.KS": [np.nan] * 3}))
    with pytest.warns(UserWarning, match="NEW.KS"):
        pinned(tmp_path)
    assert data._recorded_missing(cache_path(tmp_path)) == ["NEW.KS"]


def test_retry_with_a_failed_naver_fill_keeps_the_ticker_missing(tmp_path, monkeypatch) -> None:
    start_with_new_missing(tmp_path, monkeypatch)
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo({"NEW.KS": [100.0, np.nan, 120.0]}))
    monkeypatch.setattr(naver, "naver_closes", naver_down)
    with pytest.warns(UserWarning, match="Naver gap fill failed"), pytest.warns(UserWarning, match="Still no prices data for NEW.KS"):
        prices = pinned(tmp_path)
    assert list(prices.columns) == ["A.KS"]
    assert data._recorded_missing(cache_path(tmp_path)) == ["NEW.KS"]
    assert "NEW.KS" not in pd.read_csv(cache_path(tmp_path), index_col=0).columns

    monkeypatch.setattr(naver, "naver_closes", naver_ok)  # Naver is back: the next run retries and fills
    prices = pinned(tmp_path)
    assert prices["NEW.KS"].tolist() == [100.0, 110.0, 120.0]
    assert not data._missing_record_path(cache_path(tmp_path)).exists()
    record = json.loads(data._fill_record_path(cache_path(tmp_path)).read_text())
    assert record["filled"]["NEW.KS"] == ["2024-01-03"]


def test_leftover_retry_cache_is_not_taken_for_a_new_download(tmp_path, monkeypatch) -> None:
    start_with_new_missing(tmp_path, monkeypatch)
    retry = data._cache_path(["NEW.KS"], "2024-01-01", "2024-01-05", tmp_path / ".retry", prefix=data.PRICE_CACHE_PREFIX)
    retry.parent.mkdir(exist_ok=True)
    pd.DataFrame({"NEW.KS": [1.0, 1.0, 1.0]}, index=DAYS).to_csv(retry)
    data._fill_record_path(retry).write_text(json.dumps({"version": data.NAVER_FILL_VERSION, "filled": {}}))
    monkeypatch.setattr(data.yf, "download", lambda **kw: yahoo({"NEW.KS": [100.0, np.nan, 120.0]}))
    monkeypatch.setattr(naver, "naver_closes", naver_down)
    with pytest.warns(UserWarning, match="Naver gap fill failed"), pytest.warns(UserWarning, match="Still no prices data for NEW.KS"):
        prices = pinned(tmp_path)
    assert list(prices.columns) == ["A.KS"]
    assert data._recorded_missing(cache_path(tmp_path)) == ["NEW.KS"]
