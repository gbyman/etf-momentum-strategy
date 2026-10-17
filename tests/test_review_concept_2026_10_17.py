"""Concept review of 2026-10-17 (docs/review_concept_2026-10-17_codex.md): C17-01 and C17-02."""

from __future__ import annotations

import contextlib
import io
import sys
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

import etf_momentum.cli as cli
from etf_momentum.backtest import BacktestConfig, run_backtest, select_holdings
from etf_momentum.signals import MomentumConfig, _average_traded_value, rank_momentum

XNYS = cli._exchange_sessions("SPY")
XKRX = cli._exchange_sessions("069500.KS")
DAYS = XNYS[(XNYS >= "2024-01-01") & (XNYS <= "2025-10-07")]
AS_OF = pd.Timestamp("2025-09-30")
LAST_A_VALUE = pd.Timestamp("2025-09-22")  # six XNYS sessions before AS_OF
LIQUID = replace(MomentumConfig(), min_avg_traded_value=1e9)


def prices() -> pd.DataFrame:
    n = np.arange(len(DAYS))
    return pd.DataFrame(
        {"A": 100 * np.exp(0.002 * n), "B": 100 * np.exp(0.001 * n), "SPY": 100 * np.exp(0.0005 * n), "BIL": 100.0}, index=DAYS
    )


def compressed_traded_value(last: pd.Timestamp = LAST_A_VALUE) -> pd.DataFrame:
    """2e9 a day; the table has no rows between `last` and AS_OF, and on AS_OF only A is missing."""
    index = DAYS[(DAYS <= last) | (DAYS >= AS_OF)]
    value = pd.DataFrame(2e9, index=index, columns=["A", "B", "SPY", "BIL"])
    value.loc[value.index > last, "A"] = np.nan
    return value


def liquid_a(value: pd.DataFrame, sessions=XNYS) -> bool:
    ranking = rank_momentum(prices(), AS_OF, benchmark="SPY", config=LIQUID, candidate_tickers=["A", "B"],
                            traded_value=value, observed=prices().notna(), sessions=sessions)
    return bool(ranking.set_index("ticker").loc["A", "liquid"])


# C17-01: traded value freshness counts exchange sessions, not only the table's rows
def test_traded_value_stale_in_sessions_is_illiquid_although_the_table_has_no_rows_for_the_gap() -> None:
    assert not liquid_a(compressed_traded_value())
    assert not liquid_a(compressed_traded_value().reindex(DAYS))  # same data with the gap rows as NaN


def test_traded_value_five_sessions_old_is_liquid_six_is_not() -> None:
    for age, liquid in ((5, True), (6, False)):
        last = DAYS[DAYS.get_loc(AS_OF) - age]
        assert liquid_a(compressed_traded_value(last)) is liquid


def test_traded_value_freshness_without_a_calendar_counts_rows() -> None:
    assert liquid_a(compressed_traded_value(), sessions=None)  # documented fallback: one row after the last value


def test_krx_holidays_are_not_counted_as_traded_value_staleness() -> None:
    # 2025-10-03 and 10-06..09 are KRX holidays: four sessions (10-01, 10-02, 10-10, 10-13) but nine weekdays.
    index = XKRX[(XKRX >= "2025-01-02") & (XKRX <= "2025-10-13")]
    value = pd.DataFrame(2e9, index=index, columns=["A", "B"])
    value.loc[value.index > "2025-09-30", "A"] = np.nan
    average = _average_traded_value(value, pd.Timestamp("2025-10-13"), 63, sessions=XKRX)
    assert average["A"] == 2e9
    value.loc[value.index > "2025-09-29", "A"] = np.nan  # five sessions after 09-29
    assert _average_traded_value(value, pd.Timestamp("2025-10-13"), 63, sessions=XKRX)["A"] == 2e9
    value.loc[value.index > "2025-09-26", "A"] = np.nan  # six sessions
    assert np.isnan(_average_traded_value(value, pd.Timestamp("2025-10-13"), 63, sessions=XKRX)["A"])


def test_traded_value_window_and_minimum_count_stay_on_the_table_rows() -> None:
    # Unchanged by C17-01: the 63-row window and the 2/3 minimum count use the rows the table has.
    value = compressed_traded_value()
    value.loc[value.index <= LAST_A_VALUE, "B"] = np.r_[np.full(int((value.index <= LAST_A_VALUE).sum()) - 62, 1e9), np.full(62, 3e9)]
    average = _average_traded_value(value, AS_OF, 63, sessions=XNYS)
    assert average["B"] == pytest.approx((62 * 3e9 + 2e9) / 63)


def test_backtest_signal_skips_a_candidate_with_stale_traded_value() -> None:
    config = BacktestConfig(top_n=1, candidate_tickers=("A", "B"), benchmark="SPY", cash_ticker="BIL", momentum=LIQUID)
    _, positions, _ = run_backtest(prices(), config, traded_value=compressed_traded_value(), observed=prices().notna(), sessions=XNYS)
    assert positions.loc[pd.to_datetime(positions["rebalance_date"]) == AS_OF, "tickers"].iloc[0] == "B"


def universe(tmp_path) -> str:
    path = tmp_path / "universe.csv"
    pd.DataFrame({"ticker": ["A", "B", "SPY", "BIL"], "name": ["A", "B", "SPY", "BIL"], "category": "test", "enabled": 1}).to_csv(path, index=False)
    return str(path)


def rank(tmp_path, monkeypatch, price_frame: pd.DataFrame, extra: list[str], value: pd.DataFrame | None = None) -> pd.DataFrame:
    monkeypatch.setattr(cli, "download_prices", lambda *a, **k: price_frame)
    if value is not None:
        monkeypatch.setattr(cli, "download_traded_value", lambda *a, **k: value)
    out = tmp_path / "out"
    argv = ["etf-momentum", "rank", "--universe", universe(tmp_path), "--benchmark", "SPY", "--end", "2025-10-08", "--top-n", "1",
            "--risk-on-benchmark", "--output-dir", str(out), "--cache-dir", str(tmp_path / "cache"), *extra]
    monkeypatch.setattr(sys, "argv", argv)
    with contextlib.redirect_stdout(io.StringIO()):
        cli.main()
    return pd.read_csv(out / "latest_selection.csv")


def test_rank_command_skips_a_candidate_with_stale_traded_value(tmp_path, monkeypatch) -> None:
    selection = rank(tmp_path, monkeypatch, prices(), ["--cash", "BIL", "--min-avg-traded-value", "1e9"], compressed_traded_value())
    assert selection.loc[selection["status"] == "selected", "ticker"].tolist() == ["B"]


# C17-02: the month-end waiting asset must have been observed at that month end
def risk_off_at_month_end(cash_gap: tuple[str, str] | None = None) -> pd.DataFrame:
    frame = prices()
    frame.loc[frame.index >= AS_OF, "SPY"] = 1.0  # below its SMA: the 09-30 selection is all cash
    if cash_gap:
        frame.loc[(frame.index >= cash_gap[0]) & (frame.index <= cash_gap[1]), "BIL"] = np.nan
    return frame


def test_rank_stops_when_the_month_end_cash_was_stale_then_recovered(tmp_path, monkeypatch) -> None:
    frame = risk_off_at_month_end(("2025-09-23", "2025-09-30"))  # 6 sessions without BIL at 09-30, back from 10-01
    with pytest.raises(SystemExit, match="cash BIL.*2025-09-30"):
        rank(tmp_path, monkeypatch, frame, ["--cash", "BIL"])
    assert not (tmp_path / "out" / "latest_selection.csv").exists()


def test_rank_stops_on_stale_month_end_cash_when_candidates_run_short(tmp_path, monkeypatch) -> None:
    frame = prices()
    frame.loc[(frame.index >= "2025-09-23") & (frame.index <= "2025-09-30"), "BIL"] = np.nan
    with pytest.raises(SystemExit, match="cash BIL"):
        rank(tmp_path, monkeypatch, frame, ["--cash", "BIL", "--min-weighted-momentum", "100"])  # nobody qualifies


def test_rank_keeps_a_month_end_cash_observed_then(tmp_path, monkeypatch) -> None:
    frame = risk_off_at_month_end(("2025-09-24", "2025-09-30"))  # five sessions: still fresh
    selection = rank(tmp_path, monkeypatch, frame, ["--cash", "BIL"])
    assert selection[["status", "ticker"]].values.tolist() == [["cash", "BIL"]]


def test_rank_month_end_cash_check_only_applies_to_cash_slots(tmp_path, monkeypatch) -> None:
    frame = prices()  # risk on, A selected: the stale month-end cash isn't part of the selection
    frame.loc[(frame.index >= "2025-09-23") & (frame.index <= "2025-09-30"), "BIL"] = np.nan
    selection = rank(tmp_path, monkeypatch, frame, ["--cash", "BIL"])
    assert selection.loc[selection["status"] == "selected", "ticker"].tolist() == ["A"]


def test_rank_uninvested_cash_needs_no_cash_etf(tmp_path, monkeypatch) -> None:
    selection = rank(tmp_path, monkeypatch, risk_off_at_month_end(("2025-09-23", "2025-09-30")), ["--cash", "none"])
    assert selection[["status", "slots"]].values.tolist() == [["cash", 1]]


def test_rank_month_end_cash_listed_later_keeps_the_pre_listing_policy(tmp_path, monkeypatch) -> None:
    frame = risk_off_at_month_end(("2024-01-01", "2025-09-30"))  # BIL's first close is 10-01
    selection = rank(tmp_path, monkeypatch, frame, ["--cash", "BIL"])
    assert selection[["status", "ticker"]].values.tolist() == [["cash", "BIL"]]
