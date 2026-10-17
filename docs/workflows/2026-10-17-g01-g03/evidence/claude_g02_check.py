"""G02 current behaviour: pinned partial cache retry with a failed / recovered Naver fill, leftover retry
files, and the latest (no --end) partial policy. Yahoo and Naver are mocked; caches in a temp dir."""
import json
import shutil
import sys
import warnings
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from etf_momentum import data, naver

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "/private/tmp/etf-orca-g01-g03-20261017/claude/g02")
shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True)
ix = pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"])
T = ["A.KS", "NEW.KS"]


def yahoo(cols):
    f = pd.DataFrame(cols, index=ix)
    f.columns = pd.MultiIndex.from_product([["Close"], list(cols)])
    return f


def ok(t, s, e):
    return pd.Series([100.0, 110.0, 120.0], index=ix)


def down(t, s, e):
    raise OSError("Naver temporarily unavailable")


calls = []


def dl(cols):
    return lambda tickers, *a, **k: calls.append(list(tickers)) or yahoo(cols)


def run(cache, end="2024-01-05", **kw):
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        frame = data.download_prices(T, "2024-01-01", end, cache_dir=cache, raw=True, **kw)
    return frame, [str(x.message) for x in w]


res = {}
with patch.object(data, "NAVER_FILL", True):
    for scenario in ("fail_then_recover", "leftover_retry_files"):
        cache = OUT / scenario
        cp = data._cache_path(T, "2024-01-01", "2024-01-05", cache, prefix=data.PRICE_CACHE_PREFIX)
        steps = {}
        with patch.object(data, "_yf_download", side_effect=dl({"A.KS": [100.0, 110.0, 120.0], "NEW.KS": [np.nan] * 3})), patch.object(naver, "naver_closes", side_effect=ok):
            f, w = run(cache, refresh=True)
        steps["1_initial"] = {"columns": list(f.columns), "missing_record": data._recorded_missing(cp), "warnings": w}
        if scenario == "leftover_retry_files":
            rc = data._cache_path(["NEW.KS"], "2024-01-01", "2024-01-05", cache / ".retry", prefix=data.PRICE_CACHE_PREFIX)
            rc.parent.mkdir(exist_ok=True)
            pd.DataFrame({"NEW.KS": [1.0, 1.0, 1.0]}, index=ix).to_csv(rc)
            data._fill_record_path(rc).write_text(json.dumps({"version": data.NAVER_FILL_VERSION, "filled": {}}))
            steps["1b_leftover_written"] = str(rc.relative_to(OUT))
        calls.clear()
        with patch.object(data, "_yf_download", side_effect=dl({"NEW.KS": [100.0, np.nan, 120.0]})), patch.object(naver, "naver_closes", side_effect=down):
            f, w = run(cache)
        cached_cols = list(pd.read_csv(cp, index_col=0).columns)
        steps["2_retry_naver_down"] = {"yahoo_requests": list(calls), "returned_columns": list(f.columns), "cached_columns": cached_cols,
                                       "missing_record": data._recorded_missing(cp), "warnings": w}
        calls.clear()
        with patch.object(data, "_yf_download", side_effect=dl({"NEW.KS": [100.0, np.nan, 120.0]})), patch.object(naver, "naver_closes", side_effect=ok):
            f, w = run(cache)
        rec = json.loads(data._fill_record_path(cp).read_text())
        steps["3_retry_naver_back"] = {"yahoo_requests": list(calls), "NEW.KS": f["NEW.KS"].tolist() if "NEW.KS" in f else None,
                                       "missing_record_exists": data._missing_record_path(cp).exists(), "fill_record_NEW": rec["filled"].get("NEW.KS"), "warnings": w}
        calls.clear()
        with patch.object(data, "_yf_download", side_effect=lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected download"))):
            f, w = run(cache)
        steps["4_complete_cache_reused"] = {"columns": list(f.columns), "warnings": w}
        res[scenario] = steps

    cache = OUT / "latest_partial"
    with patch.object(data, "_yf_download", side_effect=dl({"A.KS": [100.0, 110.0, 120.0], "NEW.KS": [np.nan] * 3})), patch.object(naver, "naver_closes", side_effect=ok):
        f, w = run(cache, end=None, refresh=True)
    res["latest_partial"] = {"columns": list(f.columns), "cache_files": sorted(p.name for p in cache.glob("*")), "warnings": w}
print(json.dumps(res, indent=1, ensure_ascii=False))
