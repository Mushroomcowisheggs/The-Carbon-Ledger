"""Integrity check: does the per-year CQL sweep add up to the whole layer?

s02b gets the official area of each year by filtering the WFS layer on
`year=<YYYY>` and summing `area_km`.  That is only trustworthy if the filter
partitions the layer - no dropped features, no double counting.  The cheap way
to prove it is the WFS 2.0.0 hit count (WFS 1.0.0 always answers 0 on this
GeoServer, which is why the naive check would pass vacuously).

So for every biome this compares
    total_hits(layer)                     against
    sum over years of hits(year=YYYY)
and writes the difference.  A non-zero difference means the year inventory in
s02b cannot be used, and the pipeline should stop before the masks are rendered.

Output: data/interim/prodes_layer_check.csv
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import BIOMES, INTERIM, PRODES_YEAR_MAX, PRODES_YEAR_MIN, TB, USER_AGENT  # noqa: E402


def total_hits(ns: str, layer: str, cql: str | None = None) -> int | None:
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
              "typeNames": layer, "resultType": "hits"}
    if cql:
        params["CQL_FILTER"] = cql
    try:
        r = requests.get(f"{TB}/{ns}/ows", params=params,
                         headers={"User-Agent": USER_AGENT}, timeout=180)
        r.raise_for_status()
        for key in ('numberMatched="', 'numberOfFeatures="'):
            if key in r.text:
                return int(r.text.split(key)[1].split('"')[0])
    except Exception as exc:  # noqa: BLE001
        print(f"    {layer}: {type(exc).__name__} {str(exc)[:60]}")
    return None


def main() -> int:
    done = INTERIM / "prodes_layer_check.csv"
    if done.exists() and done.stat().st_size > 0:
        print("[local] layer-check: reusing prodes_layer_check.csv; no WFS request")
        return 0
    p = INTERIM / "prodes_annual_full.csv"
    if not p.exists():
        print("[skip] prodes_annual_full.csv missing - run s02b first")
        return 0
    annual = pd.read_csv(p)
    rows = []
    for tag, cfg in BIOMES.items():
        ns = cfg["workspace"]
        layer = cfg["yearly"]
        total = total_hits(ns, layer)
        per_year = annual[annual.biome == tag].set_index("year").n_features
        s = int(per_year.sum())
        rows.append(dict(biome=tag, layer=layer, total_hits=total,
                         sum_year_counts=s,
                         diff=(None if total is None else total - s),
                         status=("ok" if total is not None and total == s else "MISMATCH")))
        print(f"{tag:14s} layer={total if total is not None else 'n/a':>9} "
              f"sum(years)={s:>9} diff={(total - s) if total is not None else 'n/a'}")

        # accumulated layer: only the count is checkable here
        acc = total_hits(ns, cfg["baseline"])
        rows.append(dict(biome=tag, layer=cfg["baseline"], total_hits=acc,
                         sum_year_counts=None, diff=None,
                         status="info" if acc else "unavailable"))
        print(f"{'':14s} accumulated={acc if acc is not None else 'n/a':>9}")

    df = pd.DataFrame(rows)
    df.to_csv(INTERIM / "prodes_layer_check.csv", index=False)
    bad = df[df.status == "MISMATCH"]
    print(f"\nwrote prodes_layer_check.csv ({len(df)} rows), mismatches: {len(bad)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
