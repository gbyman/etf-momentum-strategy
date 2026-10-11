"""Regression tests for docs/review_2026-10-11_full_codex.md (N01-N06)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import BacktestConfig, StaleDataError, run_backtest
from etf_momentum.signals import MomentumConfig

SMALL = MomentumConfig(lookbacks=(21, 42, 63), skip_days=0, sma_window=50, volatility_window=20)


def panel(start: str, end: str) -> pd.DataFrame:
    index = pd.bdate_range(start, end)
    steps = np.arange(len(index))
    return pd.DataFrame({"A": 100 * 1.001**steps, "BENCH": 100 * 1.0005**steps, "CASH": 100.0}, index=index)


def config(**kw) -> BacktestConfig:
    return BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", candidate_tickers=("A",), momentum=SMALL, **kw)


# N01: next_close trades on the benchmark's next trading day, not on a stray holiday row
def test_next_close_skips_a_row_the_benchmark_did_not_trade() -> None:
    prices = panel("2025-01-01", "2026-02-27").drop(pd.Timestamp("2026-01-01"))
    observed = prices.notna()
    observed.loc["2025-12-31", ["BENCH", "CASH"]] = False  # only A has a (stray) row on the KRX year-end closure
    _, positions, _ = run_backtest(prices, config(execution="next_close"), observed=observed)
    row = positions[pd.to_datetime(positions["rebalance_date"]) == pd.Timestamp("2025-12-30")].iloc[0]
    assert pd.Timestamp(row["trade_date"]) == pd.Timestamp("2026-01-02")
    previous = positions[pd.to_datetime(positions["rebalance_date"]) == pd.Timestamp("2025-11-28")].iloc[0]
    assert pd.Timestamp(previous["hold_until"]) == pd.Timestamp("2026-01-02")


# N02-A: a long benchmark gap inside the evaluation stops the run (it is not 0% returns)
def test_benchmark_gap_inside_the_evaluation_is_an_error() -> None:
    prices = panel("2023-01-02", "2023-12-29")
    observed = prices.notna()
    observed.loc["2023-09-04":"2023-09-18", "BENCH"] = False  # 11 rows
    with pytest.raises(StaleDataError, match="BENCH"):
        run_backtest(prices, config(), observed=observed)


# N02-B: with exchange sessions, a month-end session the benchmark lacks is an error
def test_missing_month_end_session_is_an_error_with_sessions() -> None:
    prices = panel("2023-01-02", "2023-12-29")
    observed = prices.notna()
    observed.loc["2023-07-31", "BENCH"] = False  # Yahoo's real gap
    with pytest.raises(StaleDataError, match="2023-07-31"):
        run_backtest(prices, config(), observed=observed, sessions=prices.index)
    _, positions, _ = run_backtest(prices, config(), observed=observed)  # without sessions: old rule
    assert pd.Timestamp("2023-07-28") in set(pd.to_datetime(positions["rebalance_date"]))


def test_holiday_month_end_and_a_lagging_last_day_are_fine_with_sessions() -> None:
    prices = panel("2023-01-02", "2023-12-29")
    observed = prices.notna()
    observed.loc["2023-12-29", "BENCH"] = False  # last data day not yet published for the benchmark
    sessions = prices.index.drop(pd.Timestamp("2023-06-30"))  # June 30 was a closure; a stray row exists
    observed.loc["2023-06-30", "BENCH"] = False
    _, positions, _ = run_backtest(prices, config(), observed=observed, sessions=sessions)
    dates = set(pd.to_datetime(positions["rebalance_date"]))
    assert pd.Timestamp("2023-06-29") in dates and pd.Timestamp("2023-06-30") not in dates


# N04: the first month-end with exactly enough history is a signal date
def test_first_signal_is_the_first_month_end_with_enough_rows() -> None:
    index = pd.bdate_range("2023-01-24", "2023-04-28")
    steps = np.arange(len(index))
    prices = pd.DataFrame({"A": 100 * 1.01**steps, "BENCH": 100 * 1.005**steps, "CASH": 100.0}, index=index)
    momentum = MomentumConfig(lookbacks=(2, 3, 4), skip_days=0, sma_window=3, volatility_window=3)
    assert len(prices.loc[:"2023-01-31"]) == 6  # max(4 + 0 + 2, 3, 3 + 1)
    _, positions, _ = run_backtest(prices, BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", candidate_tickers=("A",), momentum=momentum))
    assert pd.Timestamp(positions["rebalance_date"].iloc[0]) == pd.Timestamp("2023-01-31")


# N02-B through the CLI: backtest passes the benchmark's exchange sessions
def test_backtest_command_stops_on_a_missing_month_end_session(tmp_path, monkeypatch) -> None:
    import sys

    import etf_momentum.cli as cli

    closes = panel("2022-01-03", "2023-12-29")
    closes.loc["2023-07-31", "BENCH"] = np.nan  # A and CASH traded: a real session the benchmark lacks
    monkeypatch.setattr(cli, "download_prices", lambda tickers, raw=False, **kw: (lambda f: f if raw else f.ffill())(closes[[t for t in tickers if t in closes]]))
    monkeypatch.setattr(cli, "_exchange_sessions", lambda benchmark: pd.bdate_range("2020-01-01", "2024-12-31"))
    (tmp_path / "u.csv").write_text("ticker,name,category,enabled\nA,Alpha,X,1\nCASH,Cash,Cash,1\n")
    monkeypatch.setattr(sys, "argv", ["etf-momentum", "backtest", "--universe", str(tmp_path / "u.csv"), "--benchmark", "BENCH",
                                      "--cash", "CASH", "--start", "2022-01-01", "--output-dir", str(tmp_path / "o")])
    with pytest.raises(StaleDataError, match="2023-07-31"):
        cli.main()


# N03: a proxy's traded value that stopped updating is not liquidity
def test_stale_proxy_traded_value_is_not_liquid() -> None:
    import etf_momentum.cli as cli
    from etf_momentum.data import splice_proxies
    from etf_momentum.signals import rank_momentum

    local = pd.bdate_range("2023-01-02", "2023-07-31")
    us = pd.bdate_range("2022-12-30", "2023-07-31")
    proxy_price = pd.Series(100 * 1.002 ** np.arange(len(us)), index=us)
    proxy_value = pd.Series(2_000_000.0, index=us)
    proxy_value.loc["2023-04-29":] = np.nan  # the proxy's volume stops; its price keeps updating
    fx = pd.Series(1300.0, index=us)
    raw = pd.DataFrame({"A.KS": np.nan, "B.KS": 100.0}, index=local)
    raw.loc["2023-06-26":, "A.KS"] = 260_000.0
    value = pd.DataFrame({"A.KS": np.nan, "B.KS": 5e9}, index=local)
    value.loc["2023-06-26":, "A.KS"] = 3e9
    prices, _ = splice_proxies(raw, {"A.KS": proxy_price}, fx)
    traded, starts = splice_proxies(value, {"A.KS": proxy_value}, fx, scale=False)
    traded = cli._mask_stale_proxy_values(traded, {"A.KS": proxy_value}, fx, starts)
    small = MomentumConfig(lookbacks=(5, 10, 20), skip_days=0, sma_window=10, volatility_window=10,
                           min_avg_traded_value=1e9, liquidity_window=10)
    row = rank_momentum(prices, pd.Timestamp("2023-06-20"), config=small, candidate_tickers=("A.KS",), traded_value=traded).iloc[0]
    assert not row["liquid"] and not row["eligible"]
    assert traded.loc["2023-06-26":, "A.KS"].eq(3e9).all()  # real values after listing are untouched


# N05: a cash ETF gap is an error when a scenario keeps money in cash (leverage < 1)
def run_portfolio_cli(tmp_path, monkeypatch, leverage: float) -> None:
    import json
    import sys

    import etf_momentum.cli as cli

    calendar = pd.bdate_range("2024-01-02", "2024-04-30")
    closes = pd.DataFrame({"CAL": 100.0, "GOOD": np.linspace(100, 120, len(calendar)), "CASH": np.linspace(100, 101, len(calendar))}, index=calendar)
    closes.loc["2024-02-01":"2024-03-15", "CASH"] = np.nan  # 32 sessions without a cash close

    def download(tickers, raw=False, **kw):
        frame = closes[list(tickers)].dropna(how="all")
        return frame if raw else frame.ffill()

    monkeypatch.setattr(cli, "download_prices", download)
    spec = {"base_currency": "KRW", "calendar_ticker": "CAL", "cash_ticker": "CASH",
            "sleeves": {"good": {"ticker": "GOOD", "currency": "KRW"}},
            "scenarios": [{"name": "g", "weights": {"good": 1.0}, "leverage": leverage}]}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    monkeypatch.setattr(sys, "argv", ["etf-momentum", "portfolio", "--spec", str(tmp_path / "spec.json"), "--output-dir", str(tmp_path / "o")])
    cli.main()


def test_portfolio_cash_gap_stops_when_cash_is_held(tmp_path, monkeypatch) -> None:
    with pytest.raises(SystemExit, match="CASH"):
        run_portfolio_cli(tmp_path, monkeypatch, leverage=0.5)


def test_portfolio_cash_gap_is_ignored_when_no_scenario_holds_cash(tmp_path, monkeypatch) -> None:
    run_portfolio_cli(tmp_path, monkeypatch, leverage=1.0)


# Recheck C03: only the dates a cash-holding scenario is evaluated on need cash closes
def run_late_sleeve_cli(tmp_path, monkeypatch, gap: tuple[str, str]) -> None:
    import json
    import sys

    import etf_momentum.cli as cli

    calendar = pd.bdate_range("2023-01-02", "2024-04-30")
    closes = pd.DataFrame({"CAL": 100.0, "CASH": 100 * 1.0001 ** np.arange(len(calendar))}, index=calendar)
    closes.loc[gap[0]:gap[1], "CASH"] = np.nan
    late = calendar[calendar >= "2024-01-02"]
    pd.DataFrame({"strategy": 100 * 1.001 ** np.arange(len(late))}, index=late).to_csv(tmp_path / "late.csv")

    def download(tickers, raw=False, **kw):
        frame = closes[list(tickers)].dropna(how="all")
        return frame if raw else frame.ffill()

    monkeypatch.setattr(cli, "download_prices", download)
    spec = {"base_currency": "KRW", "calendar_ticker": "CAL", "cash_ticker": "CASH",
            "sleeves": {"late": {"equity_csv": str(tmp_path / "late.csv"), "currency": "KRW"}},
            "scenarios": [{"name": "late", "weights": {"late": 1.0}, "leverage": 0.5}]}
    (tmp_path / "spec.json").write_text(json.dumps(spec))
    monkeypatch.setattr(sys, "argv", ["etf-momentum", "portfolio", "--spec", str(tmp_path / "spec.json"), "--output-dir", str(tmp_path / "o")])
    cli.main()


def test_cash_gap_before_the_scenario_is_evaluated_is_ignored(tmp_path, monkeypatch) -> None:
    run_late_sleeve_cli(tmp_path, monkeypatch, gap=("2023-02-01", "2023-03-15"))
    equity = pd.read_csv(tmp_path / "o" / "portfolio_equity.csv", index_col=0, parse_dates=True)
    assert equity.index[0] == pd.Timestamp("2024-01-02")


def test_cash_gap_running_into_the_evaluated_dates_stops(tmp_path, monkeypatch) -> None:
    # no cash close from 2023-12-01 to 2024-01-10: the first evaluated days use a stale cash close
    with pytest.raises(SystemExit, match="CASH"):
        run_late_sleeve_cli(tmp_path, monkeypatch, gap=("2023-12-01", "2024-01-10"))


# N06: the universe CSV and its meta are replaced as a pair
def test_failed_meta_replace_keeps_the_old_pair(tmp_path, monkeypatch) -> None:
    import hashlib
    import json
    import os

    import etf_momentum.universe_builder as builder

    output = tmp_path / "universe_kr_all.csv"
    output.write_text("OLD_CSV\n")
    (tmp_path / "universe_kr_all.meta.json").write_text('{"old": true}')
    universe = pd.DataFrame({"ticker": ["NEW"]})
    real_replace = os.replace
    calls = []

    def flaky_replace(src, dst):
        calls.append(dst)
        if str(dst).endswith(".meta.json"):
            raise OSError("disk full")
        real_replace(src, dst)

    monkeypatch.setattr(builder.os, "replace", flaky_replace)
    with pytest.raises(OSError):
        builder.write_universe(universe, {"built_at": "new"}, output)
    assert output.read_text() == "OLD_CSV\n"
    assert json.loads((tmp_path / "universe_kr_all.meta.json").read_text()) == {"old": True}
    assert not [p for p in tmp_path.iterdir() if p.name.endswith((".tmp", ".bak"))]

    monkeypatch.setattr(builder.os, "replace", real_replace)
    builder.write_universe(universe, {"built_at": "new"}, output)
    meta = json.loads((tmp_path / "universe_kr_all.meta.json").read_text())
    assert meta["csv_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
