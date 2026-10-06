from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.data import account_candidates, load_universe, splice_proxies
from etf_momentum.signals import MomentumConfig


UNIVERSE = """ticker,name,category,enabled,group,pension_ok,retirement_ok,risk_asset,proxy,note
069500.KS,KODEX 200,Korea Broad,1,,1,1,1,,
153130.KS,Short-Term Bond,Korea Cash,1,,1,1,0,,
360750.KS,TIGER S&P500,Overseas Equity,1,us_sp500,1,1,1,SPY,
143850.KS,TIGER S&P500 Futures(H),Overseas Equity,1,us_sp500,1,0,1,,derivative
305080.KS,UST 10Y Futures,Overseas Bond,0,,1,0,0,,
"""


def test_universe_reads_account_columns_with_defaults(tmp_path) -> None:
    path = tmp_path / "u.csv"
    path.write_text(UNIVERSE)
    members = {m.ticker: m for m in load_universe(path)}
    assert members["360750.KS"].proxy == "SPY"
    assert not members["143850.KS"].retirement_ok and members["143850.KS"].pension_ok
    assert not members["153130.KS"].risk_asset

    plain = tmp_path / "plain.csv"
    plain.write_text("ticker,name,category,enabled\nXLE,Energy,Sector,1\n")
    member = load_universe(plain)[0]
    assert member.pension_ok and member.retirement_ok and member.risk_asset and member.proxy == ""


def test_account_candidates_filter_by_account_and_exclude_benchmark_and_cash(tmp_path) -> None:
    path = tmp_path / "u.csv"
    path.write_text(UNIVERSE)
    members = load_universe(path)
    assert account_candidates(members, "all", "069500.KS", "153130.KS") == ("360750.KS", "143850.KS")
    assert account_candidates(members, "pension", "069500.KS", "153130.KS") == ("360750.KS", "143850.KS")
    assert account_candidates(members, "retirement", "069500.KS", "153130.KS") == ("360750.KS",)
    with pytest.raises(ValueError, match="account"):
        account_candidates(members, "brokerage", "069500.KS", "153130.KS")


def test_splice_fills_pre_listing_history_from_fx_converted_proxy() -> None:
    kr_days = pd.bdate_range("2020-01-01", periods=10)
    local = pd.DataFrame({"360750.KS": [np.nan] * 4 + [11000.0, 11110.0, 11000.0, 11220.0, 11330.0, 11440.0]}, index=kr_days)
    proxy = pd.Series([100.0, 101.0, 102.0, 103.0, 100.0, 101.0, 100.0, 102.0, 103.0, 104.0], index=kr_days)
    fx = pd.Series(1100.0, index=kr_days)
    fx.iloc[:2] = 1000.0
    spliced, starts = splice_proxies(local, {"360750.KS": proxy}, fx)
    assert starts == {"360750.KS": kr_days[4]}
    pd.testing.assert_series_equal(spliced["360750.KS"].iloc[4:], local["360750.KS"].iloc[4:])
    pre = spliced["360750.KS"].iloc[:5]
    proxy_krw = (proxy * fx).iloc[:5]
    pd.testing.assert_series_equal(pre / pre.iloc[-1], proxy_krw / proxy_krw.iloc[-1], check_names=False)


def test_splice_aligns_proxy_on_local_calendar() -> None:
    kr_days = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])
    local = pd.DataFrame({"360750.KS": [np.nan, np.nan, 10.0, 10.5]}, index=kr_days)
    us_days = pd.to_datetime(["2020-01-02", "2020-01-06", "2020-01-07"])  # US closed on 01-03
    proxy = pd.Series([50.0, 51.0, 52.0], index=us_days)
    spliced, _ = splice_proxies(local, {"360750.KS": proxy}, pd.Series(1.0, index=kr_days))
    assert spliced.loc["2020-01-03", "360750.KS"] == spliced.loc["2020-01-02", "360750.KS"]  # carried forward
    assert not spliced["360750.KS"].isna().any()


def risky_prices(days: int = 700) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=days)
    steps = np.arange(days)
    return pd.DataFrame(
        {"A": 100 * np.power(1.002, steps), "BENCH": 100 * np.power(1.0005, steps), "CASH": 100 * np.power(1.0001, steps)},
        index=index,
    )


def test_retirement_risky_cap_keeps_at_least_thirty_percent_in_safe_cash() -> None:
    _, positions, _ = run_backtest(
        risky_prices(),
        BacktestConfig(
            top_n=1, cash_ticker="CASH", benchmark="BENCH", momentum=MomentumConfig(skip_days=0, sma_window=100),
            candidate_tickers=("A",), max_risky_weight=0.7,
        ),
    )
    weights = dict(item.split(":") for item in positions["weights"].iloc[0].split(","))
    assert float(weights["A"]) == pytest.approx(0.7)
    assert float(weights["CASH"]) == pytest.approx(0.3)


def test_risky_cap_requires_cash_ticker() -> None:
    with pytest.raises(ValueError, match="cash"):
        run_backtest(risky_prices(), BacktestConfig(cash_ticker=None, benchmark="BENCH", max_risky_weight=0.7))
