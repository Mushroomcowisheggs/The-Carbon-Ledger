"""INPE BDQueimadas active-fire foci, 2003-2025.

Why this dataset earns its size: GHGRP says nothing about land, PRODES says
nothing about the *mechanism* of clearing, and species records say nothing about
disturbance.  Fire foci are the one Brazilian series that is (a) daily,
(b) geolocated to 1 km, (c) tagged with biome and municipality, and runs 23
years.  About 4 million detections are enough to estimate a fire regime, not
just a year total.

Source (verified 2026-11: the old queimadas.dgi.inpe.br host is broken and
redirects to a 404; dataserver-coids.inpe.br serves the same data):

    https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/anual/
        Brasil_sat_ref/focos_br_ref_{YYYY}.zip

One reference satellite per year (`sat_ref`), which keeps within-year counts
comparable; across years the reference satellite changes, so a level shift in a
series can be an instrument change rather than a fire change - flagged in the
report, and cross-checked against the all-satellite variant in the validation
step.  Zip members are space-padded CSV, UTF-8, `data_pas` in local time UTC-3.

Outputs
  data/interim/fire_foci.csv              id, lat, lon, timestamp, state,
                                          municipality, biome (all detections)
  data/interim/fire_biome_month.csv       biome x state x year x month counts
  data/interim/fire_cell_year.csv         0.1 deg cell x year counts
"""
from __future__ import annotations

import csv
import io
import os
import sys
import time
import zipfile

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import FIRE_YEARS, GRID_RES, INTERIM, LAT_MAX, LON_MIN, RAW, USER_AGENT  # noqa: E402

OUT = RAW / "fire"
OUT.mkdir(parents=True, exist_ok=True)
URL = ("https://dataserver-coids.inpe.br/queimadas/queimadas/focos/csv/anual/"
       "Brasil_sat_ref/focos_br_ref_{year}.zip")
# 0.1 deg aggregation grid for the map layer
CELL = 0.1
CCOLS = int(round(50.0 / CELL))
CROWS = int(round(41.0 / CELL))   # LAT_MAX=6 .. LAT_MIN=-35


def download(year: int) -> object | None:
    dest = OUT / f"focos_br_ref_{year}.zip"
    if dest.exists() and dest.stat().st_size > 100_000:
        return dest
    url = URL.format(year=year)
    for attempt in range(4):
        try:
            with requests.get(url, timeout=(30, 600), stream=True,
                              headers={"User-Agent": USER_AGENT}) as r:
                if r.status_code == 404:
                    print(f"  [{year}] 404 - not published", flush=True)
                    return None
                r.raise_for_status()
                tmp = dest.with_suffix(".zip.part")
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
                tmp.replace(dest)
                print(f"  [{year}] {dest.stat().st_size/1e6:6.1f} MB", flush=True)
                return dest
        except Exception as exc:  # noqa: BLE001
            print(f"  [{year}] retry {attempt+1}: {type(exc).__name__} "
                  f"{str(exc)[:60]}", flush=True)
            time.sleep(3 * (attempt + 1))
    return None


def _f(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return np.nan


def parse_year(zip_path, writer, month_counts: dict, cell_counts: dict) -> int:
    """Stream one year's zip into the combined CSV plus in-memory aggregates."""
    n = 0
    with zipfile.ZipFile(zip_path) as z:
        member = z.namelist()[0]
        with z.open(member) as raw:
            text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            reader = csv.reader(text)
            header = next(reader)
            idx = {h.strip().lower(): i for i, h in enumerate(header)}
            i_id = idx.get("id_bdq")
            i_lat, i_lon = idx.get("lat"), idx.get("lon")
            i_dt = idx.get("data_pas")
            i_st, i_mu, i_bi = idx.get("estado"), idx.get("municipio"), idx.get("bioma")
            for row in reader:
                if len(row) <= i_lon:
                    continue
                lat, lon = _f(row[i_lat]), _f(row[i_lon])
                if not (np.isfinite(lat) and np.isfinite(lon)):
                    continue
                dt = row[i_dt].strip()
                try:
                    year = int(dt[:4]); month = int(dt[5:7]); hour = int(dt[11:13])
                except (ValueError, IndexError):
                    continue
                state = row[i_st].strip() if i_st is not None else ""
                muni = row[i_mu].strip() if i_mu is not None else ""
                biome = row[i_bi].strip() if i_bi is not None else ""
                writer.writerow([row[i_id].strip() if i_id is not None else "",
                                 f"{lat:.4f}", f"{lon:.4f}", dt, year, month, hour,
                                 state, muni, biome])
                n += 1
                key = (biome, state, year, month)
                month_counts[key] = month_counts.get(key, 0) + 1
                r = int((LAT_MAX - lat) / CELL)
                c = int((lon - LON_MIN) / CELL)
                if 0 <= r < CROWS and 0 <= c < CCOLS:
                    ck = (r, c, year)
                    cell_counts[ck] = cell_counts.get(ck, 0) + 1
    return n


def main() -> int:
    cached = [INTERIM / "fire_foci.csv",
              INTERIM / "fire_biome_month.csv",
              INTERIM / "fire_cell_year.csv"]
    if all(p.exists() and p.stat().st_size > 0 for p in cached):
        print("[local] fire: reusing bundled foci tables; no rebuild needed")
        return 0
    zi_path = INTERIM.parent / "raw" / "fire_build.json"
    import json
    done = json.loads(zi_path.read_text()) if zi_path.exists() else {}

    month_counts: dict = {}
    cell_counts: dict = {}
    total = 0
    dest = INTERIM / "fire_foci.csv"
    part = INTERIM / "fire_foci.csv.part"
    with open(part, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["foco_id", "lat", "lon", "timestamp", "year", "month", "hour",
                    "state", "municipality", "biome"])
        for year in FIRE_YEARS:
            t0 = time.time()
            zp = download(year)
            if zp is None:
                continue
            n = parse_year(zp, w, month_counts, cell_counts)
            total += n
            print(f"  {year}: {n:>8,} foci  ({time.time()-t0:.0f}s, running {total:,})",
                  flush=True)
    part.replace(dest)
    print(f"\nfire foci: {total:,} rows -> {dest.name} "
          f"({dest.stat().st_size/1e6:.1f} MB)")

    bm = pd.DataFrame([dict(biome=k[0], state=k[1], year=k[2], month=k[3], n_foci=v)
                       for k, v in month_counts.items()])
    bm.sort_values(["biome", "state", "year", "month"]).to_csv(
        INTERIM / "fire_biome_month.csv", index=False)
    print(f"biome-month rows: {len(bm):,}")

    cell = pd.DataFrame([dict(row=k[0], col=k[1], year=k[2], n_foci=v)
                         for k, v in cell_counts.items()])
    cell.to_csv(INTERIM / "fire_cell_year.csv", index=False)
    print(f"cell-year rows: {len(cell):,}")

    print("\nfoci by biome and year (top 6 biomes):")
    piv = (bm.groupby(["biome", "year"]).n_foci.sum().unstack(fill_value=0))
    print(piv.loc[piv.sum(axis=1).sort_values(ascending=False).index[:6]].to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
