from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd
import pytest

import etf_momentum.data as data
from etf_momentum.backtest import BacktestConfig, run_backtest
from etf_momentum.signals import MomentumConfig, rank_momentum


FAST = MomentumConfig(skip_days=0, sma_window=100)


def prices_with_one_winner(days: int = 600) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=days)
    return pd.DataFrame(
        {
            "WIN": 100 * np.power(1.0015, np.arange(days)),
            "LOSE1": np.linspace(100, 80, days),
            "LOSE2": np.linspace(100, 70, days),
            "BENCH": 100 * np.power(1.0005, np.arange(days)),
            "CASH": 100 * np.power(1.0001, np.arange(days)),
        },
        index=index,
    )


def parse_weights(text: str) -> dict[str, float]:
    return {ticker: float(weight) for ticker, weight in (item.split(":") for item in text.split(","))}


# 1. candidate universe separation
def test_rank_momentum_limits_ranking_to_candidates_but_keeps_benchmark_comparison() -> None:
    ranking = rank_momentum(
        prices_with_one_winner(),
        benchmark="BENCH",
        config=FAST,
        candidate_tickers=["WIN", "LOSE1", "LOSE2"],
    )
    assert set(ranking["ticker"]) == {"WIN", "LOSE1", "LOSE2"}
    assert ranking["relative_to_benchmark"].notna().all()


def test_backtest_never_selects_non_candidates() -> None:
    _, positions, _ = run_backtest(
        prices_with_one_winner(),
        BacktestConfig(top_n=2, cash_ticker="CASH", benchmark="BENCH", momentum=FAST, candidate_tickers=("LOSE1", "LOSE2")),
    )
    held = {ticker for tickers in positions["tickers"] for ticker in tickers.split(",")}
    assert held == {"CASH"}


# 2. cache key stability and freshness
def test_cache_path_is_stable_for_long_ticker_lists(tmp_path) -> None:
    tickers = [f"TICKER{i:03d}" for i in range(40)]
    first = data._cache_path(tickers, "2020-01-01", None, tmp_path)
    assert first == data._cache_path(list(tickers), "2020-01-01", None, tmp_path)
    assert first != data._cache_path(tickers[:-1], "2020-01-01", None, tmp_path)
    # Deterministic across processes (builtin hash() is salted per interpreter run).
    assert first.name == "prices_40_tickers_daa0d04e4aefcbb8_2020-01-01_latest.csv"


def test_stale_latest_cache_is_redownloaded(tmp_path, monkeypatch) -> None:
    cache = data._cache_path(["AAA"], "2020-01-01", None, tmp_path)
    pd.DataFrame({"AAA": [1.0]}, index=pd.to_datetime(["2020-01-02"])).to_csv(cache)
    old = time.time() - 48 * 3600
    os.utime(cache, (old, old))

    fresh = pd.DataFrame({"Close": [1.0, 2.0]}, index=pd.to_datetime(["2020-01-02", "2020-01-03"]))
    monkeypatch.setattr(data.yf, "download", lambda **kwargs: fresh)
    prices = data.download_prices(["AAA"], start="2020-01-01", cache_dir=tmp_path)
    assert prices.index[-1] == pd.Timestamp("2020-01-03")


def test_stale_cache_is_used_with_warning_when_download_fails(tmp_path, monkeypatch) -> None:
    cache = data._cache_path(["AAA"], "2020-01-01", None, tmp_path)
    pd.DataFrame({"AAA": [1.0]}, index=pd.to_datetime(["2020-01-02"])).to_csv(cache)
    old = time.time() - 48 * 3600
    os.utime(cache, (old, old))

    monkeypatch.setattr(data.yf, "download", lambda **kwargs: pd.DataFrame())
    with pytest.warns(UserWarning, match="stale"):
        prices = data.download_prices(["AAA"], start="2020-01-01", cache_dir=tmp_path)
    assert prices.index[-1] == pd.Timestamp("2020-01-02")


def test_staleness_message_flags_old_price_data() -> None:
    prices = pd.DataFrame({"AAA": [1.0]}, index=pd.to_datetime(["2026-05-22"]))
    assert data.staleness_message(prices, today=pd.Timestamp("2026-09-30")) is not None
    assert data.staleness_message(prices, today=pd.Timestamp("2026-05-25")) is None


# 3. cash ticker validation
def test_missing_cash_ticker_raises() -> None:
    with pytest.raises(ValueError, match="NOPE"):
        run_backtest(prices_with_one_winner(), BacktestConfig(cash_ticker="NOPE", benchmark="BENCH", momentum=FAST))


# 4. unfilled slots go to cash
def test_unfilled_slots_are_allocated_to_cash() -> None:
    _, positions, _ = run_backtest(
        prices_with_one_winner(),
        BacktestConfig(top_n=3, cash_ticker="CASH", benchmark="BENCH", momentum=FAST, candidate_tickers=("WIN", "LOSE1", "LOSE2")),
    )
    weights = parse_weights(positions[positions["tickers"].str.contains("WIN")].iloc[0]["weights"])
    assert weights["WIN"] == pytest.approx(1 / 3, abs=1e-4)
    assert weights["CASH"] == pytest.approx(2 / 3, abs=1e-4)


def test_max_position_weight_holds_with_single_candidate() -> None:
    _, positions, _ = run_backtest(
        prices_with_one_winner(),
        BacktestConfig(
            top_n=1,
            cash_ticker="CASH",
            benchmark="BENCH",
            max_position_weight=0.5,
            momentum=FAST,
            candidate_tickers=("WIN", "LOSE1", "LOSE2"),
        ),
    )
    weights = parse_weights(positions[positions["tickers"].str.contains("WIN")].iloc[0]["weights"])
    assert weights["WIN"] == pytest.approx(0.5, abs=1e-4)
    assert weights["CASH"] == pytest.approx(0.5, abs=1e-4)


# 5. rank_by
def prices_fast_volatile_vs_slow_steady(days: int = 600) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=days)
    steps = np.arange(days)
    return pd.DataFrame(
        {
            # Stronger trend, but +/-3% zigzag every day makes it far more volatile.
            "WILD": 100 * np.power(1.0025, steps) * (1 + 0.03 * (-1.0) ** steps),
            "CALM": 100 * np.power(1.0012, steps) * (1 + 0.001 * (-1.0) ** steps),
            "CASH": 100.0,
        },
        index=index,
    )


def test_rank_by_risk_adjusted_score_changes_order() -> None:
    prices = prices_fast_volatile_vs_slow_steady()
    by_momentum = rank_momentum(prices, config=FAST, candidate_tickers=["WILD", "CALM"])
    by_risk = rank_momentum(
        prices, config=MomentumConfig(skip_days=0, sma_window=100, rank_by="risk_adjusted_score"), candidate_tickers=["WILD", "CALM"]
    )
    assert by_momentum.iloc[0]["ticker"] == "WILD"
    assert by_risk.iloc[0]["ticker"] == "CALM"
    assert by_risk.set_index("ticker").loc["CALM", "rank"] == 1


def test_unknown_rank_by_is_rejected() -> None:
    with pytest.raises(ValueError, match="rank_by"):
        rank_momentum(prices_with_one_winner(), config=MomentumConfig(skip_days=0, sma_window=100, rank_by="nope"))


# 6. group limit
def prices_two_semis_and_one_other(days: int = 600) -> pd.DataFrame:
    index = pd.bdate_range("2020-01-01", periods=days)
    steps = np.arange(days)
    return pd.DataFrame(
        {
            "SEMI1": 100 * np.power(1.0020, steps),
            "SEMI2": 100 * np.power(1.0019, steps),
            "OTHER": 100 * np.power(1.0010, steps),
            "CASH": 100.0,
        },
        index=index,
    )


def test_max_per_group_skips_duplicate_exposure() -> None:
    _, positions, _ = run_backtest(
        prices_two_semis_and_one_other(),
        BacktestConfig(
            top_n=2,
            cash_ticker="CASH",
            benchmark="OTHER",
            momentum=FAST,
            candidate_tickers=("SEMI1", "SEMI2", "OTHER"),
            groups={"SEMI1": "semis", "SEMI2": "semis"},
            max_per_group=1,
        ),
    )
    assert set(positions.iloc[0]["tickers"].split(",")) == {"SEMI1", "OTHER"}


def test_without_group_limit_duplicates_are_allowed() -> None:
    _, positions, _ = run_backtest(
        prices_two_semis_and_one_other(),
        BacktestConfig(
            top_n=2,
            cash_ticker="CASH",
            benchmark="OTHER",
            momentum=FAST,
            candidate_tickers=("SEMI1", "SEMI2", "OTHER"),
            groups={"SEMI1": "semis", "SEMI2": "semis"},
        ),
    )
    assert set(positions.iloc[0]["tickers"].split(",")) == {"SEMI1", "SEMI2"}


def test_load_universe_reads_optional_group_column(tmp_path) -> None:
    path = tmp_path / "universe.csv"
    path.write_text("ticker,name,category,enabled,group\nSMH,Semis,Theme,1,semis\nXLE,Energy,Sector,1,\n")
    members = data.load_universe(path)
    assert [member.group for member in members] == ["semis", ""]


# Post-fix review: empty ranking must fall back to cash, not crash
def test_candidates_missing_from_data_hold_cash() -> None:
    _, positions, _ = run_backtest(
        prices_with_one_winner(),
        BacktestConfig(cash_ticker="CASH", benchmark="BENCH", momentum=FAST, candidate_tickers=("NOPE",)),
    )
    assert set(positions["tickers"]) == {"CASH"}
    assert (positions["eligible_count"] == 0).all()


def test_candidates_without_enough_history_hold_cash_until_ready() -> None:
    prices = prices_with_one_winner(days=900)
    prices.loc[: prices.index[300], ["WIN", "LOSE1", "LOSE2"]] = np.nan  # listed late
    _, positions, _ = run_backtest(
        prices,
        BacktestConfig(top_n=1, cash_ticker="CASH", benchmark="BENCH", momentum=FAST, candidate_tickers=("WIN", "LOSE1", "LOSE2")),
    )
    assert positions.iloc[0]["tickers"] == "CASH"
    assert "WIN" in set(positions["tickers"])


def test_empty_ranking_keeps_columns() -> None:
    ranking = rank_momentum(prices_with_one_winner(), config=FAST, candidate_tickers=["NOPE"])
    assert ranking.empty
    assert {"ticker", "eligible", "rank", "weighted_momentum"} <= set(ranking.columns)
