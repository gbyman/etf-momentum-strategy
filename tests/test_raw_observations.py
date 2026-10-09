from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import etf_momentum.data as data


def raw_download(close: list[float]):
    index = pd.bdate_range("2021-01-04", periods=len(close))
    frame = pd.DataFrame({"Close": close}, index=index)
    return lambda **kwargs: frame


# Task 1: the cache keeps raw closes; callers choose filled (default) or raw.
# Y trades every day, so X's missing day is a row of its own (an all-empty row is dropped).
def test_raw_gap_survives_in_cache_and_raw_mode(tmp_path, monkeypatch) -> None:
    index = pd.bdate_range("2021-01-04", periods=4)
    columns = pd.MultiIndex.from_product([["Close"], ["X", "Y"]])
    frame = pd.DataFrame([[100.0, 1.0], [np.nan, 2.0], [110.0, 3.0], [111.0, 4.0]], index=index, columns=columns)
    monkeypatch.setattr(data.yf, "download", lambda **kwargs: frame)
    filled = data.download_prices(["X", "Y"], start="2021-01-01", end="2021-01-09", cache_dir=tmp_path)
    raw = data.download_prices(["X", "Y"], start="2021-01-01", end="2021-01-09", cache_dir=tmp_path, raw=True)
    assert filled["X"].tolist() == [100.0, 100.0, 110.0, 111.0]
    assert np.isnan(raw["X"].iloc[1]) and raw["X"].iloc[2] == 110.0
    cache = data._cache_path(["X", "Y"], "2021-01-01", "2021-01-09", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    assert cache.exists() and pd.read_csv(cache, index_col=0)["X"].isna().sum() == 1


def test_old_filled_price_cache_is_not_reused(tmp_path, monkeypatch) -> None:
    old = data._cache_path(["X"], "2021-01-01", "2021-01-09", tmp_path, prefix="prices")
    pd.DataFrame({"X": [1.0]}, index=pd.to_datetime(["2021-01-04"])).to_csv(old)
    monkeypatch.setattr(data.yf, "download", raw_download([100.0, 101.0]))
    prices = data.download_prices(["X"], start="2021-01-01", end="2021-01-09", cache_dir=tmp_path)
    assert prices["X"].tolist() == [100.0, 101.0]
    assert old.exists()  # left alone


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


def test_proxy_filled_history_counts_as_observed() -> None:
    index = pd.bdate_range("2026-01-01", periods=10)
    raw = pd.DataFrame({"K": [np.nan] * 5 + [10.0] * 5, "BENCH": 1.0}, index=index)
    prices = raw.copy()
    prices.loc[index[:5], "K"] = 9.0  # spliced from a proxy
    args = argparse.Namespace(proxy_starts={"K": index[5]})
    observed = cli._observations(raw, args, prices)
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


# Final review fixes
from etf_momentum.backtest import StaleDataError  # noqa: E402


def test_observations_reuse_the_first_download_under_refresh(tmp_path, monkeypatch) -> None:
    index = pd.bdate_range("2026-01-01", periods=10)
    both = pd.DataFrame(1.0, index=index, columns=pd.MultiIndex.from_product([["Close"], ["BENCH", "CASH"]]))
    bench_only = both.drop(columns=("Close", "CASH"))
    calls = []

    def fake(**kw):
        calls.append(1)
        return both if len(calls) == 1 else bench_only  # a second download would lose CASH

    monkeypatch.setattr(data.yf, "download", fake)
    args = argparse.Namespace(end=None, cache_dir=tmp_path, refresh=True, proxy_starts={})
    prices, raw = cli._download(["BENCH", "CASH"], None, args)
    observed = cli._observations(raw, args, prices)
    assert len(calls) == 1 and observed["CASH"].all()


def test_stale_or_missing_filter_raises_stale_data_error() -> None:
    prices = trending(650)
    observed = prices.notna()
    observed.loc[prices.index[-60]:, "BENCH"] = False
    rule = BacktestConfig(top_n=1, benchmark="BENCH", risk_on_benchmark=True)
    with pytest.raises(StaleDataError):
        select_holdings(ELIGIBLE_A, prices, prices.index[-1], rule, observed=observed)
    with pytest.raises(StaleDataError):
        select_holdings(ELIGIBLE_A, prices[["A"]], prices.index[-1], rule)


def test_walkforward_stops_on_a_mid_history_filter_gap(tmp_path, monkeypatch) -> None:
    import sys

    index = pd.bdate_range("2012-01-02", "2020-12-31")
    steps = np.arange(len(index))
    prices = pd.DataFrame({"A": 100 * 1.0004**steps, "B": 100 * 1.0003**steps, "BENCH": 100 * 1.0002**steps, "CASH": 100.0}, index=index)
    raw = prices.copy()
    raw.loc["2016-03-01":"2016-04-15", "BENCH"] = np.nan  # a past gap; the last row is fresh
    monkeypatch.setattr(
        cli, "download_prices", lambda tickers, raw=False, **kw: (lambda f: f if raw else f.ffill())(raw_prices[[t for t in tickers if t in raw_prices]])
    )
    raw_prices = raw
    universe = tmp_path / "u.csv"
    universe.write_text("ticker,name,category,enabled\nA,A,X,1\nB,B,X,1\nCASH,Cash,Cash,1\n")
    monkeypatch.setattr(sys, "argv", [
        "etf-momentum", "walkforward", "--universe", str(universe), "--benchmark", "BENCH", "--cash", "CASH",
        "--top-n-grid", "1", "--weighting-grid", "equal", "--max-per-group-grid", "0", "--risk-on-grid", "on",
        "--relative-strength-grid", "off", "--output-dir", str(tmp_path / "wf"),
    ])
    with pytest.raises(SystemExit, match="BENCH"):
        cli.main()


def test_month_end_missing_from_every_ticker_hints_at_an_outdated_calendar(monkeypatch) -> None:
    sessions = pd.bdate_range("2026-08-01", "2026-10-31")  # the calendar thinks 09-30 is a session
    monkeypatch.setattr(cli, "_month_end_sessions", lambda benchmark, today: sessions)
    index = sessions[sessions <= "2026-10-07"].drop(pd.Timestamp("2026-09-30"))  # nobody traded 09-30
    prices = pd.DataFrame({"BENCH": 1.0}, index=index)
    args = argparse.Namespace(benchmark="BENCH", selection_today=None)
    monkeypatch.setattr(cli, "_selection_today", lambda a: pd.Timestamp("2026-10-08"))
    with pytest.raises(SystemExit, match="exchange-calendars"):
        cli._month_end_date(prices, prices.notna(), args)


def test_month_end_missing_only_for_the_benchmark_asks_for_a_refresh(monkeypatch) -> None:
    sessions = pd.bdate_range("2026-08-01", "2026-10-31")
    monkeypatch.setattr(cli, "_month_end_sessions", lambda benchmark, today: sessions)
    monkeypatch.setattr(cli, "_selection_today", lambda a: pd.Timestamp("2026-10-08"))
    index = sessions[sessions <= "2026-10-07"]
    prices = pd.DataFrame({"BENCH": 1.0, "A": 1.0}, index=index)
    observed = prices.notna()
    observed.loc["2026-09-30", "BENCH"] = False
    with pytest.raises(SystemExit, match="--refresh") as exc:
        cli._month_end_date(prices, observed, argparse.Namespace(benchmark="BENCH"))
    assert "exchange-calendars" not in str(exc.value)
    observed.loc["2026-09-30", "BENCH"] = True
    assert cli._month_end_date(prices, observed, argparse.Namespace(benchmark="BENCH")) == pd.Timestamp("2026-09-30")
