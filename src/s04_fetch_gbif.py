"""Fetch species occurrence records from GBIF (API) and clean them.

Four groups are pulled, chosen so the turnover result can be checked for
consistency across very different organisms rather than resting on one:

  primates (order, 798)   - large-bodied, well recorded, strong forest dependence
  palms    (family, 7681) - the plant counterpart used for corridor structure
  bats     (order, 734)   - vagile vertebrates, a natural control
  frogs    (order, 952)   - sedentary, moisture-dependent, most sensitive to edges

Records are restricted to Brazil, with coordinates.  The field list is wider than
in the first version: IUCN category, dataset, institution, elevation and event
date are kept so that (a) threatened species can be separated from unassessed
ones, and (b) recording effort can be modelled explicitly instead of being
waved away by "both periods must have >= 8 records".

Cleaning follows the usual sequence for this kind of data:
  * keep records inside the Brazilian envelope, drop (0, 0) and duplicates
  * drop unparsable / implausible years, keep 1970-2026
  * flag high coordinate uncertainty, fossil / living-specimen records
  * assign an analysis-grid cell and a time period
Nothing here "fixes" sampling bias - that is handled analytically in s06 by
restricting the turnover test to cells sampled in both periods *and* by
including effort as a covariate.

Paging: GBIF's deep paging is expensive (offset 12 000 took 57 s in a test while
offset 6 000 took 4 s), so the query is partitioned by year and, if a single
year still exceeds the 100 000 offset cap, by month as well.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (GBIF, GBIF_FIELDS, GBIF_MAX_OFFSET, GBIF_PAGE,  # noqa: E402
                    GBIF_TARGETS, GRID_RES, INTERIM, LAT_MAX, LON_MIN, RAW,
                    USER_AGENT)

OUT = RAW / "gbif"
OUT.mkdir(parents=True, exist_ok=True)
CACHE_VERSION = "v2"

BRAZIL = dict(lon_min=-74.5, lon_max=-33.0, lat_min=-34.5, lat_max=5.5)
# A year with more records than this is fetched in twelve month partitions, so
# no single query ever pages deep into GBIF's offset window.
MONTH_SPLIT_THRESHOLD = 2000
# GBIF records run up to the current year; the upper bound is kept generous so a
# later clock does not silently empty the sample.
PERIODS = [(1970, 1999, "pre-2000"), (2000, 2009, "2000s"),
           (2010, 2016, "2010-2016"), (2017, 2026, "2017-2026")]
YEAR_MAX = 2026

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def _get(params: dict, timeout: int = 180) -> dict | None:
    for attempt in range(5):
        try:
            r = SESSION.get(f"{GBIF}/occurrence/search", params=params, timeout=timeout)
            if r.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as exc:  # noqa: BLE001
            print(f"    retry {attempt+1}: {type(exc).__name__} {str(exc)[:60]}", flush=True)
            time.sleep(2 * (attempt + 1))
    return None


def year_facets(taxon_key: int) -> list[tuple[int, int]]:
    return _facets(taxon_key, "year", 400)


def _facets(taxon_key: int, name: str, limit: int,
            extra: dict | None = None) -> list[tuple[int, int]]:
    params = {"taxonKey": taxon_key, "country": "BR", "hasCoordinate": "true",
              "limit": 0, "facet": name, "facetLimit": limit}
    if extra:
        params.update(extra)
    js = _get(params)
    if not js:
        return []
    facets = js.get("facets") or []
    if not facets:
        return []
    out = []
    for c in facets[0]["counts"]:
        try:
            val = int(c["name"])
        except (TypeError, ValueError):
            continue
        if name == "year" and not (1900 <= val <= YEAR_MAX):
            continue
        out.append((val, int(c["count"])))
    return out


def _page(taxon_key: int, extra: dict, offset: int) -> dict | None:
    params = {"taxonKey": taxon_key, "country": "BR", "hasCoordinate": "true",
              "limit": GBIF_PAGE, "offset": offset}
    params.update(extra)
    return _get(params, timeout=120)


def _drain(taxon_key: int, extra: dict, count: int, label: str,
           rows: list[dict]) -> int:
    """Page one partition to exhaustion; returns rows collected."""
    offset, got = 0, 0
    while offset < count and offset < GBIF_MAX_OFFSET:
        js = _page(taxon_key, extra, offset)
        if js is None:
            break
        res = js.get("results") or []
        if not res:
            break
        for rec in res:
            rows.append({k: rec.get(k) for k in GBIF_FIELDS})
        offset += len(res)
        got += len(res)
        if len(res) < GBIF_PAGE:
            break
    if got:
        print(f"    {label}: {got:,}/{count:,}", flush=True)
    return got


def fetch_group(name: str, taxon_key: int) -> pd.DataFrame:
    cache = OUT / f"gbif_{name}_raw_{CACHE_VERSION}.csv"
    meta = OUT / f"gbif_{name}_raw_{CACHE_VERSION}.meta.json"
    if cache.exists() and cache.stat().st_size > 10_000 and meta.exists():
        info = json.loads(meta.read_text())
        df = pd.read_csv(cache, low_memory=False)
        if info.get("complete") or len(df) >= 0.98 * (info.get("count") or 0):
            print(f"[cache] {name}: {len(df):,} rows", flush=True)
            return df

    rows: list[dict] = []
    facets = year_facets(taxon_key)
    total = sum(c for _, c in facets)
    meta.write_text(json.dumps({"count": total, "complete": False}))
    print(f"{name}: {total:,} records across {len(facets)} year partitions", flush=True)
    for year, count in sorted(facets):
        if count > GBIF_MAX_OFFSET:
            # one year alone exceeds the offset cap: split by month
            got = 0
            for month, mcount in _facets(taxon_key, "month", 12, {"year": year}):
                got += _drain(taxon_key, {"year": year, "month": month},
                              mcount, f"{name} {year}-{month:02d}", rows)
            print(f"  {name} {year}: {got:,}/{count:,} (month partitions)", flush=True)
        elif count > MONTH_SPLIT_THRESHOLD:
            # deep offsets are expensive (a request at offset 12 000 took 57 s in
            # testing while offset 6 000 took 4 s), so big years are split by
            # month too, which keeps every offset shallow
            got = 0
            for month, mcount in _facets(taxon_key, "month", 12, {"year": year}):
                got += _drain(taxon_key, {"year": year, "month": month},
                              mcount, f"{name} {year}-{month:02d}", rows)
            print(f"  {name} {year}: {got:,}/{count:,} (month partitions)", flush=True)
        else:
            _drain(taxon_key, {"year": year}, count, f"{name} {year}", rows)
    df = pd.DataFrame(rows)
    df.to_csv(cache, index=False)
    meta.write_text(json.dumps({"count": total, "rows": len(df), "complete": True}))
    print(f"{name}: {len(df):,} rows cached", flush=True)
    return df


def clean(df: pd.DataFrame, group: str) -> pd.DataFrame:
    df = df.copy()
    df["group"] = group
    for c in ("decimalLatitude", "decimalLongitude", "year", "month",
              "coordinateUncertaintyInMeters", "elevation"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    n0 = len(df)
    df = df.dropna(subset=["decimalLatitude", "decimalLongitude", "year"])
    n1 = len(df)
    df = df[df["year"].between(PERIODS[0][0], YEAR_MAX)]
    n_year = len(df)
    df = df[~((df["decimalLatitude"].abs() < 0.01) & (df["decimalLongitude"].abs() < 0.01))]
    df = df[df["decimalLatitude"].between(BRAZIL["lat_min"], BRAZIL["lat_max"])
            & df["decimalLongitude"].between(BRAZIL["lon_min"], BRAZIL["lon_max"])]
    n2 = len(df)
    df = df.drop_duplicates(["group", "species", "decimalLatitude",
                             "decimalLongitude", "year"])
    n3 = len(df)
    df["uncertainty_km"] = df["coordinateUncertaintyInMeters"] / 1000.0
    df["high_uncertainty"] = df["uncertainty_km"].fillna(0) > 10
    df["is_specimen"] = df["basisOfRecord"].isin(
        ["PRESERVED_SPECIMEN", "MATERIAL_SAMPLE", "LIVING_SPECIMEN", "FOSSIL_SPECIMEN"])
    df["is_human_observation"] = df["basisOfRecord"].isin(
        ["HUMAN_OBSERVATION", "MACHINE_OBSERVATION", "OBSERVATION"])
    # grid cell (row/col in the global analysis grid)
    df["col"] = ((df["decimalLongitude"] - LON_MIN) / GRID_RES).astype(int)
    df["row"] = ((LAT_MAX - df["decimalLatitude"]) / GRID_RES).astype(int)
    df["cell"] = df["row"].astype(str) + "_" + df["col"].astype(str)
    df["decade"] = (df["year"] // 10 * 10).astype(int)
    bins = [PERIODS[0][0] - 1] + [p[1] for p in PERIODS]
    labels = [p[2] for p in PERIODS]
    df["period"] = pd.cut(df["year"], bins=bins, labels=labels)
    # IUCN category as published by GBIF (may be blank for unassessed species)
    if "iucnRedListCategory" in df.columns:
        df["iucn"] = df["iucnRedListCategory"].fillna("").astype(str).str.upper()
        df.loc[~df.iucn.isin(["EX", "EW", "CR", "EN", "VU", "NT", "LC", "DD"]), "iucn"] = ""
    else:
        df["iucn"] = ""
    print(f"{group}: {n0:,} raw -> {n1:,} with coord+year -> {n_year:,} year<=2026 "
          f"-> {n2:,} in Brazil -> {n3:,} after dedupe", flush=True)
    return df


def main() -> int:
    """Fetch, clean and cache one or more groups, then rebuild the combined file.

    Each group is written to its own cleaned CSV before the combined file is
    assembled, so the four fetches can run as independent jobs (or be resumed
    one at a time) without losing what already completed.
    """
    wanted = sys.argv[1:] or list(GBIF_TARGETS)
    for name in wanted:
        spec = GBIF_TARGETS[name]
        out = INTERIM / f"gbif_{name}_clean.csv"
        if out.exists() and out.stat().st_size > 100_000:
            print(f"[cached] {name}: {out.stat().st_size/1e6:.1f} MB", flush=True)
            continue
        raw = fetch_group(name, spec["taxonKey"])
        df = clean(raw, name)
        df.to_csv(out, index=False)
        print(f"{name}: {len(df):,} cleaned rows -> {out.name}", flush=True)

    frames = []
    for name in GBIF_TARGETS:
        p = INTERIM / f"gbif_{name}_clean.csv"
        if p.exists():
            frames.append(pd.read_csv(p, low_memory=False))
    if not frames:
        print("no cleaned group files yet")
        return 0
    occ = pd.concat(frames, ignore_index=True)
    if "key" in occ.columns:
        occ = occ.drop_duplicates("key")
    keep = [c for c in occ.columns if c not in ("issue", "scientificName",
                                                "acceptedScientificName")]
    occ = occ[keep]
    occ.to_csv(INTERIM / "gbif_occurrences.csv", index=False)
    print(f"\ntotal cleaned records: {len(occ):,}")
    print(occ.groupby(["group", "period"]).size().unstack(fill_value=0).to_string())
    print("\nunique species:", occ.groupby("group")["species"].nunique().to_dict())
    print("unique datasets:", occ["datasetKey"].nunique() if "datasetKey" in occ else "-")
    return 0


if __name__ == "__main__":
    sys.exit(main())
