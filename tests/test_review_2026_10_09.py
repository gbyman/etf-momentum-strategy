"""Regression tests for docs/review_2026-10-09_full_codex.md."""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd
import pytest

import etf_momentum.cli as cli
from etf_momentum.portfolio import PortfolioConfig, run_portfolio
from etf_momentum.signals import MomentumConfig, rank_momentum
from etf_momentum.walkforward import walk_forward


def run_rank(tmp_path, monkeypatch, prices: pd.DataFrame, universe: str, *extra: str) -> list[list[str]]:
    calls: list[list[str]] = []

    def fake(tickers, raw=False, **kw):
        calls.append(list(tickers))
        frame = prices[[t for t in tickers if t in prices]]
        return frame if raw else frame.ffill()

    (tmp_path / "u.csv").write_text(universe)
    monkeypatch.setattr(cli, "download_prices", fake)
    monkeypatch.setattr(sys, "argv", [
        "etf-momentum", "rank", "--universe", str(tmp_path / "u.csv"), "--benchmark", "BENCH",
        "--end", "2026-10-07", "--output-dir", str(tmp_path / "out"), *extra,
    ])
    cli.main()
    return calls


def trending_panel(columns: dict[str, float]) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-10-06", periods=700)
    steps = np.arange(700)
    return pd.DataFrame({ticker: 100 * growth**steps for ticker, growth in columns.items()}, index=index)


# F03: rank downloads and checks the cash ETF like backtest does
def test_rank_stops_when_the_cash_etf_has_no_data(tmp_path, monkeypatch) -> None:
    prices = trending_panel({"A": 1.002, "BENCH": 1.0005})
    with pytest.raises(SystemExit, match="cash MISSING"):
        run_rank(tmp_path, monkeypatch, prices, "ticker,name,category,enabled\nA,Alpha,X,1\nBENCH,Bench,X,1\n", "--cash", "MISSING")


def test_rank_requests_a_cash_etf_outside_the_universe(tmp_path, monkeypatch) -> None:
    prices = trending_panel({"A": 1.002, "BENCH": 1.0005, "CASH": 1.0001})
    calls = run_rank(tmp_path, monkeypatch, prices, "ticker,name,category,enabled\nA,Alpha,X,1\nBENCH,Bench,X,1\n", "--cash", "CASH")
    assert "CASH" in calls[0]


# F08: invalid momentum weights / lookbacks are argument errors, not a cash signal
@pytest.mark.parametrize(
    "extra",
    [["--weights", "nan,0,1"], ["--weights", "inf,0,1"], ["--weights", "0,0,0"], ["--weights", "1,1"], ["--lookbacks", "0,126,252"]],
)
def test_invalid_weights_or_lookbacks_are_rejected_before_downloading(extra, monkeypatch) -> None:
    monkeypatch.setattr(cli, "download_prices", lambda *a, **k: pytest.fail("should not download"))
    monkeypatch.setattr(sys, "argv", ["etf-momentum", "rank", *extra])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


# F06: a zero-weight sleeve neither shortens the evaluation nor needs data
def test_zero_weight_sleeve_does_not_change_the_portfolio() -> None:
    index = pd.bdate_range("2024-01-01", periods=6)
    returns = pd.DataFrame({"A": [0, 0.1, 0.1, 0.1, 0.1, 0.1], "B": [np.nan, np.nan, 0, 0, np.nan, np.nan]}, index=index)
    alone, _ = run_portfolio(returns, PortfolioConfig({"A": 1.0}))
    with_zero, _ = run_portfolio(returns, PortfolioConfig({"A": 1.0, "B": 0.0}))
    pd.testing.assert_series_equal(alone, with_zero)
    without_b, _ = run_portfolio(returns[["A"]], PortfolioConfig({"A": 1.0, "B": 0.0}))
    pd.testing.assert_series_equal(alone, without_b)


# F09: missing test returns of the selected config are an error, not 0% returns
def test_walk_forward_rejects_missing_returns_in_the_selected_test_window() -> None:
    index = pd.bdate_range("2020-01-01", "2022-12-30")
    returns = pd.DataFrame({"WIN": 0.002, "OTHER": 0.0001}, index=index)
    returns.loc["2021-01-01":"2021-03-31", "WIN"] = np.nan
    with pytest.raises(ValueError, match="WIN"):
        walk_forward(returns, train_years=1, metric="cagr")


# F10: exclusion reasons name the thresholds actually used
def test_exclusion_reason_names_custom_thresholds() -> None:
    prices = trending_panel({"A": 1.002})
    config = MomentumConfig(min_weighted_momentum=0.5, min_trend_strength=0.3)
    row = rank_momentum(prices, config=config).iloc[0]
    reason = cli._exclusion_reason(row, config)
    assert "모멘텀<50.0%" in reason and "200일선 대비 30.0% 미만" in reason
    assert "모멘텀≤0" not in reason


def test_exclusion_reason_keeps_default_wording() -> None:
    row = pd.Series({"positive_momentum": False, "above_sma": False, "price_fresh": True})
    assert cli._exclusion_reason(row, MomentumConfig(sma_window=120)) == "모멘텀≤0, 120일선 아래"


# F05: a held asset (cash included) whose data stops mid-holding is a data error, not 0% returns
from etf_momentum.backtest import BacktestConfig, StaleDataError, run_backtest  # noqa: E402


def cash_only_backtest(observed_gap: slice | None) -> tuple:
    index = pd.bdate_range(end="2026-10-06", periods=700)
    steps = np.arange(700)
    prices = pd.DataFrame({"BENCH": 100 * 1.0005**steps, "CASH": 100 * 1.0001**steps}, index=index)
    observed = prices.notna()
    if observed_gap is not None:
        observed.loc[index[observed_gap], "CASH"] = False
    config = BacktestConfig(top_n=1, benchmark="BENCH", cash_ticker="CASH", candidate_tickers=("NOPE",))
    return run_backtest(prices, config, observed=observed)


def test_held_cash_gap_mid_backtest_is_an_error() -> None:
    with pytest.raises(StaleDataError, match="CASH"):
        cash_only_backtest(slice(400, 501))


def test_short_holiday_gap_in_held_cash_is_fine() -> None:
    equity, _, _ = cash_only_backtest(slice(400, 403))  # 3 rows without a close
    assert equity["strategy"].iloc[-1] > 1


def test_cash_before_its_first_observation_is_uninvested_cash() -> None:
    equity, _, _ = cash_only_backtest(slice(0, 450))  # cash ETF listed later: held as 0% cash until then
    assert equity["strategy"].iloc[-1] > 1


# F02: proxy-filled rows are observed only when the proxy (and FX) really had new closes
from etf_momentum.data import proxy_observations, splice_proxies  # noqa: E402


def test_proxy_that_stopped_updating_is_not_observed() -> None:
    local = pd.bdate_range("2018-01-01", "2020-03-02")
    raw = pd.DataFrame({"LOCAL": np.nan}, index=local)
    raw.iloc[-1, 0] = 200.0
    us = pd.bdate_range("2017-12-29", "2020-01-15")  # the proxy and FX stop 44 days before listing
    proxy = pd.Series(100 * np.exp(np.arange(len(us)) * 0.001), index=us)
    fx = pd.Series(1000.0, index=us)
    filled, starts = splice_proxies(raw, {"LOCAL": proxy}, fx)
    seen = proxy_observations(proxy, fx, local[local < starts["LOCAL"]])
    row = rank_momentum(filled, pd.Timestamp("2020-02-28"), observed=seen.to_frame("LOCAL").reindex(local, fill_value=True)).iloc[0]
    assert not row["price_fresh"] and not row["eligible"]
    assert pd.Timestamp(row["last_observed"]) == pd.Timestamp("2020-01-16")  # the first local day after the last US close


def test_proxy_observed_each_day_with_us_holidays() -> None:
    local = pd.bdate_range("2019-01-01", "2019-12-31")
    us = pd.bdate_range("2018-12-01", "2019-12-31").drop(pd.to_datetime(["2019-07-04", "2019-11-28"]))
    proxy = pd.Series(100.0, index=us)
    fx = pd.Series(1000.0, index=pd.bdate_range("2018-12-01", "2019-12-31"))
    seen = proxy_observations(proxy, fx, local)
    assert seen.sum() == len(local) - 2  # only the days after the two US holidays bring no new close


# F07: the curve starts at the 1.0 invested, so the first purchase cost is part of the measured return
def test_initial_cost_stays_in_the_curve() -> None:
    index = pd.bdate_range("2024-01-01", periods=2)
    equity, history = run_portfolio(pd.DataFrame({"A": [0.0, 0.0]}, index=index), PortfolioConfig({"A": 1.0}, rebalance_cost_bps=100))
    assert equity.tolist() == pytest.approx([1.0, 0.99])
    assert history["cost"].tolist() == pytest.approx([0.01])


# F01/F04: one selection run uses one market's tickers; mixing Korean and US closes on one date
# index looks ahead (US close of the same date) and changes lookback windows (extra rows).
@pytest.mark.parametrize(
    "extra",
    [
        ["--benchmark", "SPY"],  # Korean universe, forgotten --benchmark default
        ["--risk-on-benchmark", "--risk-on-ticker", "SPY"],
        ["--cash", "BIL"],
    ],
)
def test_mixed_market_selection_stops_before_downloading(extra, tmp_path, monkeypatch) -> None:
    (tmp_path / "u.csv").write_text("ticker,name,category,enabled\n069500.KS,K200,X,1\n091160.KS,Semi,X,1\n153130.KS,Cash,Cash,1\n")
    monkeypatch.setattr(cli, "download_prices", lambda *a, **k: pytest.fail("should not download"))
    base = ["--universe", str(tmp_path / "u.csv"), "--benchmark", "069500.KS", "--cash", "153130.KS"]
    monkeypatch.setattr(sys, "argv", ["etf-momentum", "rank", *base, *extra, "--output-dir", str(tmp_path / "o")])
    with pytest.raises(SystemExit, match="SPY|BIL"):
        cli.main()


def test_market_of_a_ticker() -> None:
    assert cli._market("069500.KS") == cli._market("229200.KQ") == "KR"
    assert cli._market("SPY") == cli._market("BIL") == "US"
