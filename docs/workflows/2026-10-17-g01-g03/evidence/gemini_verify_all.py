from __future__ import annotations

import contextlib
import hashlib
import io
import json
import math
import sys
import tempfile
import warnings
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

import etf_momentum.cli as cli
import etf_momentum.data as data
import etf_momentum.naver as naver
from etf_momentum.backtest import (
    BacktestConfig,
    StaleDataError,
    previous_month_end,
    run_backtest,
    select_holdings,
    summarize_performance,
)
from etf_momentum.signals import (
    PRICE_MAX_STALE_DAYS,
    MomentumConfig,
    observed_freshness,
    rank_momentum,
)


def verify_g01() -> dict:
    out: dict = {}
    xnys = cli._exchange_sessions("SPY")
    xkrx = cli._exchange_sessions("069500.KS")

    # 1. 5 / 6 / 8 session gap boundary & normal current observation on XNYS
    sessions_slice = xnys[(xnys >= "2025-01-02") & (xnys <= "2025-09-30")]
    as_of = pd.Timestamp("2025-09-30")
    cfg = MomentumConfig(lookbacks=(21, 42, 63), weights=(0.3, 0.3, 0.4), skip_days=5, sma_window=20, volatility_window=20)

    boundary_results = {}
    for gap_sessions in (5, 6, 8):
        # Last observation of A is at sessions_slice[-(gap_sessions + 1)]
        # Then (gap_sessions - 1) sessions have NO row in the panel at all,
        # and on the final session (2025-09-30), SPY and BIL have a row while A is NaN.
        last_obs_date = sessions_slice[-(gap_sessions + 1)]
        dropped_sessions = sessions_slice[-gap_sessions:-1]
        n = len(sessions_slice)
        panel = pd.DataFrame(
            {
                "A": 100.0 * np.power(1.003, np.arange(n)),
                "B": 100.0 * np.power(1.001, np.arange(n)),
                "SPY": 100.0 * np.power(1.0005, np.arange(n)),
                "BIL": np.full(n, 100.0),
            },
            index=sessions_slice,
        )
        panel.loc[dropped_sessions, :] = np.nan
        panel.loc[as_of, "A"] = np.nan
        raw = panel.dropna(how="all")
        prices = raw.ffill()
        obs = raw.notna()

        # Independent calculation:
        # Row age in `raw.index`: `last_obs_date` is at index -2, `as_of` is at index -1 -> row_age = 1
        indep_row_age = int(raw.index.get_loc(as_of) - raw.index.get_loc(last_obs_date))
        indep_session_age = int(
            xnys.searchsorted(as_of, side="right") - xnys.searchsorted(last_obs_date, side="right")
        )
        indep_effective_age = max(indep_row_age, indep_session_age)
        indep_fresh = indep_effective_age <= PRICE_MAX_STALE_DAYS

        last_actual, fresh_actual = observed_freshness(obs["A"], raw.index, sessions=xnys)
        last_nocals, fresh_nocals = observed_freshness(obs["A"], raw.index, sessions=None)

        ranking = rank_momentum(
            prices,
            as_of=as_of,
            benchmark="SPY",
            config=cfg,
            candidate_tickers=["A", "B"],
            observed=obs,
            sessions=xnys,
        )
        row_a = ranking.set_index("ticker").loc["A"]
        bt_cfg = BacktestConfig(
            top_n=1,
            candidate_tickers=("A", "B"),
            momentum=cfg,
            benchmark="SPY",
            cash_ticker="BIL",
        )
        sel = select_holdings(ranking, prices, as_of, bt_cfg, observed=obs, sessions=xnys)

        boundary_results[f"gap_{gap_sessions}_sessions"] = {
            "last_obs_expected": str(last_obs_date.date()),
            "last_obs_actual": str(pd.Timestamp(last_actual).date()),
            "indep_row_age": indep_row_age,
            "indep_session_age": indep_session_age,
            "indep_fresh": indep_fresh,
            "actual_fresh_with_sessions": bool(fresh_actual),
            "actual_fresh_without_sessions_fallback": bool(fresh_nocals),
            "rank_price_fresh": bool(row_a["price_fresh"]),
            "rank_eligible": bool(row_a["eligible"]),
            "selected_tickers": sel.tickers,
            "matches_expected": (
                pd.Timestamp(last_actual) == last_obs_date
                and bool(fresh_actual) == indep_fresh
                and bool(row_a["price_fresh"]) == indep_fresh
                and bool(row_a["eligible"]) == indep_fresh
                and (sel.tickers == (["A"] if indep_fresh else ["B"]))
                and bool(fresh_nocals) is True  # row_age is 1 <= 5 when sessions=None
            ),
        }

    # Normal current observation after a 7-session panel gap (observed on as_of = 2025-09-30)
    dropped_7 = sessions_slice[-8:-1]
    n = len(sessions_slice)
    panel_resumed = pd.DataFrame(
        {
            "A": 100.0 * np.power(1.003, np.arange(n)),
            "B": 100.0 * np.power(1.001, np.arange(n)),
            "SPY": 100.0 * np.power(1.0005, np.arange(n)),
            "BIL": np.full(n, 100.0),
        },
        index=sessions_slice,
    )
    panel_resumed.loc[dropped_7, :] = np.nan
    raw_resumed = panel_resumed.dropna(how="all")
    last_res, fresh_res = observed_freshness(raw_resumed["A"].notna(), raw_resumed.index, sessions=xnys)
    boundary_results["normal_current_observation_on_as_of"] = {
        "last_obs_expected": str(as_of.date()),
        "last_obs_actual": str(pd.Timestamp(last_res).date()),
        "fresh_expected": True,
        "fresh_actual": bool(fresh_res),
        "matches_expected": pd.Timestamp(last_res) == as_of and bool(fresh_res) is True,
    }
    out["boundary_5_6_8_and_normal"] = boundary_results

    # 2. Required assets (benchmark, cash, market filter) & row_age > session_age vs session_age > row_age
    req_results = {}
    for gap_sessions, should_pass in ((5, True), (6, False)):
        dropped = sessions_slice[-gap_sessions:-1]
        panel = pd.DataFrame(
            {"SPY": 100.0, "BIL": 100.0, "FLT": 100.0},
            index=sessions_slice,
        )
        panel.loc[dropped, :] = np.nan
        panel.loc[as_of, "FLT"] = np.nan
        raw = panel.dropna(how="all")
        obs = raw.notna()
        try:
            cli._require_fresh(obs, {"benchmark": "SPY", "cash": "BIL", "market filter 1": "FLT"}, xnys)
            passed = True
        except SystemExit:
            passed = False
        req_results[f"require_fresh_{gap_sessions}_sessions"] = {
            "expected_pass": should_pass,
            "actual_pass": passed,
            "matches_expected": passed == should_pass,
        }

    # Row age > session age test (e.g. stray weekend/holiday rows in price index)
    five_sessions = xnys[(xnys >= "2025-09-22") & (xnys <= "2025-09-26")]  # 5 sessions: Mon..Fri
    extra_weekend = pd.DatetimeIndex(["2025-09-20", "2025-09-21", "2025-09-27", "2025-09-28"])
    mixed_index = five_sessions.union(extra_weekend).sort_values()
    # Last observed on 2025-09-20: followed by 2025-09-21 + 5 sessions + 2 weekend days = 8 rows, but only 5 NYSE sessions
    mask_mixed = pd.Series(False, index=mixed_index)
    mask_mixed.loc["2025-09-20"] = True
    _, fresh_mixed = observed_freshness(mask_mixed, mixed_index, sessions=xnys)
    req_results["row_age_exceeds_session_age"] = {
        "row_age": 8,
        "session_age": 5,
        "expected_fresh": False,
        "actual_fresh": bool(fresh_mixed),
        "matches_expected": bool(fresh_mixed) is False,
    }

    # KR holiday vs missing rows test:
    # 2026-06-03 (local election) and 2026-07-17 (Constitution Day) are removed from XKRX sessions,
    # and Chuseok 2025-10-03..2025-10-09 are XKRX holidays.
    kr_oct = xkrx[(xkrx >= "2025-09-25") & (xkrx <= "2025-10-20")]
    # Between 2025-10-02 (Thu) and 2025-10-10 (Fri), 2025-10-03..10-09 are all KRX holidays (0 sessions in between, so 10-10 is 1 session after 10-02)
    idx_kr = pd.DatetimeIndex(["2025-10-02", "2025-10-10"])
    mask_kr = pd.Series([True, False], index=idx_kr)
    _, fresh_kr_holiday = observed_freshness(mask_kr, idx_kr, sessions=xkrx)
    _, fresh_us_calendar_on_same_dates = observed_freshness(mask_kr, idx_kr, sessions=xnys)
    req_results["kr_chuseok_holiday_vs_us_sessions"] = {
        "kr_sessions_after_2025_10_02_to_10_10": int(xkrx.searchsorted("2025-10-10", side="right") - xkrx.searchsorted("2025-10-02", side="right")),
        "us_sessions_after_2025_10_02_to_10_10": int(xnys.searchsorted("2025-10-10", side="right") - xnys.searchsorted("2025-10-02", side="right")),
        "kr_fresh_expected": True,  # 1 KRX session <= 5
        "kr_fresh_actual": bool(fresh_kr_holiday),
        "us_fresh_expected": False,  # 6 NYSE sessions > 5
        "us_fresh_actual": bool(fresh_us_calendar_on_same_dates),
        "kr_special_holidays_excluded": (
            pd.Timestamp("2026-06-03") not in xkrx and pd.Timestamp("2026-07-17") not in xkrx
            and pd.Timestamp("2026-06-03") in xnys and pd.Timestamp("2026-07-17") in xnys
        ),
        "matches_expected": (
            bool(fresh_kr_holiday) is True
            and bool(fresh_us_calendar_on_same_dates) is False
            and pd.Timestamp("2026-06-03") not in xkrx
            and pd.Timestamp("2026-07-17") not in xkrx
        ),
    }
    out["required_and_holidays"] = req_results

    # 3. Command propagation across rank, weekly, backtest (and month-end selection vs latest row)
    # Build a panel from 2025-01-02 to 2025-10-07 where X has a 6-session gap at 2025-09-30
    # (only 3 rows in panel after 2025-09-22 because 2025-09-25, 26, 29 have no rows for any ticker),
    # and then X is observed again on 2025-10-07.
    days_full = xnys[(xnys >= "2025-01-02") & (xnys <= "2025-10-07")]
    n_full = len(days_full)
    aug_end = np.searchsorted(days_full, pd.Timestamp("2025-08-29"))
    x_prices = 100.0 * np.exp(np.clip(np.arange(n_full) - aug_end, 0, None) * 0.012)
    b_prices = 100.0 * np.exp(np.arange(n_full) * 0.0015)
    spy_prices = 100.0 * np.exp(np.arange(n_full) * 0.0005)
    bil_prices = np.full(n_full, 100.0)
    panel_cmd = pd.DataFrame({"X": x_prices, "B": b_prices, "SPY": spy_prices, "BIL": bil_prices}, index=days_full)
    no_row_dates = pd.DatetimeIndex(["2025-09-25", "2025-09-26", "2025-09-29"])
    x_nan_dates = pd.DatetimeIndex(["2025-09-23", "2025-09-24", "2025-09-30"])
    panel_cmd.loc[x_nan_dates, "X"] = np.nan
    panel_cmd_dropped = panel_cmd.drop(no_row_dates)

    # At 2025-09-30: X was last observed 2025-09-22 -> 6 NYSE sessions elapsed, 3 rows in panel_cmd_dropped
    # Check backtest at 2025-09-30 rebalance
    bt_cfg = BacktestConfig(top_n=1, candidate_tickers=("X", "B"), momentum=cfg, benchmark="SPY", cash_ticker="BIL")
    _, bt_pos_with_sess, _ = run_backtest(panel_cmd_dropped.ffill(), bt_cfg, observed=panel_cmd_dropped.notna(), sessions=xnys)
    _, bt_pos_no_sess, _ = run_backtest(panel_cmd_dropped.ffill(), bt_cfg, observed=panel_cmd_dropped.notna(), sessions=None)
    bt_pick_with_sess = bt_pos_with_sess.loc[pd.to_datetime(bt_pos_with_sess["rebalance_date"]) == "2025-09-30", "tickers"].iloc[0]
    bt_pick_no_sess = bt_pos_no_sess.loc[pd.to_datetime(bt_pos_no_sess["rebalance_date"]) == "2025-09-30", "tickers"].iloc[0]

    # Check CLI `rank` and `weekly` end-to-end with mocked yf.download
    with tempfile.TemporaryDirectory(dir="/private/tmp/etf-orca-g01-g03-20261017/gemini") as tmpdir:
        tmp = Path(tmpdir)
        univ_path = tmp / "universe.csv"
        pd.DataFrame(
            {
                "ticker": ["X", "B", "SPY", "BIL"],
                "name": ["X", "B", "SPY", "BIL"],
                "category": ["Sector", "Sector", "Bench", "Cash"],
                "enabled": [1, 1, 1, 1],
            }
        ).to_csv(univ_path, index=False)

        # 3a. rank at --end 2025-10-08 (latest is 2025-10-07, month-end selection is 2025-09-30)
        yf_resp = panel_cmd_dropped.copy()
        yf_resp.columns = pd.MultiIndex.from_product([["Close"], list(yf_resp.columns)])
        with patch.object(data.yf, "download", return_value=yf_resp):
            argv_rank = [
                "etf-momentum", "rank",
                "--universe", str(univ_path),
                "--benchmark", "SPY", "--cash", "BIL",
                "--top-n", "1", "--lookback-years", "1", "--end", "2025-10-08",
                "--lookbacks", "21,42,63", "--skip-days", "5", "--sma-window", "20", "--volatility-window", "20",
                "--output-dir", str(tmp / "out_rank"), "--cache-dir", str(tmp / "cache_rank"),
            ]
            with patch.object(sys, "argv", argv_rank), contextlib.redirect_stdout(io.StringIO()):
                cli.main()
            sel_df = pd.read_csv(tmp / "out_rank" / "latest_selection.csv")
            rank_selected = sel_df.loc[sel_df["status"] == "selected", "ticker"].tolist()
            rank_excluded = sel_df.loc[sel_df["status"] == "excluded", ["ticker", "reason"]].to_dict("records")

            # 3b. weekly at --end 2025-10-01 (as_of is 2025-09-30 where X is stale by 6 sessions)
            yf_resp_sep = panel_cmd_dropped.loc[:"2025-09-30"].copy()
            yf_resp_sep.columns = pd.MultiIndex.from_product([["Close"], list(yf_resp_sep.columns)])
        with patch.object(data.yf, "download", return_value=yf_resp_sep):
            argv_weekly = [
                "etf-momentum", "weekly",
                "--universe", str(univ_path),
                "--benchmark", "SPY", "--cash", "BIL",
                "--lookback-years", "1", "--end", "2025-10-01",
                "--lookbacks", "21,42,63", "--skip-days", "5", "--sma-window", "20", "--volatility-window", "20",
                "--output-dir", str(tmp / "out_weekly"), "--cache-dir", str(tmp / "cache_weekly"),
            ]
            with patch.object(sys, "argv", argv_weekly), contextlib.redirect_stdout(io.StringIO()):
                cli.main()
            weekly_ranking = pd.read_csv(tmp / "out_weekly" / "latest_ranking.csv").set_index("ticker")

    out["command_propagation"] = {
        "backtest_2025_09_30_with_sessions": bt_pick_with_sess,
        "backtest_2025_09_30_without_sessions_fallback": bt_pick_no_sess,
        "rank_month_end_2025_09_30_selected": rank_selected,
        "rank_month_end_2025_09_30_excluded": rank_excluded,
        "weekly_2025_09_30_X_price_fresh": bool(weekly_ranking.loc["X", "price_fresh"]),
        "weekly_2025_09_30_X_eligible": bool(weekly_ranking.loc["X", "eligible"]),
        "matches_expected": (
            bt_pick_with_sess == "B"
            and bt_pick_no_sess == "X"
            and rank_selected == ["B"]
            and any(r["ticker"] == "X" and "2025-09-22" in str(r["reason"]) for r in rank_excluded)
            and bool(weekly_ranking.loc["X", "price_fresh"]) is False
            and bool(weekly_ranking.loc["X", "eligible"]) is False
        ),
    }
    return out


def verify_g02() -> dict:
    out: dict = {}
    days = pd.to_datetime(["2024-02-05", "2024-02-06", "2024-02-07", "2024-02-08"])
    tickers = ["069500.KS", "A.KS", "LATE1.KS", "LATE2.KS"]

    def make_yf(cols: dict[str, list[float]]) -> pd.DataFrame:
        df = pd.DataFrame(cols, index=days)
        df.columns = pd.MultiIndex.from_product([["Close"], list(cols.keys())])
        return df

    naver_map = {
        "069500.KS": pd.Series([1000.0, 1010.0, 1020.0, 1030.0], index=days),
        "A.KS": pd.Series([2000.0, 2020.0, 2040.0, 2060.0], index=days),
        # LATE1.KS has Yahoo adjusted closes = 0.5 * raw closes, with 2024-02-06 and 2024-02-07 missing in Yahoo
        "LATE1.KS": pd.Series([1000.0, 1040.0, 1080.0, 1100.0], index=days),
    }

    def naver_ok(t, s, e):
        return naver_map[t]

    def naver_fail(t, s, e):
        raise OSError("Simulated Naver timeout")

    with tempfile.TemporaryDirectory(dir="/private/tmp/etf-orca-g01-g03-20261017/gemini") as tmpdir:
        tmp = Path(tmpdir)
        cpath = data._cache_path(tickers, "2024-02-01", "2024-02-09", tmp, prefix=data.PRICE_CACHE_PREFIX)

        with patch.object(data, "NAVER_FILL", True):
            # Step 1: Initial pinned download with LATE1.KS and LATE2.KS missing
            yf_initial = make_yf(
                {
                    "069500.KS": [1000.0, 1010.0, 1020.0, 1030.0],
                    "A.KS": [1000.0, 1010.0, 1020.0, 1030.0],
                    "LATE1.KS": [np.nan] * 4,
                    "LATE2.KS": [np.nan] * 4,
                }
            )
            with patch.object(data.yf, "download", return_value=yf_initial), patch.object(naver, "naver_closes", side_effect=naver_ok), warnings.catch_warnings(record=True) as w1:
                warnings.simplefilter("always")
                df1 = data.download_prices(tickers, start="2024-02-01", end="2024-02-09", cache_dir=tmp, raw=True)

            step1_ok = (
                sorted(df1.columns.tolist()) == ["069500.KS", "A.KS"]
                and data._recorded_missing(cpath) == ["LATE1.KS", "LATE2.KS"]
                and data._fill_record_path(cpath).exists()
            )

            # Step 2: Plant stale retry leftovers in .retry/, then run retry where Yahoo returns LATE1.KS with gaps
            # but Naver fails!
            retry_dir = tmp / ".retry"
            retry_dir.mkdir(exist_ok=True)
            retry_cache = data._cache_path(["LATE1.KS", "LATE2.KS"], "2024-02-01", "2024-02-09", retry_dir, prefix=data.PRICE_CACHE_PREFIX)
            pd.DataFrame({"LATE1.KS": [999.0, 999.0, 999.0, 999.0]}, index=days).to_csv(retry_cache)
            data._fill_record_path(retry_cache).write_text(json.dumps({"version": data.NAVER_FILL_VERSION, "filled": {}}))
            data._missing_record_path(retry_cache).write_text(json.dumps({"missing": ["LATE2.KS"]}))

            yf_retry1 = make_yf({"LATE1.KS": [500.0, np.nan, np.nan, 550.0], "LATE2.KS": [np.nan] * 4})
            with patch.object(data.yf, "download", return_value=yf_retry1), patch.object(naver, "naver_closes", side_effect=naver_fail), warnings.catch_warnings(record=True) as w2:
                warnings.simplefilter("always")
                df2 = data.download_prices(tickers, start="2024-02-01", end="2024-02-09", cache_dir=tmp, raw=True)

            disk_cols_after_fail = pd.read_csv(cpath, index_col=0).columns.tolist()
            missing_after_fail = data._recorded_missing(cpath)
            step2_ok = (
                sorted(df2.columns.tolist()) == ["069500.KS", "A.KS"]
                and sorted(disk_cols_after_fail) == ["069500.KS", "A.KS"]
                and missing_after_fail == ["LATE1.KS", "LATE2.KS"]
                and not retry_cache.exists()
            )

            # Step 3: Next run, Naver is back! LATE1.KS is filled and merged; LATE2.KS stays in .missing.json
            with patch.object(data.yf, "download", return_value=yf_retry1), patch.object(naver, "naver_closes", side_effect=naver_ok), warnings.catch_warnings(record=True) as w3:
                warnings.simplefilter("always")
                df3 = data.download_prices(tickers, start="2024-02-01", end="2024-02-09", cache_dir=tmp, raw=True)

            # Independent calculation of filled values for LATE1.KS:
            # Known before gap (2024-02-05): Yahoo 500.0 / Naver 1000.0 = 0.5
            # Known after gap (2024-02-08): Yahoo 550.0 / Naver 1100.0 = 0.5
            # Filled 2024-02-06 = 1040.0 * 0.5 = 520.0; 2024-02-07 = 1080.0 * 0.5 = 540.0
            expected_late1 = [500.0, 520.0, 540.0, 550.0]
            actual_late1 = df3["LATE1.KS"].tolist()
            fill_rec = json.loads(data._fill_record_path(cpath).read_text())
            missing_after_partial_recovery = data._recorded_missing(cpath)
            step3_ok = (
                np.allclose(actual_late1, expected_late1, rtol=0, atol=1e-12)
                and fill_rec["filled"].get("LATE1.KS") == ["2024-02-06", "2024-02-07"]
                and fill_rec["filled_days"] == 2
                and missing_after_partial_recovery == ["LATE2.KS"]
            )

            # Step 4: Latest download (`end=None`) with partial failure is NOT cached at all
            tmp_latest = tmp / "latest_test"
            tmp_latest.mkdir()
            with patch.object(data.yf, "download", return_value=yf_initial), patch.object(naver, "naver_closes", side_effect=naver_ok), warnings.catch_warnings(record=True) as w4:
                warnings.simplefilter("always")
                df_latest = data.download_prices(tickers, start="2024-02-01", end=None, cache_dir=tmp_latest, raw=True)
            latest_files = list(tmp_latest.glob("*"))
            step4_ok = sorted(df_latest.columns.tolist()) == ["069500.KS", "A.KS"] and len(latest_files) == 0

    out["pinned_partial_retry_and_latest"] = {
        "step1_initial_partial_cache_ok": step1_ok,
        "step2_naver_fail_and_leftover_rejected_ok": step2_ok,
        "step3_recovery_and_partial_merge_ok": step3_ok,
        "expected_late1_closes": expected_late1,
        "actual_late1_closes": actual_late1,
        "missing_after_partial_recovery": missing_after_partial_recovery,
        "step4_latest_partial_not_cached_ok": step4_ok,
        "matches_expected": step1_ok and step2_ok and step3_ok and step4_ok,
    }
    return out


def verify_normal_regression_and_local_data() -> dict:
    out: dict = {}
    # 1. Direct momentum arithmetic check
    idx = pd.bdate_range("2024-01-01", periods=300)
    n = np.arange(len(idx))
    p_a = 100.0 * np.power(1.0018, n)
    p_b = 100.0 * np.power(1.0007, n)
    p_spy = 100.0 * np.power(1.0010, n)
    prices = pd.DataFrame({"A": p_a, "B": p_b, "SPY": p_spy, "BIL": 100.0}, index=idx)
    cfg = MomentumConfig(lookbacks=(63, 126, 252), weights=(0.3, 0.3, 0.4), skip_days=21, sma_window=200, volatility_window=63)
    ranking = rank_momentum(prices, benchmark="SPY", config=cfg, candidate_tickers=["A", "B"])
    row_a = ranking.set_index("ticker").loc["A"]

    # Independent formula:
    anchor_idx = -1 - 21
    r_63 = p_a[anchor_idx] / p_a[anchor_idx - 63] - 1.0
    r_126 = p_a[anchor_idx] / p_a[anchor_idx - 126] - 1.0
    r_252 = p_a[anchor_idx] / p_a[anchor_idx - 252] - 1.0
    w_mom = 0.3 * r_63 + 0.3 * r_126 + 0.4 * r_252
    sma_200 = p_a[-200:].mean()
    trend_str = p_a[-1] / sma_200 - 1.0
    daily_ret_a = p_a[1:] / p_a[:-1] - 1.0
    vol_a = float(pd.Series(daily_ret_a[-63:]).std(ddof=1) * math.sqrt(252))
    ra_score = w_mom / max(vol_a, 0.05)

    out["momentum_formula"] = {
        "expected_weighted_momentum": float(w_mom),
        "actual_weighted_momentum": float(row_a["weighted_momentum"]),
        "abs_diff_momentum": abs(float(w_mom) - float(row_a["weighted_momentum"])),
        "expected_trend_strength": float(trend_str),
        "actual_trend_strength": float(row_a["trend_strength_vs_sma"]),
        "expected_risk_adjusted_score": float(ra_score),
        "actual_risk_adjusted_score": float(row_a["risk_adjusted_score"]),
        "matches_expected": (
            abs(float(w_mom) - float(row_a["weighted_momentum"])) < 1e-12
            and abs(float(trend_str) - float(row_a["trend_strength_vs_sma"])) < 1e-12
            and abs(float(ra_score) - float(row_a["risk_adjusted_score"])) < 1e-12
        ),
    }

    # 2. Direct holding accounting & initial transaction cost arithmetic on a small price array
    # With 4 prior rows (2023-09-29..2023-12-29), 2024-01-31 is the 5th row (min_history_rows) and Rebalance 0!
    small_dates = pd.to_datetime(
        [
            "2023-09-29", "2023-10-31", "2023-11-30", "2023-12-29",
            "2024-01-31",  # Rebalance 0 (5th row -> first date in valid_index)
            "2024-02-15",  # Holding day 1 of period 0
            "2024-02-29",  # Holding day 2 of period 0 & Rebalance 1
            "2024-03-29",  # Holding day 1 of period 1
        ]
    )
    small_prices = pd.DataFrame(
        {
            "A": [102.0, 104.0, 106.0, 108.0, 110.0, 121.0, 133.1, 133.1],  # +10%, +10% in Feb, 0% in Mar
            "B": [101.0, 102.0, 103.0, 104.0, 105.0, 105.0, 94.5, 103.95],  # 0%, -10% in Feb, +10% in Mar
            "SPY": [101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0, 108.0],
            "BIL": [100.0] * 8,
        },
        index=small_dates,
    )
    small_mom = MomentumConfig(lookbacks=(1, 2, 3), weights=(0.3, 0.3, 0.4), skip_days=0, sma_window=3, volatility_window=3)
    # At 2024-01-31, both A and B are eligible; top_n=2 equal weight -> w_A = 0.5, w_B = 0.5.
    # Initial traded_notional = |0.5 - 0| + |0.5 - 0| = 1.0, turnover = 0.5, cost (at 10 bps) = 1.0 * 0.001 = 0.001.
    # Holding period 0 (2024-02-15, 2024-02-29):
    # Day 1 (2024-02-15): A grows 1.1, B grows 1.0 -> portfolio value before cost = 0.5*1.1 + 0.5*1.0 = 1.05 -> r_1 = 0.05 - 0.001 = 0.049.
    # Day 2 (2024-02-29): A cumulative growth 1.21, B cumulative growth 0.9 -> portfolio value = 0.5*1.21 + 0.5*0.9 = 0.605 + 0.45 = 1.055.
    # Buy-and-hold Day 2 return = 1.055 / 1.05 - 1 = 0.004761904761904808.
    # Drifted weights at 2024-02-29: w_A_end = 0.605 / 1.055 = 0.5734597156398105, w_B_end = 0.45 / 1.055 = 0.4265402843601896.
    # At 2024-02-29 rebalance: B dropped -10%, so B's momentum is negative and below 3-SMA ((105+105+94.5)/3 = 101.5 > 94.5)!
    # Thus at 2024-02-29 only A is eligible -> 1 of 2 slots filled -> target weights: A = 0.5, BIL = 0.5, B = 0.0!
    # Traded notional at 2024-02-29 = |0.5 - 0.605/1.055| + |0.0 - 0.45/1.055| + |0.5 - 0.0|
    # = (0.605/1.055 - 0.5) + 0.45/1.055 + 0.5 = 1.0! Turnover = 0.5, cost = 0.001.
    # Period 1 (2024-03-29): A return = 0%, BIL return = 0% -> raw return = 0.0, minus cost 0.001 = -0.001.
    # Cumulative strategy equity:
    # 2024-01-31: 1.0
    # 2024-02-15: 1.049
    # 2024-02-29: 1.049 * (1.055 / 1.05) = 1.0539952380952382
    # 2024-03-29: 1.0539952380952382 * (1 - 0.001) = 1.052941242857143
    bt_small_cfg = BacktestConfig(
        top_n=2,
        rebalance="M",
        cash_ticker="BIL",
        benchmark="SPY",
        transaction_cost_bps=10.0,
        weighting="equal",
        accounting="buy_and_hold",
        momentum=small_mom,
        candidate_tickers=("A", "B"),
    )
    eq_bh, pos_bh, met_bh = run_backtest(small_prices, bt_small_cfg)
    exp_eq_bh = [1.0, 1.049, 1.049 * (1.055 / 1.05), 1.049 * (1.055 / 1.05) * 0.999]

    # Also check daily_rebalanced on the same small_prices:
    # Day 1: 0.5*0.1 + 0.5*0.0 - 0.001 = 0.049
    # Day 2: 0.5*0.1 + 0.5*(-0.1) = 0.0
    # At 2024-02-29 rebalance: previous_weights = [A:0.5, B:0.5, BIL:0.0], new weights = [A:0.5, B:0.0, BIL:0.5]
    # Traded notional = |0.5-0.5| + |0-0.5| + |0.5-0| = 1.0, cost = 0.001.
    # Day 3 (2024-03-29): 0.0 - 0.001 = -0.001
    # Cumulative equity daily_rebalanced: [1.0, 1.049, 1.049, 1.049 * 0.999 = 1.047951]
    eq_dr, pos_dr, met_dr = run_backtest(small_prices, BacktestConfig(**{**bt_small_cfg.__dict__, "accounting": "daily_rebalanced"}))
    exp_eq_dr = [1.0, 1.049, 1.049, 1.049 * 0.999]

    out["holding_and_cost_accounting"] = {
        "buy_and_hold_expected": exp_eq_bh,
        "buy_and_hold_actual": eq_bh["strategy"].tolist(),
        "daily_rebalanced_expected": exp_eq_dr,
        "daily_rebalanced_actual": eq_dr["strategy"].tolist(),
        "total_cost_expected": 0.002,
        "total_cost_actual": float(met_bh["total_transaction_cost"]),
        "matches_expected": bool(
            np.allclose(eq_bh["strategy"].tolist(), exp_eq_bh, rtol=0, atol=1e-12)
            and np.allclose(eq_dr["strategy"].tolist(), exp_eq_dr, rtol=0, atol=1e-12)
            and abs(float(met_bh["total_transaction_cost"]) - 0.002) < 1e-12
        ),
    }

    # 3. Local cache verification & data availability check
    repo = Path("/Users/dc_macair/Documents/Research/etf_momentum_strategy")
    data_dir = repo / "data"
    p60_path = data_dir / "prices_raw_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv"
    tv60_path = data_dir / "traded_value_60_tickers_24ef6177bb30b87c_2010-01-01_2026-10-07.csv"
    p788_path = data_dir / "prices_raw_788_tickers_1d20b3b9c5fefa8f_2010-01-01_2026-10-07.csv"
    p788_missing_path = data_dir / "prices_raw_788_tickers_1d20b3b9c5fefa8f_2010-01-01_2026-10-07.csv.missing.json"
    tv788_candidates = list(data_dir.glob("traded_value_788_*"))

    univ_all = pd.read_csv(repo / "config/universe_kr_all.csv")
    univ_60 = pd.read_csv(repo / "config/universe_kr_pension.csv")
    tv60_cols = set(pd.read_csv(tv60_path, index_col=0, nrows=1).columns)
    p788_cols = set(pd.read_csv(p788_path, index_col=0, nrows=1).columns)
    overlap_788_tv = sorted(set(univ_all["ticker"]) & tv60_cols)

    # Also verify local 60-ETF fixed cache at 2026-09-30 (month-end) with KRX sessions
    raw_60 = pd.read_csv(p60_path, index_col=0, parse_dates=True)
    tv_60 = pd.read_csv(tv60_path, index_col=0, parse_dates=True)
    obs_60 = raw_60.notna()
    prices_60 = raw_60.ffill(limit=PRICE_MAX_STALE_DAYS)
    as_of_60 = pd.Timestamp("2026-09-30")
    hist_60 = prices_60.loc[:as_of_60]
    obs_hist_60 = obs_60.loc[:as_of_60]
    tv_hist_60 = tv_60.reindex(index=hist_60.index, columns=hist_60.columns).where(obs_hist_60)
    krx_sessions_60 = cli._exchange_sessions("069500.KS")
    candidates_60 = tuple(t for t in univ_60["ticker"] if t not in ("357870.KS",))
    rk_60 = rank_momentum(
        hist_60,
        config=MomentumConfig(min_avg_traded_value=1_000_000_000.0),
        candidate_tickers=candidates_60,
        traded_value=tv_hist_60,
        observed=obs_hist_60,
        sessions=krx_sessions_60,
    )
    top1_ticker = str(rk_60.iloc[0]["ticker"])
    s_top1 = prices_60.loc[:as_of_60, top1_ticker].dropna()
    exp_top1_m3 = s_top1.iloc[-22] / s_top1.iloc[-85] - 1.0
    exp_top1_m6 = s_top1.iloc[-22] / s_top1.iloc[-148] - 1.0
    exp_top1_m12 = s_top1.iloc[-22] / s_top1.iloc[-274] - 1.0
    exp_top1_wmom = 0.3 * exp_top1_m3 + 0.3 * exp_top1_m6 + 0.4 * exp_top1_m12

    out["local_data_limits"] = {
        "p60_exists": p60_path.exists(),
        "tv60_exists": tv60_path.exists(),
        "p788_exists": p788_path.exists(),
        "p788_columns": len(p788_cols),
        "p788_recorded_missing": json.loads(p788_missing_path.read_text())["missing"] if p788_missing_path.exists() else [],
        "tv788_exists": len(tv788_candidates) > 0,
        "univ_788_count": len(univ_all),
        "univ_788_with_local_traded_value": len(overlap_788_tv),
        "univ_788_missing_local_traded_value": len(set(univ_all["ticker"]) - tv60_cols),
        "univ_60_count": len(univ_60),
        "local_60_as_of_2026_09_30_top1": top1_ticker,
        "local_60_top1_expected_wmom": float(exp_top1_wmom),
        "local_60_top1_actual_wmom": float(rk_60.iloc[0]["weighted_momentum"]),
        "local_60_top1_wmom_diff": abs(float(exp_top1_wmom) - float(rk_60.iloc[0]["weighted_momentum"])),
        "local_60_top5": rk_60.head(5)[["ticker", "rank", "eligible", "weighted_momentum", "risk_adjusted_score"]].to_dict(orient="records"),
    }
    return out


if __name__ == "__main__":
    results = {
        "g01": verify_g01(),
        "g02": verify_g02(),
        "normal_and_local": verify_normal_regression_and_local_data(),
    }
    out_path = Path("/private/tmp/etf-orca-g01-g03-20261017/gemini/verification_results.json")
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(json.dumps(results, indent=2, ensure_ascii=False))
