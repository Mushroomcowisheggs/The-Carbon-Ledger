"""National / global CO2 and forest context from CSVs.

Two sources, both plain HTTP:

1. OWID / Global Carbon Budget country-year panel.  This is the only cheap way to
   put the US facility-level carbon series and the Brazilian land-use series on
   the same axis: it carries fossil CO2 *and* land-use-change CO2 per country per
   year, plus population and GDP, so per-capita and per-unit-GDP readings are
   available without another download.
2. World Bank indicators (forest area, protected-area share, renewable
   electricity share) for Brazil and the world aggregate - the official series
   the PRODES raster is then triangulated against.

Outputs
  data/interim/owid_co2.csv        country, year, co2, luc_co2, total_ghg, ...
  data/interim/wb_indicators.csv   country, indicator, year, value
"""
from __future__ import annotations

import os
import sys
import time

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (INTERIM, OWID_CO2_URL, RAW, USER_AGENT, WB_COUNTRIES,  # noqa: E402
                    WB_INDICATORS, WB_URL)

OUT = RAW / "national"
OUT.mkdir(parents=True, exist_ok=True)

OWID_COLS = ["country", "year", "iso_code", "population", "gdp", "co2",
             "co2_per_capita", "co2_per_gdp", "total_ghg", "methane",
             "nitrous_oxide", "land_use_change_co2", "cumulative_co2",
             "coal_co2", "oil_co2", "gas_co2", "cement_co2", "flaring_co2",
             "land_use_change_co2_per_capita", "temperature_change_from_co2"]


OWID_MIRRORS = [
    OWID_CO2_URL,
    "https://cdn.jsdelivr.net/gh/owid/co2-data@master/owid-co2-data.csv",
    "https://raw.githack.com/owid/co2-data/master/owid-co2-data.csv",
]


def _download_stream(url: str, dest, min_bytes: int = 1_000_000) -> bool:
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(4):
        try:
            with requests.get(url, timeout=(30, 300), stream=True,
                              headers={"User-Agent": USER_AGENT}) as r:
                r.raise_for_status()
                with open(tmp, "wb") as fh:
                    for chunk in r.iter_content(1 << 20):
                        fh.write(chunk)
            if tmp.stat().st_size >= min_bytes:
                tmp.replace(dest)
                print(f"[ok] {dest.name} {dest.stat().st_size/1e6:.1f} MB <- {url[:60]}")
                return True
            print(f"  [short] {url[:60]} -> {tmp.stat().st_size:,} bytes")
        except Exception as exc:  # noqa: BLE001
            print(f"  [retry {attempt+1}] {type(exc).__name__} {str(exc)[:70]}")
        time.sleep(3 * (attempt + 1))
    return False


def fetch_owid() -> pd.DataFrame:
    dest = OUT / "owid-co2-data.csv"
    if dest.exists() and dest.stat().st_size > 1_000_000:
        print(f"[skip] owid-co2-data.csv {dest.stat().st_size/1e6:.1f} MB")
    else:
        for url in OWID_MIRRORS:
            if _download_stream(url, dest):
                break
        else:
            raise SystemExit("OWID co2 panel unavailable from all mirrors")
    df = pd.read_csv(dest, low_memory=False)
    keep = [c for c in OWID_COLS if c in df.columns]
    df = df[keep]
    df = df[df.year >= 1850]
    # OWID carries regional aggregates (iso_code NaN or OWID_*); keep them but flag
    df["is_aggregate"] = df["iso_code"].isna() | df["iso_code"].astype(str).str.startswith("OWID")
    df.to_csv(INTERIM / "owid_co2.csv", index=False)
    print(f"owid panel: {len(df):,} rows, {df.country.nunique()} entities, "
          f"{int(df.year.min())}-{int(df.year.max())}")
    return df


def fetch_worldbank() -> pd.DataFrame:
    dest = INTERIM / "wb_indicators.csv"
    if dest.exists() and dest.stat().st_size > 0:
        print("[local] world bank: reusing wb_indicators.csv; no API request")
        return pd.read_csv(dest)
    rows = []
    for label, ind in WB_INDICATORS.items():
        url = WB_URL.format(countries=WB_COUNTRIES, ind=ind)
        js = None
        for attempt in range(4):
            try:
                r = requests.get(url, timeout=(30, 240),
                                 headers={"User-Agent": USER_AGENT})
                r.raise_for_status()
                js = r.json()
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  [retry {attempt+1}] world bank {label}: "
                      f"{type(exc).__name__} {str(exc)[:60]}")
                time.sleep(5 * (attempt + 1))
        if not isinstance(js, list) or len(js) < 2 or not js[1]:
            print(f"  [warn] world bank {label}: empty payload")
            continue
        for rec in js[1]:
            if rec.get("value") is None:
                continue
            rows.append(dict(indicator=label, country=rec["country"]["value"],
                             iso3=rec.get("countryiso3code"),
                             year=int(rec["date"]), value=float(rec["value"])))
        print(f"  world bank {label}: {len(js[1])} annual rows")
    df = pd.DataFrame(rows)
    df.to_csv(INTERIM / "wb_indicators.csv", index=False)
    print(f"world bank panel: {len(df):,} rows")
    return df


def main() -> int:
    fetch_owid()
    fetch_worldbank()
    return 0


if __name__ == "__main__":
    sys.exit(main())
