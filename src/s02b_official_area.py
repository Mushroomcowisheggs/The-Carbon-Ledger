"""Exact official PRODES deforestation areas, year by year, plus the year inventory.

The WMS rasterisation in s02 preserves the *shape* of a deforestation front but
inflates the *amount*: at a ~2 km pixel every PRODES polygon - median size well
under 1 km2 - is drawn at a minimum of one full pixel, which is a 3-5x area
error.  This script recovers the true totals from the same service without ever
downloading geometry: a WFS request with `propertyName=area_km` and
`outputFormat=csv` returns one line of attributes per polygon, which is streamed
and summed.

It sweeps **every** year in the published window, not a handful of epochs, for
two reasons: (a) the cumulative calibration in s05 needs the full annual series,
otherwise the cumulative total under-counts the years it skips; (b) the sweep is
also the authoritative inventory of which years each biome actually publishes,
which s02 then uses to decide what to rasterise.

`resultType=hits` is not usable here - this GeoServer instance answers 0 for it -
so a filtered attribute sweep with `maxFeatures=1` is what proves a year exists.

Sampling was tried first and rejected: `area_km` is extremely heavy-tailed
(median 0.08 km2, mean varies 1.5-5.0 km2 between chunks), so a 6 000-feature
sample gave totals that moved by a factor of three between chunks.  The full
attribute sweep is a few MB per layer and is exact.

Outputs
  data/interim/prodes_official_area.csv   biome, layer, year, n_features, total_km2
  data/interim/prodes_annual_full.csv     biome, year, n_features, total_km2 (yearly only)
  data/interim/prodes_year_sweep.json      incremental cache (makes reruns cheap)
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (BIOMES, INTERIM, PRODES_BASELINE_BY_STATE,  # noqa: E402
                    PRODES_YEAR_MAX, PRODES_YEAR_MIN, TB, USER_AGENT)

PAGE = 50_000   # GeoServer's default WFS feature cap; larger values are ignored
CACHE = INTERIM / "prodes_year_sweep.json"


def _stream_page(ns: str, layer: str, cql: str | None,
                 start: int, page: int) -> tuple[int, float]:
    params = {"service": "WFS", "version": "1.0.0", "request": "GetFeature",
              "typeName": layer, "outputFormat": "csv",
              "propertyName": "area_km", "maxFeatures": page,
              "startIndex": start}
    if cql:
        params["CQL_FILTER"] = cql
    r = requests.get(f"{TB}/{ns}/ows", params=params,
                     headers={"User-Agent": USER_AGENT}, timeout=1800, stream=True)
    r.raise_for_status()
    r.raw.decode_content = True
    text = io.TextIOWrapper(r.raw, encoding="utf-8", errors="replace", newline="")
    n, total, idx = 0, 0.0, None
    for row in csv.reader(text):
        if idx is None:                      # header
            try:
                idx = [c.strip().lower() for c in row].index("area_km")
            except ValueError:
                idx = -1
            continue
        n += 1
        if idx >= 0 and idx < len(row):
            try:
                total += float(row[idx])
            except (TypeError, ValueError):
                pass
    return n, total


def stream_area(ns: str, layer: str, cql: str | None,
                tag: str = "") -> tuple[int, float]:
    """Exact (n_features, total_km2), paging past the 50 000-feature WFS cap.

    The Mata Atlantica accumulated layer carries ~1.1 M polygons and GeoServer's
    gateway returns 504 on a deep offset under load (observed at startIndex
    200 000).  So a page is retried in place, with a smaller page size, instead
    of the whole sweep restarting; `start` never advances past rows already
    counted, which keeps the sum exact.
    """
    n_total, area_total, start = 0, 0.0, 0
    page = PAGE
    while True:
        n, tot = None, None
        for attempt in range(5):
            try:
                n, tot = _stream_page(ns, layer, cql, start, page)
                break
            except Exception as exc:  # noqa: BLE001
                wait = 15 * (attempt + 1)
                print(f"    {tag} start={start:,} page={page:,}: "
                      f"{type(exc).__name__} -> retry in {wait}s", flush=True)
                time.sleep(wait)
                page = max(5_000, page // 2)
        if n is None:
            raise SystemExit(f"stream_area failed: {ns} {layer} cql={cql} "
                             f"at start={start}")
        n_total += n
        area_total += tot
        if n < page:
            break
        start += n
        if page < PAGE:
            page = min(PAGE, page * 2)
    return n_total, area_total


def load_cache() -> dict:
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


UF = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS",
      "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR", "RS", "SC",
      "SE", "SP", "TO"]


def hits_total(ns: str, layer: str, cql: str | None = None) -> int | None:
    """Feature count from WFS 2.0.0 (1.0.0 answers 0 on this server)."""
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
        print(f"    hits {layer}: {type(exc).__name__} {str(exc)[:60]}")
    return None


def baseline_area(ns: str, layer: str, tag: str) -> tuple[int, float]:
    """Exact area of an accumulated layer, by state partition when needed."""
    total_hits = hits_total(ns, layer)
    n_sum, a_sum = 0, 0.0
    for uf in UF:
        n, a = stream_area(ns, layer, f"state='{uf}'", tag=f"{tag} {uf}")
        n_sum += n
        a_sum += a
        if n:
            print(f"      {uf}: {n:>7,} feats  {a:>9,.0f} km2", flush=True)
    n_null, a_null = stream_area(ns, layer, "state IS NULL", tag=f"{tag} NULL")
    n_sum += n_null
    a_sum += a_null
    if n_null:
        print(f"      (no state): {n_null:,} feats  {a_null:,.0f} km2", flush=True)
    if total_hits is not None and abs(n_sum - total_hits) > max(50, 0.001 * total_hits):
        print(f"    [warn] state partition incomplete "
              f"({n_sum:,} of {total_hits:,}) - falling back to a full sweep",
              flush=True)
        return stream_area(ns, layer, None, tag=f"{tag} full")
    print(f"    state partition adds up: {n_sum:,} of {total_hits:,} features",
          flush=True)
    return n_sum, a_sum


def main() -> int:
    cache = load_cache()
    rows: list[dict] = []

    def flush() -> None:
        """Persist after every step: a 20-minute sweep must survive a gateway 504."""
        df = pd.DataFrame(rows)
        df.to_csv(INTERIM / "prodes_official_area.csv", index=False)
        yearly = df[df.layer == "yearly"]
        yearly[["biome", "year", "n_features", "total_km2"]].to_csv(
            INTERIM / "prodes_annual_full.csv", index=False)

    for tag, cfg in BIOMES.items():
        ns = cfg["workspace"]
        t0 = time.time()

        key = f"{tag}|baseline|{cfg['baseline_year']}"
        if key in cache:
            n, tot = cache[key]
            print(f"{tag:14s} baseline {cfg['baseline_year']}  [cached] "
                  f"n={n:>8,} total={tot:>10,.0f} km2", flush=True)
        elif tag in PRODES_BASELINE_BY_STATE:
            print(f"{tag:14s} baseline {cfg['baseline_year']}  "
                  f"(state-partitioned sweep)", flush=True)
            n, tot = baseline_area(ns, cfg["baseline"], f"{tag} baseline")
            cache[key] = [n, tot]
            CACHE.write_text(json.dumps(cache), encoding="utf-8")
        else:
            n, tot = stream_area(ns, cfg["baseline"], None, tag=f"{tag} baseline")
            cache[key] = [n, tot]
            CACHE.write_text(json.dumps(cache), encoding="utf-8")
        rows.append(dict(biome=tag, layer="baseline", year=cfg["baseline_year"],
                         n_features=n, total_km2=tot))
        flush()

        empty_run = 0
        for yr in range(PRODES_YEAR_MIN, PRODES_YEAR_MAX + 1):
            key = f"{tag}|yearly|{yr}"
            if key in cache:
                n, tot = cache[key]
            else:
                n, tot = stream_area(ns, cfg["yearly"], f"year={yr}",
                                     tag=f"{tag} {yr}")
                cache[key] = [n, tot]
                CACHE.write_text(json.dumps(cache), encoding="utf-8")
            rows.append(dict(biome=tag, layer="yearly", year=yr,
                             n_features=n, total_km2=tot))
            flush()
            if n == 0:
                empty_run += 1
            else:
                empty_run = 0
            flag = "" if n else "  (not published)"
            print(f"{tag:14s} {yr}            n={n:>8,}  total={tot:>10,.0f} km2"
                  f"{flag}", flush=True)
            # stop probing after 3 consecutive empty years beyond the last data
            if empty_run >= 3 and yr >= PRODES_YEAR_MAX - 2:
                break
        print(f"  {tag}: {time.time()-t0:.0f}s", flush=True)

    df = pd.DataFrame(rows)
    yearly = df[df.layer == "yearly"].copy()
    yearly[["biome", "year", "n_features", "total_km2"]].to_csv(
        INTERIM / "prodes_annual_full.csv", index=False)

    print("\nyear inventory (published increments only):")
    for tag, g in yearly.groupby("biome"):
        pub = g[g.n_features > 0]
        print(f"  {tag:14s} {len(pub):2d} years: "
              f"{', '.join(str(int(y)) for y in pub.year)}  "
              f"sum={pub.total_km2.sum():,.0f} km2")
    print(f"\nwrote prodes_official_area.csv ({len(df)} rows), "
          f"prodes_annual_full.csv ({len(yearly)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
