from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd
import pytest

import etf_momentum.backtest as backtest
import etf_momentum.cli as cli
from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.data import UniverseMember
from etf_momentum.portfolio import PortfolioConfig, run_portfolio
from etf_momentum.signals import MomentumConfig


def market(days: int = 700) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-10-06", periods=days)
    steps = np.arange(index.size)
    return pd.DataFrame(
        {"A": 100 * 1.002**steps, "C": 100 * 1.001**steps, "BENCH": 100 * 1.0005**steps, "CASH": 100 * 1.0001**steps,
         "OUTSIDE": 100 * 1.0003**steps},
        index=index,
    )


def strategy(tmp_path, cash_row: str, account: str = "retirement", **extra) -> str:
    universe = tmp_path / "u.csv"
    universe.write_text("ticker,name,category,enabled,group,pension_ok,retirement_ok,risk_asset\n"
                        "A,Alpha,X,1,,1,1,1\nC,Gamma,X,1,,1,1,1\n" + cash_row)
    raw = {
        "id": "s", "version": 1, "description": "t", "universe": str(universe), "account": account, "benchmark": "BENCH",
        "cash": "CASH", "use_proxies": False,
        "signal": {"lookbacks": [63, 126, 252], "weights": [0.3, 0.3, 0.4], "skip_days": 21, "sma_window": 200,
                   "volatility_window": 63, "rank_by": "weighted_momentum", "min_avg_traded_value": None},
        "selection": {"top_n": 2, "max_per_group": 1, "risk_on_benchmark": True, "risk_on_tickers": None},
    }
    for key, value in extra.items():
        (raw["signal"] if key in raw["signal"] else raw["selection"] if key in raw["selection"] else raw)[key] = value
    path = tmp_path / "s.json"
    path.write_text(json.dumps(raw))
    return str(path)


@pytest.fixture
def offline(monkeypatch):
    data = market()
    calls: list[list[str]] = []

    def fake(tickers, **kw):
        calls.append(list(tickers))
        return data[[t for t in tickers if t in data]]

    monkeypatch.setattr(cli, "download_prices", fake)
    monkeypatch.setattr(cli, "previous_month_end", lambda d, today=None: backtest.previous_month_end(d, pd.Timestamp("2026-10-07")))
    return calls


def run(monkeypatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["etf-momentum", *argv])
    cli.main()


# 3. every command validates the cash ETF for the account, rank included, before downloading
@pytest.mark.parametrize(
    "cash_row, account, message",
    [
        ("CASH,Cash,Cash,1,,1,0,1\n", "retirement", "risk_asset=1"),
        ("CASH,Cash,Cash,1,,1,0,0\n", "retirement", "retirement_ok=0"),
        ("CASH,Cash,Cash,0,,1,1,0\n", "retirement", "not an enabled member"),
        ("", "pension", "not an enabled member"),
        ("CASH,Cash,Cash,1,,0,1,0\n", "pension", "pension_ok=0"),
    ],
)
@pytest.mark.parametrize("command", ["rank", "weekly", "backtest"])
def test_cash_etf_is_validated_for_the_account_in_every_command(tmp_path, monkeypatch, offline, cash_row, account, message, command) -> None:
    with pytest.raises(SystemExit, match=message):
        run(monkeypatch, command, "--strategy", strategy(tmp_path, cash_row, account), "--output-dir", str(tmp_path / "o"))
    assert offline == []  # stopped before downloading


# 5. a past --end moves the download window back with it
def test_lookback_start_counts_back_from_a_past_end() -> None:
    assert cli._start_from_lookback_years(3, end="2020-03-01") < "2020-03-01"
    assert cli._start_from_lookback_years(3, end="2020-03-01") == "2017-02-27"  # 3 * 366 days before
    assert cli._start_from_lookback_years(3, end=None) == (pd.Timestamp.today().normalize() - pd.Timedelta(days=1098)).date().isoformat()


# 6. weekly does not use the market filter, so it neither needs nor downloads it
def test_weekly_runs_with_a_market_filter_outside_the_universe(tmp_path, monkeypatch, offline) -> None:
    path = strategy(tmp_path, "CASH,Cash,Cash,1,,1,1,0\n", "retirement", risk_on_tickers=["OUTSIDE"])
    run(monkeypatch, "weekly", "--strategy", path, "--output-dir", str(tmp_path / "o"))
    assert all("OUTSIDE" not in call for call in offline)


# 7. portfolio settings must be finite, long-only weights and sane leverage / rates
@pytest.mark.parametrize(
    "weights, kwargs, field",
    [
        ({"A": 1.0}, {"leverage": float("nan")}, "leverage"),
        ({"A": 1.0}, {"leverage": 0.0}, "leverage"),
        ({"A": -1.0, "B": 2.0}, {}, "weight"),
        ({"A": float("inf"), "B": -float("inf")}, {}, "weight"),
        ({"A": 1.0}, {"borrow_rate": -0.01}, "borrow_rate"),
        ({"A": 1.0}, {"rebalance_cost_bps": float("nan")}, "rebalance_cost_bps"),
    ],
)
def test_portfolio_rejects_unsupported_settings(weights, kwargs, field) -> None:
    returns = pd.DataFrame({"A": [0.0, 0.1], "B": [0.0, 0.0]}, index=pd.bdate_range("2021-01-04", periods=2))
    with pytest.raises(ValueError, match=field):
        run_portfolio(returns, PortfolioConfig(weights=weights, **kwargs))


# 8. hold_until is the last day the position is actually held (the next trade day under next_close)
@pytest.mark.parametrize("execution", ["same_close", "next_close"])
def test_hold_until_is_the_next_trade_date(execution) -> None:
    index = pd.bdate_range("2020-01-01", periods=600)
    prices = pd.DataFrame({"A": 100 * 1.001 ** np.arange(600), "BENCH": 100.0, "CASH": 100.0}, index=index)
    _, positions, _ = run_backtest(prices, BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", candidate_tickers=("A",),
                                                          momentum=MomentumConfig(skip_days=0, sma_window=100), execution=execution))
    assert (pd.to_datetime(positions["hold_until"].iloc[:-1]).values == pd.to_datetime(positions["trade_date"].iloc[1:]).values).all()


# 9. the explanation uses the configured moving-average length
def test_explanation_uses_configured_sma_window(tmp_path, monkeypatch, offline, capsys) -> None:
    path = strategy(tmp_path, "CASH,Cash,Cash,1,,1,1,0\n", "retirement", sma_window=100)
    run(monkeypatch, "rank", "--strategy", path, "--output-dir", str(tmp_path / "o"))
    out = capsys.readouterr().out
    assert "100일선" in out and "200일선" not in out
    assert cli._exclusion_reason(pd.Series({"above_sma": False}), sma_window=100) == "100일선 아래"


def test_portfolio_command_reports_bad_settings_without_a_traceback(tmp_path, monkeypatch) -> None:
    data = market()
    monkeypatch.setattr(cli, "download_prices", lambda tickers, **kw: data[[t for t in tickers if t in data]])
    spec = {"base_currency": "KRW", "calendar_ticker": "A", "cash_ticker": "CASH", "fx": {},
            "sleeves": {"A": {"ticker": "A", "currency": "KRW"}, "C": {"ticker": "C", "currency": "KRW"}},
            "scenarios": [{"name": "short", "weights": {"A": -1.0, "C": 2.0}}]}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    with pytest.raises(SystemExit, match="long only"):
        run(monkeypatch, "portfolio", "--spec", str(tmp_path / "spec.json"), "--output-dir", str(tmp_path / "o"))
