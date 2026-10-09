from __future__ import annotations

import json
import sys

import numpy as np
import pandas as pd
import pytest

import etf_momentum.cli as cli
from etf_momentum.backtest import BacktestConfig, select_holdings
from etf_momentum.signals import MomentumConfig, rank_momentum
from etf_momentum.walkforward import walk_forward


DAYS = pd.bdate_range("2024-01-01", periods=350)


def trending(**extra: np.ndarray) -> pd.DataFrame:
    steps = np.arange(DAYS.size)
    return pd.DataFrame({"A": 100 * 1.001**steps, "BENCH": 100 * 1.0005**steps, **extra}, index=DAYS)


ELIGIBLE_A = pd.DataFrame({"ticker": ["A"], "eligible": [True]})


# 1. a market filter that can't be evaluated is an error, never "risk on"
def test_missing_market_filter_ticker_is_an_error() -> None:
    with pytest.raises(ValueError, match="MISSING"):
        select_holdings(ELIGIBLE_A, trending(), DAYS[-1], BacktestConfig(top_n=1, benchmark="MISSING", risk_on_benchmark=True))


def test_partly_missing_market_filters_are_an_error() -> None:
    config = BacktestConfig(top_n=1, benchmark="BENCH", risk_on_benchmark=True, risk_on_tickers=("BENCH", "GONE"))
    with pytest.raises(ValueError, match="GONE"):
        select_holdings(ELIGIBLE_A, trending(), DAYS[-1], config)


def test_cli_stops_when_required_ticker_has_no_data() -> None:
    prices = trending()
    with pytest.raises(SystemExit, match="069500.KS"):
        cli._require_tickers(prices, {"benchmark": "069500.KS", "cash": "BENCH"})
    cli._require_tickers(prices, {"benchmark": "BENCH", "cash": None})  # present -> no error


# 2. a candidate whose price has not changed for more than 5 trading days is not eligible
def test_stale_forward_filled_price_is_not_eligible() -> None:
    prices = trending()
    prices.loc[DAYS[-60]:, "A"] = np.nan
    prices = prices.ffill()  # what the cache cleaning does
    row = rank_momentum(prices, benchmark="BENCH", candidate_tickers=("A",)).iloc[0]
    assert not row["price_fresh"] and not row["eligible"]
    assert pd.Timestamp(row["last_price_change"]) == DAYS[-61]


def test_short_unchanged_run_is_still_fresh() -> None:
    prices = trending()
    prices.loc[DAYS[-5]:, "A"] = prices.loc[DAYS[-6], "A"]  # 5 unchanged days: allowed
    row = rank_momentum(prices, benchmark="BENCH", candidate_tickers=("A",)).iloc[0]
    assert row["price_fresh"] and row["eligible"]


# 5. portfolio --refresh reaches every series the evaluation uses
def test_portfolio_refresh_reaches_single_ticker_downloads(tmp_path, monkeypatch) -> None:
    index = pd.bdate_range("2023-01-02", periods=300)
    data = pd.DataFrame({"KR": np.linspace(100, 130, 300), "US": np.linspace(50, 60, 300), "FX": 1300.0, "CASH": 100.0}, index=index)
    calls: list[bool] = []

    def fake_download(tickers, start=None, end=None, cache_dir="data", refresh=False, **kw):
        calls.append(refresh)
        return data[list(tickers)]

    spec = {
        "base_currency": "KRW", "calendar_ticker": "KR", "cash_ticker": "CASH", "fx": {"USD": "FX"},
        "sleeves": {"KR": {"ticker": "KR", "currency": "KRW"}, "US": {"ticker": "US", "currency": "USD"}},
        "scenarios": [{"name": "mix", "weights": {"KR": 0.5, "US": 0.5}}],
    }
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    monkeypatch.setattr(cli, "download_prices", fake_download)
    monkeypatch.setattr(sys, "argv", ["etf-momentum", "portfolio", "--spec", str(tmp_path / "spec.json"),
                                      "--output-dir", str(tmp_path / "out"), "--refresh"])
    cli.main()
    assert len(calls) >= 4 and all(calls)


# 9. invalid settings are rejected up front
def test_top_n_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="top_n"):
        select_holdings(ELIGIBLE_A, trending(), DAYS[-1], BacktestConfig(top_n=0, benchmark="BENCH"))


@pytest.mark.parametrize("kwargs", [{"test_months": 0}, {"train_years": 0}, {"test_months": -1}])
def test_walk_forward_rejects_non_positive_windows(kwargs) -> None:
    returns = pd.DataFrame({"A": 0.001}, index=pd.bdate_range("2010-01-01", "2016-12-31"))
    with pytest.raises(ValueError, match="positive"):
        walk_forward(returns, **kwargs)


@pytest.mark.parametrize(
    "argv",
    [
        ["rank", "--top-n", "0"],
        ["backtest", "--transaction-cost-bps", "-5"],
        ["backtest", "--target-vol", "0"],
        ["backtest", "--max-position-weight", "1.5"],
        ["walkforward", "--test-months", "0"],
        ["rank", "--min-avg-traded-value", "nan"],
    ],
)
def test_cli_rejects_out_of_range_arguments_before_downloading(argv, monkeypatch) -> None:
    def no_download(*args, **kwargs):
        raise AssertionError("should not download with invalid arguments")

    monkeypatch.setattr(cli, "download_prices", no_download)
    monkeypatch.setattr(sys, "argv", ["etf-momentum", *argv])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2  # argparse usage error


# 3. the previous month must be the month before "today" and its month-end must be in the data
from etf_momentum.backtest import previous_month_end  # noqa: E402


def test_month_cut_off_mid_month_is_an_error() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        previous_month_end(pd.bdate_range("2026-09-01", "2026-09-15"), today=pd.Timestamp("2026-10-07"))


def test_missing_previous_month_does_not_fall_back_to_an_older_month() -> None:
    with pytest.raises(ValueError, match="2026-09"):
        previous_month_end(pd.bdate_range("2026-08-03", "2026-08-31"), today=pd.Timestamp("2026-10-07"))


def test_month_ending_on_holiday_is_complete_when_next_month_has_data() -> None:
    dates = pd.to_datetime(["2026-12-29", "2026-12-30", "2027-01-04"])  # 12-31 (Thu) closed
    assert previous_month_end(dates, today=pd.Timestamp("2027-01-08")) == pd.Timestamp("2026-12-30")
    with pytest.raises(ValueError, match="incomplete"):  # holiday month-end, next month not traded yet
        previous_month_end(dates[:2], today=pd.Timestamp("2027-01-01"))


def test_month_ending_friday_is_complete_before_next_month_trades() -> None:
    dates = pd.bdate_range("2026-07-01", "2026-07-31")  # 07-31 is a Friday; run on Saturday 08-01
    assert previous_month_end(dates, today=pd.Timestamp("2026-08-01")) == pd.Timestamp("2026-07-31")


def test_rank_uses_end_date_as_today_for_month_end() -> None:
    import argparse

    assert cli._selection_today(argparse.Namespace(end="2025-03-01")) == pd.Timestamp("2025-03-01")
    assert cli._selection_today(argparse.Namespace(end=None)) is None


# found while verifying: a partly failed download must not be cached or crash the proxy step
import etf_momentum.data as data  # noqa: E402


def test_partly_failed_download_warns_and_is_not_cached(tmp_path, monkeypatch) -> None:
    index = pd.to_datetime(["2020-01-02", "2020-01-03"])
    returned = pd.DataFrame(
        [[1.0, np.nan], [2.0, np.nan]], index=index, columns=pd.MultiIndex.from_product([["Close"], ["AAA", "BBB"]])
    )
    monkeypatch.setattr(data.yf, "download", lambda **kwargs: returned)
    with pytest.warns(UserWarning, match="BBB"):
        prices = data.download_prices(["AAA", "BBB"], start="2020-01-01", end="2020-01-04", cache_dir=tmp_path)
    assert list(prices.columns) == ["AAA"]
    assert not list(tmp_path.glob("*.csv"))  # next run retries instead of reusing a partial cache


def test_missing_proxy_is_skipped_with_a_message(monkeypatch, capsys) -> None:
    import argparse

    from etf_momentum.data import UniverseMember

    local = pd.DataFrame({"K1": [np.nan, 10.0, 11.0], "K2": [np.nan, 20.0, 21.0]}, index=pd.bdate_range("2021-01-04", periods=3))
    proxies = pd.DataFrame({"P1": [1.0, 1.1, 1.2], "KRW=X": 1000.0}, index=local.index)  # P2 failed to download
    monkeypatch.setattr(cli, "download_prices", lambda tickers, **kw: proxies)
    members = [UniverseMember("K1", "k1", "x", proxy="P1"), UniverseMember("K2", "k2", "x", proxy="P2")]
    args = argparse.Namespace(use_proxies=True, proxy_fx="KRW=X", end=None, cache_dir="data", refresh=False)
    spliced, _ = cli._apply_proxies(members, local, None, None, args)
    assert np.isnan(spliced.loc[local.index[0], "K2"])  # no proxy history, but no crash
    assert "P2" in capsys.readouterr().out


def test_cache_missing_a_requested_ticker_is_downloaded_again(tmp_path, monkeypatch) -> None:
    index = pd.to_datetime(["2020-01-02", "2020-01-03"])
    cache = data._cache_path(["AAA", "BBB"], "2020-01-01", "2020-01-04", tmp_path, prefix=data.PRICE_CACHE_PREFIX)
    pd.DataFrame({"AAA": [1.0, 2.0]}, index=index).to_csv(cache)  # an old partial cache
    full = pd.DataFrame([[1.0, 5.0], [2.0, 6.0]], index=index, columns=pd.MultiIndex.from_product([["Close"], ["AAA", "BBB"]]))
    calls = []
    monkeypatch.setattr(data.yf, "download", lambda **kwargs: calls.append(1) or full)
    prices = data.download_prices(["AAA", "BBB"], start="2020-01-01", end="2020-01-04", cache_dir=tmp_path)
    assert calls and list(prices.columns) == ["AAA", "BBB"]
