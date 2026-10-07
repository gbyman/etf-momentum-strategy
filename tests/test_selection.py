from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import BacktestConfig, previous_month_end, run_backtest, select_holdings
from etf_momentum.signals import MomentumConfig, rank_momentum


# --- previous month-end close -------------------------------------------------------------

def test_previous_month_end_is_last_trading_day_of_last_completed_month() -> None:
    dates = pd.to_datetime(["2026-09-29", "2026-09-30", "2026-10-01", "2026-10-06"])
    assert previous_month_end(dates, today=pd.Timestamp("2026-10-07")) == pd.Timestamp("2026-09-30")


def test_month_in_progress_is_not_used_even_on_its_last_day() -> None:
    dates = pd.to_datetime(["2026-09-30", "2026-10-29", "2026-10-30"])
    assert previous_month_end(dates, today=pd.Timestamp("2026-10-31")) == pd.Timestamp("2026-09-30")


def test_year_end_closure_uses_last_trading_day_in_data() -> None:
    dates = pd.to_datetime(["2026-12-29", "2026-12-30", "2027-01-04"])  # Korea closed 12-31
    assert previous_month_end(dates, today=pd.Timestamp("2027-01-05")) == pd.Timestamp("2026-12-30")


def test_no_completed_month_is_an_error() -> None:
    with pytest.raises(ValueError, match="month"):
        previous_month_end(pd.to_datetime(["2026-10-01", "2026-10-02"]), today=pd.Timestamp("2026-10-05"))


# --- holdings selection -----------------------------------------------------------------

def ranking(*rows: tuple[str, bool]) -> pd.DataFrame:
    return pd.DataFrame([{"ticker": t, "eligible": e} for t, e in rows])


def bench_prices(trending_up: bool) -> pd.DataFrame:
    index = pd.bdate_range("2025-01-01", periods=300)
    path = np.linspace(100, 150, 300) if trending_up else np.linspace(150, 100, 300)
    return pd.DataFrame({"BENCH": path}, index=index)


def config(**overrides) -> BacktestConfig:
    base = dict(top_n=2, cash_ticker="CASH", benchmark="BENCH", max_per_group=1, risk_on_benchmark=True,
                groups={"A": "g1", "B": "g1"}, momentum=MomentumConfig(sma_window=200))
    base.update(overrides)
    return BacktestConfig(**base)


def test_group_limit_skips_second_member_and_takes_next_eligible() -> None:
    prices = bench_prices(trending_up=True)
    picked = select_holdings(ranking(("A", True), ("B", True), ("C", True)), prices, prices.index[-1], config())
    assert picked.risk_on is True
    assert picked.tickers == ["A", "C"]
    assert picked.group_skipped == ["B"]
    assert picked.empty_slots == 0


def test_too_few_eligible_leaves_empty_slots() -> None:
    prices = bench_prices(trending_up=True)
    picked = select_holdings(ranking(("A", True), ("X", False)), prices, prices.index[-1], config(top_n=3))
    assert picked.tickers == ["A"]
    assert picked.empty_slots == 2


def test_market_filter_off_selects_nothing() -> None:
    prices = bench_prices(trending_up=False)
    picked = select_holdings(ranking(("A", True), ("C", True)), prices, prices.index[-1], config())
    assert picked.risk_on is False
    assert picked.tickers == []
    assert picked.empty_slots == 2


def test_backtest_holds_exactly_what_select_holdings_picks() -> None:
    index = pd.bdate_range("2020-01-01", periods=700)
    steps = np.arange(index.size)
    prices = pd.DataFrame(
        {
            "A": 100 * np.power(1.0020, steps),
            "B": 100 * np.power(1.0018, steps),
            "C": 100 * np.power(1.0010, steps),
            "BENCH": 100 * np.power(1.0005, steps),
            "CASH": 100.0,
        },
        index=index,
    )
    cfg = config(momentum=MomentumConfig(skip_days=0, sma_window=100), candidate_tickers=("A", "B", "C"))
    _, positions, _ = run_backtest(prices, cfg)
    first = pd.Timestamp(positions["rebalance_date"].iloc[0])
    ranked = rank_momentum(prices, first, benchmark="BENCH", config=cfg.momentum, candidate_tickers=cfg.candidate_tickers)
    assert positions["tickers"].iloc[0].split(",") == sorted(select_holdings(ranked, prices, first, cfg).tickers, key=list(prices.columns).index)
    assert select_holdings(ranked, prices, first, cfg).tickers == ["A", "C"]  # B shares A's group


# --- rank command output ----------------------------------------------------------------

def test_rank_command_writes_previous_month_end_selection(tmp_path, monkeypatch) -> None:
    import sys

    import etf_momentum.backtest as backtest
    import etf_momentum.cli as cli

    index = pd.bdate_range(end="2026-10-06", periods=700)
    steps = np.arange(index.size)
    prices = pd.DataFrame(
        {
            "A": 100 * np.power(1.0020, steps),
            "B": 100 * np.power(1.0018, steps),
            "C": 100 * np.power(1.0010, steps),
            "D": np.linspace(100, 80, index.size),  # downtrend: not eligible
            "BENCH": 100 * np.power(1.0005, steps),
            "CASH": 100 * np.power(1.0001, steps),
        },
        index=index,
    )
    prices.loc["2026-10-01":, "C"] = prices.loc["2026-10-01":, "C"] * 3  # an October jump must not affect the September pick
    universe = tmp_path / "u.csv"
    universe.write_text(
        "ticker,name,category,enabled,group\nA,Alpha,X,1,g1\nB,Beta,X,1,g1\nC,Gamma,X,1,\nD,Delta,X,1,\nCASH,Cash,Cash,1,\n"
    )
    monkeypatch.setattr(cli, "download_prices", lambda tickers, **kw: prices[[t for t in tickers if t in prices]])
    monkeypatch.setattr(
        cli, "previous_month_end", lambda dates, today=None: backtest.previous_month_end(dates, pd.Timestamp("2026-10-07"))
    )
    monkeypatch.setattr(
        sys, "argv",
        ["etf-momentum", "rank", "--universe", str(universe), "--benchmark", "BENCH", "--cash", "CASH",
         "--top-n", "3", "--max-per-group", "1", "--risk-on-benchmark", "--output-dir", str(tmp_path / "out")],
    )
    cli.main()

    selection = pd.read_csv(tmp_path / "out" / "latest_selection.csv")
    assert set(selection["as_of"]) == {"2026-09-30"}
    picked = selection[selection["status"] == "selected"]
    assert picked["ticker"].tolist() == ["A", "C"]
    assert selection.loc[selection["status"] == "group_limit", "ticker"].tolist() == ["B"]
    assert selection.loc[selection["status"] == "cash", "ticker"].tolist() == ["CASH"]  # one empty slot of three
