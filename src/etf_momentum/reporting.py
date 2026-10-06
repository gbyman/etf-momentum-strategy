from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from etf_momentum import __version__
from etf_momentum.backtest import EXECUTION_ASSUMPTION


def ensure_output_dir(path: str | Path) -> Path:
    output_dir = Path(path)
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def save_json(data: dict[str, float], path: str | Path) -> None:
    formatted = {key: _json_float(value) for key, value in data.items()}
    Path(path).write_text(json.dumps(formatted, indent=2, sort_keys=True), encoding="utf-8")


def save_run_metadata(
    output_dir: str | Path,
    args: argparse.Namespace,
    prices: pd.DataFrame,
    traded_value: pd.DataFrame | None = None,
    extra: dict[str, object] | None = None,
    execution: str | None = None,
) -> Path:
    """Write <command>_run_config.json so a result file can be traced back to how it was produced.

    `extra` adds command-specific inputs (e.g. a portfolio spec and its input files); `execution`
    replaces the default momentum execution note for commands that trade differently.
    """
    arguments = {key: value for key, value in vars(args).items() if key != "func"}
    universe = Path(str(arguments.get("universe", "")))
    metadata = {
        "command": arguments.get("command"),
        "argv": sys.argv,
        "run_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "package_version": __version__,
        "code_revision": _code_revision(),
        "arguments": arguments,
        "universe_sha256": hashlib.sha256(universe.read_bytes()).hexdigest() if universe.is_file() else None,
        "price_data": {
            "first_date": str(pd.Timestamp(prices.index[0]).date()),
            "last_date": str(pd.Timestamp(prices.index[-1]).date()),
            "tickers": list(prices.columns),
        },
        "traded_value_data": _traded_value_section(prices, traded_value),
        "execution": execution or EXECUTION_ASSUMPTION,
        **(extra or {}),
    }
    path = Path(output_dir) / f"{metadata['command']}_run_config.json"
    path.write_text(json.dumps(metadata, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    return path


def _code_revision() -> dict[str, object] | None:
    """Git commit of the package source and whether the working tree had uncommitted changes."""
    root = Path(__file__).resolve().parent
    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        return None
    return {"commit": commit.stdout.strip(), "dirty": bool(status.stdout.strip())}


def _traded_value_section(prices: pd.DataFrame, traded_value: pd.DataFrame | None) -> dict[str, object] | None:
    if traded_value is None:
        return None
    covered = traded_value.dropna(axis=1, how="all")
    return {
        "first_date": str(pd.Timestamp(covered.index[0]).date()) if not covered.empty else None,
        "last_date": str(pd.Timestamp(covered.index[-1]).date()) if not covered.empty else None,
        "tickers": sorted(covered.columns),
        "missing_tickers": sorted(set(prices.columns) - set(covered.columns)),
    }


def save_equity_chart(equity: pd.DataFrame, path: str | Path) -> None:
    figure, axis = plt.subplots(figsize=(11, 6))
    equity.plot(ax=axis, linewidth=1.8)
    axis.set_title("ETF Momentum Strategy")
    axis.set_ylabel("Growth of $1")
    axis.grid(True, alpha=0.25)
    figure.tight_layout()
    figure.savefig(path, dpi=160)
    plt.close(figure)


def _json_float(value: float) -> float | None:
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    return round(float(value), 6)

