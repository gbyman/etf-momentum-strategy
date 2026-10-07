from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.backtest import summarize_performance
from etf_momentum.walkforward import score, walk_forward


# 3-1. walk-forward Sharpe subtracts the same risk-free rate as the reported Sharpe
def alternating(mean: float, swing: float, index: pd.DatetimeIndex) -> pd.Series:
    return pd.Series(np.where(np.arange(index.size) % 2 == 0, mean + swing, mean - swing), index=index)


def test_training_sharpe_matches_reported_sharpe_definition() -> None:
    index = pd.bdate_range("2020-01-01", periods=500)
    a, b, rf = alternating(0.0015, 0.001, index), alternating(0.0006, 0.0002, index), pd.Series(0.0005, index=index)
    reported = {
        name: summarize_performance((1 + s).cumprod().to_frame("x"), risk_free=rf)["x_sharpe"] for name, s in (("A", a), ("B", b))
    }
    trained = {name: score(s, "sharpe", risk_free=rf) for name, s in (("A", a), ("B", b))}
    assert reported["A"] > reported["B"] and trained["A"] > trained["B"]  # same preference
    assert trained["A"] == pytest.approx((a - rf).mean() / (a - rf).std() * np.sqrt(252))


def test_walk_forward_picks_by_excess_sharpe_when_risk_free_given() -> None:
    index = pd.bdate_range("2010-01-01", "2016-12-31")
    returns = pd.DataFrame({"A": alternating(0.0015, 0.001, index), "B": alternating(0.0006, 0.0002, index)})
    rf = pd.Series(0.0005, index=index)
    _, raw = walk_forward(returns, train_years=3, test_months=12, metric="sharpe")
    _, excess = walk_forward(returns, train_years=3, test_months=12, metric="sharpe", risk_free=rf)
    assert set(raw["selected"]) == {"B"}  # raw return/vol favours the calm series
    assert set(excess["selected"]) == {"A"}  # excess over cash favours A, as the report does


# 3-2. a foreign (previous_close) sleeve that goes stale and later resumes is an error, not a silent skip
from etf_momentum.portfolio import sleeve_returns_on_calendar  # noqa: E402


def test_previous_close_gap_then_resume_is_an_error() -> None:
    calendar = pd.bdate_range("2021-01-01", periods=20)
    foreign = pd.Series([100.0, 100.0, 200.0, 200.0, 200.0], index=calendar[[0, 1, 12, 13, 19]])
    with pytest.raises(ValueError, match="gap"):
        sleeve_returns_on_calendar(foreign, calendar, "previous_close")


def test_previous_close_permanent_end_still_ends_the_sleeve() -> None:
    calendar = pd.bdate_range("2021-01-01", periods=20)
    foreign = pd.Series([100.0, 110.0], index=calendar[:2])
    returns = sleeve_returns_on_calendar(foreign, calendar, "previous_close")
    assert returns.iloc[-1:].isna().all()


def test_previous_close_short_holiday_is_fine() -> None:
    calendar = pd.bdate_range("2021-01-04", periods=10)
    foreign = pd.Series(range(100, 109), index=calendar.delete(4), dtype=float)  # one foreign holiday
    returns = sleeve_returns_on_calendar(foreign, calendar, "previous_close")
    assert returns.dropna().size == 9


# 3-3. selection reasons: metrics for picks, reasons for better-ranked ETFs that were not picked
import json  # noqa: E402
import sys  # noqa: E402

import etf_momentum.backtest as backtest  # noqa: E402
import etf_momentum.cli as cli  # noqa: E402


def test_exclusion_reason_names_every_failed_check() -> None:
    row = pd.Series({"positive_momentum": False, "above_sma": False, "relative_ok": True, "liquid": False,
                     "volatility_ok": True, "price_fresh": False})
    assert cli._exclusion_reason(row) == "모멘텀≤0, 200일선 아래, 유동성 부족, 가격 정체"


@pytest.fixture
def reason_setup(tmp_path, monkeypatch):
    index = pd.bdate_range(end="2026-10-06", periods=700)
    steps = np.arange(index.size)
    data = pd.DataFrame(
        {
            "A": 100 * np.power(1.0020, steps), "B": 100 * np.power(1.0018, steps), "C": 100 * np.power(1.0010, steps),
            "D": np.linspace(100, 80, index.size), "E": 100 * np.power(1.0030, steps),
            "BENCH": 100 * np.power(1.0005, steps), "CASH": 100 * np.power(1.0001, steps),
        },
        index=index,
    )
    data.loc["2026-08-17":, "E"] = data.loc["2026-08-14", "E"]  # frozen since mid-August: stale at 09-30
    universe = tmp_path / "u.csv"
    universe.write_text("ticker,name,category,enabled,group\nA,Alpha,X,1,g1\nB,Beta,X,1,g1\nC,Gamma,X,1,\n"
                        "D,Delta,X,1,\nE,Echo,X,1,\nCASH,Cash,Cash,1,\n")
    strategy = {
        "id": "why", "version": 1, "description": "t", "universe": str(universe), "account": "all", "benchmark": "BENCH",
        "cash": "CASH", "use_proxies": False,
        "signal": {"lookbacks": [63, 126, 252], "weights": [0.3, 0.3, 0.4], "skip_days": 21, "sma_window": 200,
                   "volatility_window": 63, "rank_by": "weighted_momentum", "min_avg_traded_value": None},
        "selection": {"top_n": 3, "max_per_group": 1, "risk_on_benchmark": True, "risk_on_tickers": None},
    }
    (tmp_path / "why.json").write_text(json.dumps(strategy))
    monkeypatch.setattr(cli, "download_prices", lambda tickers, **kw: data[[t for t in tickers if t in data]])
    monkeypatch.setattr(cli, "previous_month_end", lambda d, today=None: backtest.previous_month_end(d, pd.Timestamp("2026-10-07")))
    monkeypatch.setattr(sys, "argv", ["etf-momentum", "rank", "--strategy", str(tmp_path / "why.json"), "--output-dir", str(tmp_path / "out")])
    cli.main()
    return tmp_path / "out"


def test_selection_file_explains_picks_exclusions_and_cash(reason_setup) -> None:
    sel = pd.read_csv(reason_setup / "latest_selection.csv", keep_default_na=False)
    picked = sel[sel["status"] == "selected"]
    assert picked["ticker"].tolist() == ["A", "C"]
    assert (picked["weighted_momentum"] != "").all() and (picked["trend_strength_vs_sma"] != "").all()
    excluded = sel[sel["status"] == "excluded"].set_index("ticker")
    assert "가격 정체" in excluded.loc["E", "reason"]
    assert "D" not in excluded.index  # ranked below the last pick: not a "why not" candidate
    assert sel.loc[sel["status"] == "group_limit", "reason"].tolist() == ["그룹 제한 (g1)"]
    assert sel.loc[sel["status"] == "cash", "reason"].tolist() == ["조건 통과 후보 부족"]


def test_excluded_rows_are_not_written_to_the_signal_log(reason_setup) -> None:
    signals = pd.read_csv(reason_setup / "strategies" / "why" / "signals.csv", dtype=str)
    assert "excluded" not in set(signals["status"])
