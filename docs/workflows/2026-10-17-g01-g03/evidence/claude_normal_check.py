"""Normal-input regression with hand formulas (not reusing implementation results as expectations)."""
import json

import numpy as np
import pandas as pd

from etf_momentum import cli
from etf_momentum.backtest import BacktestConfig, _holding_period, run_backtest
from etf_momentum.portfolio import PortfolioConfig, run_portfolio
from etf_momentum.signals import MomentumConfig, rank_momentum

out = {}
days = pd.bdate_range(end="2026-10-06", periods=700)
n = np.arange(len(days))
prices = pd.DataFrame({"A": 100 * 1.002**n, "B": 100 * 1.001**n, "BENCH": 100 * 1.0005**n}, index=days)
cfg = MomentumConfig()
sessions = pd.DatetimeIndex(days)
row = rank_momentum(prices, benchmark="BENCH", candidate_tickers=["A"], observed=prices.notna(), sessions=sessions).iloc[0]
# weighted momentum = sum w * (P[t-skip] / P[t-skip-L] - 1); t = last row, skip = 21
t = len(days) - 1
expected = sum(w * (1.002 ** (t - cfg.skip_days) / 1.002 ** (t - cfg.skip_days - L) - 1) for w, L in zip(cfg.weights, cfg.lookbacks))
out["momentum"] = {"lookbacks": list(cfg.lookbacks), "skip": cfg.skip_days, "weights": list(cfg.weights),
                   "expected": expected, "actual": float(row["weighted_momentum"]), "price_fresh": bool(row["price_fresh"])}
assert abs(expected - row["weighted_momentum"]) < 1e-12 and row["price_fresh"]

r = pd.DataFrame({"A": [0.10, -0.10], "B": [0.0, 0.0]}, index=pd.bdate_range("2020-01-01", periods=2))
w = pd.Series({"A": 0.5, "B": 0.5})
buy, drift = _holding_period(r, w, "buy_and_hold")
daily, _ = _holding_period(r, w, "daily_rebalanced")
out["buy_and_hold"] = {"expected": 0.5 * 1.1 * 0.9 + 0.5, "actual": float((1 + buy).prod()),
                       "end_weight_A_expected": 0.495 / 0.995, "end_weight_A_actual": float(drift["A"])}
out["daily_rebalanced"] = {"expected": 1.05 * 0.95, "actual": float((1 + daily).prod())}
assert np.isclose((1 + buy).prod(), 0.995, atol=1e-12) and np.isclose(drift["A"], 0.495 / 0.995, atol=1e-12)
assert np.isclose((1 + daily).prod(), 0.9975, atol=1e-12)

equity, _ = run_portfolio(pd.DataFrame({"A": [0.0, 0.0]}, index=pd.bdate_range("2020-01-02", periods=2)), PortfolioConfig({"A": 1}, rebalance_cost_bps=100))
out["portfolio_initial_cost"] = {"expected": [1.0, 0.99], "actual": [float(v) for v in equity]}
assert np.allclose(equity.to_numpy(), [1.0, 0.99], atol=1e-12)

# Backtest accounting: one candidate A at full weight from the first month end, no cost, buy-and-hold.
xnys = cli._exchange_sessions("SPY")
ix = xnys[(xnys >= "2024-01-02") & (xnys <= "2024-06-28")]
p = pd.DataFrame({"A": 100 * 1.003 ** np.arange(len(ix)), "SPY": 100 * 1.001 ** np.arange(len(ix)), "BIL": 100.0}, index=ix)
bt = BacktestConfig(top_n=1, candidate_tickers=("A",), benchmark="SPY", cash_ticker="BIL", transaction_cost_bps=0,
                    momentum=MomentumConfig(lookbacks=(5, 10, 15), skip_days=0, sma_window=5, volatility_window=5))
eq, pos, _ = run_backtest(p, bt, observed=p.notna(), sessions=xnys)
first = pd.Timestamp(pos["rebalance_date"].iloc[0])
expected_end = p["A"].iloc[-1] / p.loc[first, "A"]
out["backtest_full_weight"] = {"first_signal": str(first.date()), "expected_end": float(expected_end),
                               "actual_end": float(eq["strategy"].iloc[-1] / eq.loc[first, "strategy"])}
assert abs(out["backtest_full_weight"]["actual_end"] - expected_end) < 1e-10
print(json.dumps(out, indent=1))
