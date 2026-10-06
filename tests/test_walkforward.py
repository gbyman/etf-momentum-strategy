from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from etf_momentum.walkforward import walk_forward


def regime_switch_returns() -> pd.DataFrame:
    """A wins for the first 6 years, B for the rest."""
    index = pd.bdate_range("2010-01-01", "2019-12-31")
    first_half = index < pd.Timestamp("2016-01-01")
    return pd.DataFrame(
        {
            "A": np.where(first_half, 0.0008, -0.0004),
            "B": np.where(first_half, -0.0004, 0.0008),
        },
        index=index,
    )


def test_selection_uses_only_training_window() -> None:
    returns = regime_switch_returns()
    oos, windows = walk_forward(returns, train_years=3, test_months=12, metric="cagr")

    first = windows.iloc[0]
    assert first["train_start"] == pd.Timestamp("2010-01-01")
    assert first["test_start"] == pd.Timestamp("2013-01-01")
    assert first["selected"] == "A"
    # 2016 test window trains on 2013-2015 (all A-favourable), so still A: no peeking at 2016.
    assert windows.set_index("test_start").loc[pd.Timestamp("2016-01-01"), "selected"] == "A"
    # Once the training window is mostly B-favourable, B is chosen.
    assert windows.iloc[-1]["selected"] == "B"


def test_oos_returns_are_selected_config_returns_in_test_window() -> None:
    returns = regime_switch_returns()
    oos, windows = walk_forward(returns, train_years=3, test_months=12, metric="cagr")
    for row in windows.itertuples():
        window = oos.loc[row.test_start : row.test_end]
        pd.testing.assert_series_equal(window, returns.loc[window.index, row.selected], check_names=False)
    assert oos.index[0] == pd.Timestamp("2013-01-01")
    assert not oos.index.duplicated().any()


def test_configs_without_full_training_history_are_skipped() -> None:
    returns = regime_switch_returns()
    returns["LATE"] = np.nan
    returns.loc["2014-06-01":, "LATE"] = 0.01  # spectacular, but no history in early windows
    _, windows = walk_forward(returns, train_years=3, test_months=12, metric="cagr")
    assert windows.iloc[0]["selected"] == "A"


def test_anchored_training_starts_at_beginning() -> None:
    _, windows = walk_forward(regime_switch_returns(), train_years=3, test_months=12, anchored=True)
    assert (windows["train_start"] == pd.Timestamp("2010-01-01")).all()


def test_unknown_metric_rejected() -> None:
    with pytest.raises(ValueError, match="metric"):
        walk_forward(regime_switch_returns(), metric="nope")


def test_zero_drawdown_scores() -> None:
    from etf_momentum.walkforward import score

    rising = pd.Series([0.001] * 300)
    flat = pd.Series([0.0] * 300)
    assert score(rising, "calmar") == np.inf
    assert score(flat, "calmar") == 0.0
    assert score(rising, "sharpe") == np.inf
    assert score(flat, "sharpe") == 0.0


def test_lossless_config_can_be_selected() -> None:
    returns = regime_switch_returns()
    noise = np.where(np.arange(len(returns)) % 2 == 0, 0.01, -0.01)
    returns[["A", "B"]] = returns[["A", "B"]].add(noise, axis=0)  # higher return, but with drawdowns
    returns["STEADY"] = 0.0003  # never loses, so max drawdown is 0
    _, windows = walk_forward(returns, train_years=3, test_months=12, metric="calmar")
    assert (windows["selected"] == "STEADY").all()


def test_tied_infinite_scores_prefer_higher_cagr() -> None:
    index = pd.bdate_range("2010-01-01", "2016-12-31")
    returns = pd.DataFrame({"SLOW": 0.0001, "FAST": 0.0005}, index=index)  # both lossless
    _, windows = walk_forward(returns, train_years=3, test_months=12, metric="calmar")
    assert (windows["selected"] == "FAST").all()
