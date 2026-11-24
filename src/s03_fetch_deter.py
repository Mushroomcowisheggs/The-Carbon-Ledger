"""DETER near-real-time deforestation alerts (INPE), Amazônia and Cerrado.

PRODES is an annual inventory: it can tell you that 2021 lost more forest than
2020, but not when.  DETER is the monthly alert stream behind it, published as
WFS layers on the same GeoServer.  It is the only series here with monthly
resolution, which is what makes a fire / alert / dry-season comparison possible.

Coverage is a hard property of the service, not a query artefact:
  deter-amz:deter_amz            2016-08 -> 2026-11   469 368 features
  deter-cerrado-nb:deter_cerrado  2018-05 -> 2026-11   136 625 features
There is **no DETER layer for the Atlantic Forest** on this GeoServer (checked
against both WFS and WMS capabilities), so that biome has no monthly series and
the pipeline says so instead of substituting PRODES and calling it DETER.

Three WFS quirks drive the implementation, all verified against the service:

  * `resultType=hits` returns 0 on WFS 1.0.0 regardless of filter - it has to be
    1.1.0 or 2.0.0.  Version 2.0.0 is used for the counts.
  * `view_date BETWEEN a AND b` returns 0, while the half-open pair
    `view_date>='a' AND view_date<'b'` works.  That is the filter used.
  * **paging is not stable**: a 50 000-row page boundary can repeat rows from the
    previous page (a year fetch of 52 908 returned 50 000 rows with only 40 114
    distinct `gid`).  `gid` is not unique either - the same `gid` appears with
    different view dates in different years - so de-duplicating on it silently
    halves the dataset.  The fetcher therefore partitions by **year and month**
    (each month is under one page), and de-duplicates on the full attribute
    tuple, which keeps the result exact.

Attributes are pulled without geometry (`propertyName=`), which keeps 606 k
alerts inside a few tens of MB.

Outputs
  data/interim/deter_alerts.csv        one row per alert (no geometry)
  data/interim/deter_biome_month.csv   biome x year x month counts and alert area
  data/interim/deter_layer_counts.csv  yearly feature counts per layer (validation)
"""
from __future__ import annotations

import calendar
import csv
import io
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import DATA_VINTAGE_YEAR, INTERIM, TB, USER_AGENT  # noqa: E402

# layer -> (biome tag, first year to probe)
LAYERS = {
    "deter-amz:deter_amz": ("Amazonia", 2016),
    "deter-cerrado-nb:deter_cerrado": ("Cerrado", 2018),
}
PROPS_COMMON = ["gid", "classname", "view_date", "areauckm", "areamunkm",
                "uf", "municipality"]
PROPS_EXTRA = {"deter-cerrado-nb:deter_cerrado": ["areatotalkm"]}
PAGE = 50_000

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def hits(namespace: str, layer: str, cql: str | None) -> int | None:
    """WFS 2.0.0 hit count (1.0.0 silently returns 0 on this server)."""
    params = {"service": "WFS", "version": "2.0.0", "request": "GetFeature",
              "typeNames": layer, "resultType": "hits"}
    if cql:
        params["CQL_FILTER"] = cql
    for attempt in range(3):
        try:
            r = SESSION.get(f"{TB}/{namespace}/ows", params=params, timeout=120)
            r.raise_for_status()
            for key in ('numberMatched="', 'numberOfFeatures="'):
                if key in r.text:
                    return int(r.text.split(key)[1].split('"')[0])
            return None
        except Exception as exc:  # noqa: BLE001
            print(f"    hits retry {attempt+1}: {type(exc).__name__}", flush=True)
            time.sleep(3)
    return None


def _page(namespace: str, layer: str, props: list[str], cql: str,
          start: int) -> tuple[list[dict], int]:
    params = {"service": "WFS", "version": "1.0.0", "request": "GetFeature",
              "typeName": layer, "outputFormat": "csv",
              "propertyName": ",".join(props), "maxFeatures": PAGE,
              "startIndex": start}
    if cql:
        params["CQL_FILTER"] = cql
    r = SESSION.get(f"{TB}/{namespace}/ows", params=params, timeout=900, stream=True)
    r.raise_for_status()
    r.raw.decode_content = True
    text = io.TextIOWrapper(r.raw, encoding="utf-8", errors="replace", newline="")
    out, header = [], None
    for row in csv.reader(text):
        if header is None:
            header = [c.strip().lower() for c in row]
            continue
        if len(row) < len(header):
            continue
        out.append(dict(zip(header, [c.strip() for c in row])))
    return out, len(out)


def fetch_partition(namespace: str, layer: str, props: list[str],
                    cql: str, label: str) -> list[dict]:
    rows: list[dict] = []
    start = 0
    while True:
        try:
            page, n = _page(namespace, layer, props, cql, start)
        except Exception as exc:  # noqa: BLE001
            print(f"    {label} start={start}: {type(exc).__name__} "
                  f"{str(exc)[:60]}", flush=True)
            time.sleep(10)
            page, n = _page(namespace, layer, props, cql, start)
        rows.extend(page)
        start += n
        if n < PAGE:
            break
    return rows


def main() -> int:
    cached = [INTERIM / "deter_alerts.csv",
              INTERIM / "deter_biome_month.csv",
              INTERIM / "deter_layer_counts.csv"]
    if all(p.exists() and p.stat().st_size > 0 for p in cached):
        print("[local] deter: reusing bundled alert tables; no WFS request")
        return 0
    all_rows: list[dict] = []
    counts: list[dict] = []
    for layer, (biome, first_year) in LAYERS.items():
        ns = layer.split(":")[0]
        props = PROPS_COMMON + PROPS_EXTRA.get(layer, [])
        print(f"\n=== {layer} ({biome}) ===", flush=True)
        total = hits(ns, layer, None)
        print(f"  layer total: {total:,}" if total is not None else "  total: n/a",
              flush=True)
        counts.append(dict(biome=biome, layer=layer, year="all", n_features=total))

        fetched, year_rows = 0, 0
        for year in range(first_year, DATA_VINTAGE_YEAR + 1):
            y_cql = f"view_date>='{year}-01-01' AND view_date<'{year+1}-01-01'"
            y_hits = hits(ns, layer, y_cql)
            counts.append(dict(biome=biome, layer=layer, year=year,
                               n_features=y_hits))
            if not y_hits:
                print(f"  {year}: 0 alerts", flush=True)
                continue
            for month in range(1, 13):
                nxt_year, nxt_month = (year + 1, 1) if month == 12 else (year, month + 1)
                cql = (f"view_date>='{year}-{month:02d}-01' AND "
                       f"view_date<'{nxt_year}-{nxt_month:02d}-01'")
                got = fetch_partition(ns, layer, props, cql,
                                      f"{biome} {year}-{month:02d}")
                for rec in got:
                    rec["biome"] = biome
                    all_rows.append(rec)
                fetched += len(got)
                year_rows += len(got)
            flag = "" if (y_hits is None or year_rows == y_hits) else "  [MISMATCH]"
            print(f"  {year}: {y_hits:,} alerts, fetched {year_rows:,}{flag}",
                  flush=True)
            year_rows = 0
        print(f"  fetched {fetched:,} rows for {biome}", flush=True)

    df = pd.DataFrame(all_rows)
    if df.empty:
        print("[warn] no DETER rows returned")
        return 0
    for c in ("areauckm", "areamunkm", "areatotalkm"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df["view_date"] = pd.to_datetime(df["view_date"], errors="coerce")
    df["year"] = df["view_date"].dt.year
    df["month"] = df["view_date"].dt.month
    # de-duplicate on the full attribute tuple: no single attribute is unique on
    # this layer, but two real alerts cannot share date + class + area + place
    key = ["biome", "gid", "classname", "view_date", "areamunkm", "uf"]
    keep = [c for c in ("biome", "gid", "classname", "view_date", "year", "month",
                        "areauckm", "areamunkm", "areatotalkm", "uf",
                        "municipality") if c in df.columns]
    before = len(df)
    df = df.drop_duplicates(key)[keep].sort_values(["biome", "view_date"])
    print(f"\nalerts: {before:,} fetched -> {len(df):,} after de-duplication")
    df.to_csv(INTERIM / "deter_alerts.csv", index=False)
    print(f"wrote deter_alerts.csv ({(INTERIM / 'deter_alerts.csv').stat().st_size/1e6:.1f} MB)")

    area_col = "areamunkm" if "areamunkm" in df.columns else None
    agg = {"n_alerts": ("gid", "size")}
    if area_col:
        agg["alert_area_km2"] = (area_col, "sum")
    bm = df.groupby(["biome", "year", "month"]).agg(**agg).reset_index()
    bm.to_csv(INTERIM / "deter_biome_month.csv", index=False)
    pd.DataFrame(counts).to_csv(INTERIM / "deter_layer_counts.csv", index=False)
    print(f"biome-month rows: {len(bm):,}")
    print(bm.groupby("biome")[["n_alerts"]].sum().to_string())
    print("\nyearly totals after de-duplication:")
    print(df.groupby(["biome", "year"]).size().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
