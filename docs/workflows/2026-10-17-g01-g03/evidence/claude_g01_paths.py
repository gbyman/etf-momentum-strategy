"""G01 call-chain check below the top-level rank/weekly ranking.

Scenario (XNYS): month end 2025-09-30. Three sessions before it (09-25, 09-26, 09-29) have no row for any
ticker, so the benchmark/cash gap is 3 sessions (<= 5, the held/benchmark guard does not stop).
Ticker X also misses 09-23, 09-24 and 09-30 (rows exist for the others): rows after X's last close (09-22)
= 3, sessions = 6 > PRICE_MAX_STALE_DAYS. Data continue through 2025-10-31 with X closing again
from 10-01.

Case 1 (candidate): X is flat through August (not held), jumps in September. At the 09-30 signal X ranks
first on its stale price. Expected (rank policy): X stale -> excluded, B selected.
Case 2 (backtest market filter): X is the only risk-on filter, B the only candidate.
Expected: the backtest stops at the 09-30 signal (filter without a recent observation).
Case 3 (CLI rank, month-end selection): --end 2025-10-08, so the latest row (10-07) is fresh for X but
the previous month end 09-30 is not. Expected: month-end selection stops on the stale filter.
"""
import contextlib
import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from etf_momentum import cli, data
from etf_momentum.backtest import BacktestConfig, StaleDataError, run_backtest, select_holdings
from etf_momentum.signals import MomentumConfig, rank_momentum

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/private/tmp/etf-orca-g01-g03-20261017/claude/paths")
OUT.mkdir(parents=True, exist_ok=True)
XNYS = cli._exchange_sessions("SPY")
ss = XNYS[(XNYS >= "2025-01-02") & (XNYS <= "2025-10-31")]
n = np.arange(len(ss))
x = np.where(ss <= pd.Timestamp("2025-08-29"), 100.0, 100 * np.exp(np.clip(n - np.searchsorted(ss, pd.Timestamp("2025-08-29")), 0, None) * 0.01))
full = pd.DataFrame({"X": x, "B": 100 * np.exp(n * 0.001), "SPY": 100 * np.exp(n * 0.0005), "BIL": 100.0}, index=ss)
no_row = pd.DatetimeIndex(["2025-09-25", "2025-09-26", "2025-09-29"])
raw = full.copy()
raw.loc[pd.DatetimeIndex(["2025-09-23", "2025-09-24", "2025-09-30"]), "X"] = np.nan
raw = raw.drop(no_row)
obs = raw.notna()
m = MomentumConfig(lookbacks=(21, 42, 63), skip_days=0, sma_window=20, volatility_window=20)
out: dict = {
    "X_last_close_before_0930": "2025-09-22",
    "rows_after_until_0930": int(((raw.index > "2025-09-22") & (raw.index <= "2025-09-30")).sum()),
    "sessions_after_until_0930": int(((XNYS > "2025-09-22") & (XNYS <= "2025-09-30")).sum()),
}
me = pd.Timestamp("2025-09-30")
rk = rank_momentum(raw.ffill(), me, benchmark="SPY", config=m, candidate_tickers=["X", "B"], observed=obs, sessions=XNYS).set_index("ticker")
out["rank_momentum_0930_with_sessions"] = {t: {"price_fresh": bool(rk.loc[t, "price_fresh"]), "eligible": bool(rk.loc[t, "eligible"])} for t in ["X", "B"]}
rk0 = rank_momentum(raw.ffill(), me, benchmark="SPY", config=m, candidate_tickers=["X", "B"], observed=obs).set_index("ticker")
out["rank_momentum_0930_rows_only"] = {t: {"price_fresh": bool(rk0.loc[t, "price_fresh"]), "rank": int(rk0.loc[t, "rank"])} for t in ["X", "B"]}

# Case 1
cfg = BacktestConfig(top_n=1, candidate_tickers=("X", "B"), momentum=m, benchmark="SPY", cash_ticker="BIL")
try:
    _, pos, _ = run_backtest(raw.ffill(), cfg, observed=obs, sessions=XNYS)
    sel = pos[pd.to_datetime(pos["rebalance_date"]) >= "2025-08-29"][["rebalance_date", "tickers"]]
    out["case1_backtest_signals"] = sel.astype(str).to_dict("records")
except StaleDataError as exc:
    out["case1_backtest_error"] = str(exc)

# Case 2
cfg_f = BacktestConfig(top_n=1, candidate_tickers=("B",), momentum=m, benchmark="SPY", cash_ticker="BIL", risk_on_benchmark=True, risk_on_tickers=("X",))
try:
    _, pos, _ = run_backtest(raw.ffill(), cfg_f, observed=obs, sessions=XNYS)
    sel = pos[pd.to_datetime(pos["rebalance_date"]) >= "2025-08-29"][["rebalance_date", "tickers"]]
    out["case2_backtest_filter"] = {"result": "ran (no stop)", "signals": sel.astype(str).to_dict("records")}
except StaleDataError as exc:
    out["case2_backtest_filter"] = {"result": "stopped", "error": str(exc)}
try:
    select_holdings(rk.reset_index(), raw.ffill(), me, cfg_f, observed=obs)
    out["case2_select_holdings_no_sessions"] = "passed"
except StaleDataError as exc:
    out["case2_select_holdings_no_sessions"] = "stopped: " + str(exc)

# Case 3: CLI rank with month-end selection; Yahoo response mocked, temp cache/output
resp = raw.reindex(raw.index.union(no_row)).loc[:"2025-10-07"]
resp.columns = pd.MultiIndex.from_product([["Close"], list(resp.columns)])
u = OUT / "universe.csv"
pd.DataFrame({"ticker": ["X", "B", "SPY", "BIL"], "name": ["X", "B", "SPY", "BIL"], "category": ["eq", "eq", "eq", "cash"], "enabled": 1}).to_csv(u, index=False)
argv = ["etf-momentum", "rank", "--universe", str(u), "--benchmark", "SPY", "--cash", "BIL", "--top-n", "1",
        "--risk-on-benchmark", "--risk-on-ticker", "X", "--lookback-years", "1", "--end", "2025-10-08",
        "--lookbacks", "21,42,63", "--skip-days", "0", "--sma-window", "20", "--volatility-window", "20",
        "--output-dir", str(OUT / "rank"), "--cache-dir", str(OUT / "cache"), "--refresh"]
log = io.StringIO()
try:
    with patch.object(sys, "argv", argv), patch.object(data, "_yf_download", return_value=resp), contextlib.redirect_stdout(log):
        cli.main()
    s = pd.read_csv(OUT / "rank" / "latest_selection.csv")
    out["case3_cli_rank"] = {"result": "ran", "selection": s[["ticker", "status"]].astype(str).to_dict("records")}
except (SystemExit, StaleDataError) as exc:
    out["case3_cli_rank"] = {"result": "stopped", "error": f"{type(exc).__name__}: {exc}"}
(OUT / "rank_stdout.log").write_text(log.getvalue())
print(json.dumps(out, indent=1))
