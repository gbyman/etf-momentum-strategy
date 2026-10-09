"""Regression tests for docs/review_2026-10-09_recheck_codex.md."""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd
import pytest

import etf_momentum.cli as cli
from etf_momentum.data import account_candidates, load_universe, proxy_observations, splice_proxies
from etf_momentum.records import record_signal
from etf_momentum.signals import MomentumConfig, rank_momentum


# R01: proxy-filled rows are fresh for at most PRICE_MAX_STALE_DAYS rows after the FX rate's last value
def fx_outage_ranking(fx_days: int, as_of_pos: int) -> pd.Series:
    local = pd.bdate_range("2020-01-02", periods=30)
    us = pd.bdate_range("2020-01-01", periods=31)
    proxy = pd.Series(100 * 1.01 ** np.arange(len(us)), index=us)  # keeps updating
    fx = pd.Series(1000.0, index=us[:fx_days])  # stops
    raw = pd.DataFrame({"A": np.nan}, index=local)
    raw.iloc[-1, 0] = 200.0
    prices, starts = splice_proxies(raw, {"A": proxy}, fx)
    seen = proxy_observations(proxy, fx, local[local < starts["A"]])
    observed = cli._observations(raw, argparse.Namespace(proxy_observed={"A": seen}), prices)
    config = MomentumConfig(lookbacks=(1, 2, 3), skip_days=0, sma_window=2, volatility_window=2)
    return rank_momentum(prices, local[as_of_pos], config=config, observed=observed).iloc[0]


@pytest.mark.parametrize("rows_without_fx, fresh", [(5, True), (6, False), (10, False)])
def test_fx_outage_allows_five_rows_only(rows_without_fx, fresh) -> None:
    # FX's last value (us[13] = 2020-01-20) first reaches the local row 2020-01-21 (local[13])
    row = fx_outage_ranking(fx_days=14, as_of_pos=13 + rows_without_fx)
    assert bool(row["price_fresh"]) is fresh


def test_us_and_fx_holidays_on_different_days_stay_fresh() -> None:
    local = pd.bdate_range("2019-01-01", "2019-12-31")
    us = pd.bdate_range("2018-12-01", "2019-12-31").drop(pd.to_datetime(["2019-07-04", "2019-11-28"]))
    fx_days = pd.bdate_range("2018-12-01", "2019-12-31").drop(pd.to_datetime(["2019-05-01", "2019-10-03"]))
    seen = proxy_observations(pd.Series(100.0, index=us), pd.Series(1000.0, index=fx_days), local)
    longest_gap = (~seen).astype(int).groupby(seen.cumsum()).sum().max()
    assert seen.iloc[0] and longest_gap == 1


# R02: never append to a signal log whose last row is cut off
def selection(ticker: str = "AAA") -> pd.DataFrame:
    return pd.DataFrame([{"status": "selected", "rank_in_selection": 1, "ticker": ticker, "name": "", "group": "", "slots": 1, "market_filter": "on"}])


def meta(date: str) -> dict:
    return {"signal_date": date, "recorded_at": "2026-10-09T12:00:00", "strategy_id": "s", "version": 1, "config_hash": "h",
            "code_commit": "abc", "code_dirty": False, "price_last_date": "2026-10-07"}


def test_truncated_signal_log_is_an_error_and_left_unchanged(tmp_path) -> None:
    path = tmp_path / "signals.csv"
    record_signal(path, selection(), meta("2026-08-31"))
    path.write_bytes(path.read_bytes()[:-3])  # the last field and newline are gone
    before = path.read_bytes()
    with pytest.raises(ValueError, match="incomplete|fields"):
        record_signal(path, selection(), meta("2026-09-30"))
    assert path.read_bytes() == before


def test_complete_log_without_final_newline_gets_a_separate_row(tmp_path) -> None:
    path = tmp_path / "signals.csv"
    record_signal(path, selection(), meta("2026-08-31"))
    path.write_bytes(path.read_bytes().rstrip(b"\r\n"))
    assert record_signal(path, selection(), meta("2026-09-30")).action == "added"
    log = pd.read_csv(path, dtype=str)
    assert log["signal_date"].tolist() == ["2026-08-31", "2026-09-30"] and (log["slots"] == "1").all()


def test_unterminated_quote_is_an_error(tmp_path) -> None:
    path = tmp_path / "signals.csv"
    record_signal(path, selection(), meta("2026-08-31"))
    path.write_bytes(path.read_bytes() + b'2026-09-30,"cut')
    with pytest.raises(ValueError):
        record_signal(path, selection(), meta("2026-10-30"))


# R03: a sleeve no scenario uses (weight 0 everywhere) is not loaded or checked
def test_sleeve_unused_by_every_scenario_does_not_block_the_portfolio(tmp_path, monkeypatch) -> None:
    calendar = pd.bdate_range("2024-01-02", periods=4)
    raw = pd.DataFrame({"GOOD": [100.0, 101.0, 102.0, 103.0], "CAL": 100.0, "UNUSED": [100.0, np.nan, 101.0, 102.0]}, index=calendar)

    def download(tickers, raw_frame=raw, raw=False, **kw):
        frame = raw_frame[list(tickers)].dropna(how="all")
        return frame if raw else frame.ffill()

    monkeypatch.setattr(cli, "download_prices", download)

    def run(with_unused: bool) -> pd.DataFrame:
        spec = {"base_currency": "KRW", "calendar_ticker": "CAL", "start": "2024-01-02", "end": "2024-01-06",
                "sleeves": {"good": {"ticker": "GOOD", "currency": "KRW"}}, "scenarios": [{"name": "g", "weights": {"good": 1.0}}]}
        if with_unused:
            spec["sleeves"]["unused"] = {"ticker": "UNUSED", "currency": "KRW"}
            spec["sleeves"]["no_file"] = {"equity_csv": str(tmp_path / "missing.csv"), "currency": "KRW"}
            spec["scenarios"][0]["weights"]["unused"] = 0.0
        out = tmp_path / f"o{with_unused}"
        (tmp_path / "spec.json").write_text(json.dumps(spec))
        monkeypatch.setattr(sys, "argv", ["etf-momentum", "portfolio", "--spec", str(tmp_path / "spec.json"), "--output-dir", str(out)])
        cli.main()
        return pd.read_csv(out / "portfolio_equity.csv", index_col=0)

    pd.testing.assert_frame_equal(run(False), run(True))


def test_used_sleeve_with_a_gap_still_stops(tmp_path, monkeypatch) -> None:
    calendar = pd.bdate_range("2024-01-02", periods=4)
    closes = pd.DataFrame({"GOOD": [100.0, np.nan, 102.0, 103.0], "CAL": 100.0}, index=calendar)
    monkeypatch.setattr(cli, "download_prices", lambda tickers, raw=False, **kw: closes[list(tickers)] if raw else closes[list(tickers)].ffill())
    spec = {"base_currency": "KRW", "calendar_ticker": "CAL", "sleeves": {"good": {"ticker": "GOOD", "currency": "KRW"}},
            "scenarios": [{"name": "g", "weights": {"good": 1.0}}]}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    monkeypatch.setattr(sys, "argv", ["etf-momentum", "portfolio", "--spec", str(tmp_path / "spec.json"), "--output-dir", str(tmp_path / "o")])
    with pytest.raises(SystemExit, match="good"):
        cli.main()


# R04: a ticker listed twice is an input error, and selection never counts one ETF twice
def test_duplicate_enabled_ticker_is_rejected_with_rows(tmp_path) -> None:
    path = tmp_path / "u.csv"
    path.write_text("ticker,name,category,enabled\nA,Alpha,X,1\nB,Beta,X,1\n a ,Alpha again,X,1\nCASH,Cash,Cash,1\n")
    with pytest.raises(ValueError, match=r"A.*rows 2, 4"):
        load_universe(path)


def test_disabled_duplicate_is_allowed(tmp_path) -> None:
    path = tmp_path / "u.csv"
    path.write_text("ticker,name,category,enabled\nA,Alpha,X,1\nA,Alpha old,X,0\n")
    assert [m.ticker for m in load_universe(path)] == ["A"]


def test_rank_momentum_ranks_a_repeated_candidate_once() -> None:
    index = pd.bdate_range("2020-01-01", periods=300)
    prices = pd.DataFrame({"A": 100 * 1.002 ** np.arange(300), "B": 100 * 1.001 ** np.arange(300)}, index=index)
    ranking = rank_momentum(prices, candidate_tickers=("A", "B", "A"))
    assert ranking["ticker"].tolist() == ["A", "B"]
