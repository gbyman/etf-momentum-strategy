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
