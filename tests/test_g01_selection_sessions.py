"""G01 below the top-level ranking: the backtest's ranking and every market-filter check count exchange
sessions with no row (docs/review_2026-10-13_full_codex.md, recheck 2026-10-17)."""

from __future__ import annotations

import contextlib
import io
import sys

import numpy as np
import pandas as pd
import pytest

import etf_momentum.cli as cli
import etf_momentum.data as data
from etf_momentum.backtest import BacktestConfig, StaleDataError, run_backtest, select_holdings
from etf_momentum.signals import MomentumConfig, rank_momentum

XNYS = cli._exchange_sessions("SPY")
DAYS = XNYS[(XNYS >= "2025-01-02") & (XNYS <= "2025-10-31")]
NO_ROW = pd.DatetimeIndex(["2025-09-25", "2025-09-26", "2025-09-29"])  # no ticker: benchmark gap 3 sessions
X_MISSING = pd.DatetimeIndex(["2025-09-23", "2025-09-24", "2025-09-30"])  # X only: 3 rows, 6 sessions after 09-22
MONTH_END = pd.Timestamp("2025-09-30")
SMALL = MomentumConfig(lookbacks=(21, 42, 63), skip_days=0, sma_window=20, volatility_window=20)


def full_panel() -> pd.DataFrame:
    """X flat through August (never held), then up 1% a day; B, SPY steady; BIL cash."""
    n = np.arange(len(DAYS))
    start = np.searchsorted(DAYS, pd.Timestamp("2025-08-29"))
    x = 100 * np.exp(np.clip(n - start, 0, None) * 0.01)
    return pd.DataFrame({"X": x, "B": 100 * np.exp(n * 0.001), "SPY": 100 * np.exp(n * 0.0005), "BIL": 100.0}, index=DAYS)


def gapped() -> pd.DataFrame:
    raw = full_panel()
    raw.loc[X_MISSING, "X"] = np.nan
    return raw.drop(NO_ROW)


def signal_on(positions: pd.DataFrame, date: pd.Timestamp) -> str:
    return positions.loc[pd.to_datetime(positions["rebalance_date"]) == date, "tickers"].iloc[0]


def test_backtest_does_not_pick_a_candidate_stale_in_sessions() -> None:
    raw = gapped()
    config = BacktestConfig(top_n=1, candidate_tickers=("X", "B"), momentum=SMALL, benchmark="SPY", cash_ticker="BIL")
    _, positions, _ = run_backtest(raw.ffill(), config, observed=raw.notna(), sessions=XNYS)
    assert signal_on(positions, MONTH_END) == "B"  # the rank command's choice for the same month end


def test_backtest_picks_the_same_candidate_when_it_is_observed() -> None:
    raw = full_panel()
    config = BacktestConfig(top_n=1, candidate_tickers=("X", "B"), momentum=SMALL, benchmark="SPY", cash_ticker="BIL")
    _, positions, _ = run_backtest(raw, config, observed=raw.notna(), sessions=XNYS)
    assert signal_on(positions, MONTH_END) == "X"


def test_backtest_market_filter_stale_in_sessions_stops() -> None:
    raw = gapped()
    config = BacktestConfig(
        top_n=1, candidate_tickers=("B",), momentum=SMALL, benchmark="SPY", cash_ticker="BIL",
        risk_on_benchmark=True, risk_on_tickers=("X",),
    )
    with pytest.raises(StaleDataError, match="Market filter.*2025-09-30.*X"):
        run_backtest(raw.ffill(), config, observed=raw.notna(), sessions=XNYS)


def test_select_holdings_counts_sessions_and_falls_back_to_rows_without_a_calendar() -> None:
    raw = gapped()
    ranking = rank_momentum(raw.ffill(), MONTH_END, benchmark="SPY", config=SMALL, candidate_tickers=["B"], observed=raw.notna())
    rule = BacktestConfig(top_n=1, momentum=SMALL, benchmark="SPY", cash_ticker="BIL", risk_on_benchmark=True, risk_on_tickers=("X",))
    with pytest.raises(StaleDataError, match="Market filter"):
        select_holdings(ranking, raw.ffill(), MONTH_END, rule, observed=raw.notna(), sessions=XNYS)
    assert select_holdings(ranking, raw.ffill(), MONTH_END, rule, observed=raw.notna()).tickers == ["B"]  # rows: 3


def test_backtest_without_gaps_is_the_same_with_or_without_sessions() -> None:
    raw = full_panel()
    config = BacktestConfig(
        top_n=1, candidate_tickers=("X", "B"), momentum=SMALL, benchmark="SPY", cash_ticker="BIL",
        risk_on_benchmark=True, risk_on_tickers=("SPY",),
    )
    with_sessions = run_backtest(raw, config, observed=raw.notna(), sessions=XNYS)
    without = run_backtest(raw, config, observed=raw.notna())
    pd.testing.assert_frame_equal(with_sessions[0], without[0])
    pd.testing.assert_frame_equal(with_sessions[1], without[1])


def test_rank_month_end_market_filter_stale_in_sessions_stops(tmp_path, monkeypatch) -> None:
    # The latest row (10-07) is fresh for X, so only the month-end check can catch it.
    raw = gapped()
    response = raw.reindex(raw.index.union(NO_ROW)).loc[:"2025-10-07"]
    response.columns = pd.MultiIndex.from_product([["Close"], list(raw.columns)])
    monkeypatch.setattr(data.yf, "download", lambda **kw: response)
    universe = tmp_path / "universe.csv"
    pd.DataFrame(
        {"ticker": ["X", "B", "SPY", "BIL"], "name": ["X", "B", "SPY", "BIL"], "category": ["eq", "eq", "eq", "cash"], "enabled": 1}
    ).to_csv(universe, index=False)
    argv = ["etf-momentum", "rank", "--universe", str(universe), "--benchmark", "SPY", "--cash", "BIL", "--top-n", "1",
            "--risk-on-benchmark", "--risk-on-ticker", "X", "--lookback-years", "1", "--end", "2025-10-08",
            "--lookbacks", "21,42,63", "--skip-days", "0", "--sma-window", "20", "--volatility-window", "20",
            "--output-dir", str(tmp_path / "out"), "--cache-dir", str(tmp_path / "cache")]
    monkeypatch.setattr(sys, "argv", argv)
    with contextlib.redirect_stdout(io.StringIO()), pytest.raises(StaleDataError, match="Market filter.*X"):
        cli.main()
