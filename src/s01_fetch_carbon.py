"""Fetch + clean the carbon layers.

Three sources:

1. US EPA GHGRP annual summary workbooks (2010-2023).  Every sheet that carries
   a facility id *and* coordinates is loaded, so the resulting table covers the
   whole "carbon boundary" that the programme actually observes:
      - direct  : on-site combustion / process emissions  (scope-1-like)
      - supply  : fossil-fuel suppliers, i.e. upstream supply-chain nodes
      - fugitive: petroleum & natural-gas systems, coal mines, landfills ...
   Each row keeps lat/lon so emissions stay bound to a geography.

2. SBTi target dashboard (`targets-excel.xlsx`, `companies-excel.xlsx`), which
   records the *commitment* side: base year, target year, ambition and which
   scopes the target covers.

3. Nothing else.  Scope-2 and scope-3 *tonnages* are deliberately not
   fabricated: GHGRP does not publish purchased-electricity or value-chain
   emissions per facility, so they are reported as a coverage gap, not a number.
"""
from __future__ import annotations

import os
import re
import sys
import zipfile

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (GHGRP_YEARS, GHGRP_ZIP_URL, INTERIM, RAW, USER_AGENT)  # noqa: E402

GH_DIR = RAW / "ghgrp"
GH_DIR.mkdir(parents=True, exist_ok=True)
SBTI_DIR = RAW / "sbti"
SBTI_DIR.mkdir(parents=True, exist_ok=True)

SBTI_FILES = {
    "sbti_targets.xlsx": "https://files.sciencebasedtargets.org/production/files/targets-excel.xlsx",
    "sbti_companies.xlsx": "https://files.sciencebasedtargets.org/production/files/companies-excel.xlsx",
}

# ---------------------------------------------------------------------------
# canonical renaming
# ---------------------------------------------------------------------------
def norm(s) -> str:
    s = str(s).lower().strip()
    s = s.replace("\u2013", "-").replace("\u2014", "-")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s


CANON = {
    "facility_id": "facility_id",
    "frs_id": "frs_id",
    "facility_name": "facility_name",
    "city": "city",
    "reported_city": "city",
    "state": "state",
    "reported_state": "state",
    "zip_code": "zip",
    "reported_zip_code": "zip",
    "address": "address",
    "reported_address": "address",
    "county": "county",
    "reported_county": "county",
    "latitude": "latitude",
    "reported_latitude": "latitude",
    "longitude": "longitude",
    "reported_longitude": "longitude",
    "primary_naics_code": "naics",
    "industry_type_subparts": "subparts",
    "industry_type_sectors": "sectors",
    "total_reported_direct_emissions": "scope1_co2e",
    "total_reported_emissions_from_onshore_oil_gas_production": "scope1_co2e",
    "total_reported_emissions": "scope1_co2e",
    "co2_emissions_non_biogenic": "co2",
    "methane_ch4_emissions": "ch4",
    "nitrous_oxide_n2o_emissions": "n2o",
    "hfc_emissions": "hfc",
    "pfc_emissions": "pfc",
    "sf6_emissions": "sf6",
    "nf3_emissions": "nf3",
    "biogenic_co2_emissions_metric_tons": "biogenic_co2",
    "stationary_combustion": "stationary_combustion",
    "electricity_generation": "electricity_generation",
    "petroleum_refining": "petroleum_refining",
    "municipal_landfills": "municipal_landfills",
    "underground_coal_mines": "underground_coal_mines",
    "petroleum_and_natural_gas_systems_processing": "png_processing",
    "petroleum_and_natural_gas_systems_transmission_compression": "png_transmission",
    "petroleum_and_natural_gas_systems_underground_storage": "png_storage",
    "petroleum_and_natural_gas_systems_offshore_production": "png_offshore",
    "basin": "basin",
    # supplier-specific quantity columns
    "ghg_quantity_associated_with_petroleum_products_produced": "supplier_petroleum_produced",
    "ghg_quantity_associated_with_petroleum_products_imported": "supplier_petroleum_imported",
    "ghg_quantity_associated_with_petroleum_products_exported": "supplier_petroleum_exported",
    "ghg_quantity_associated_with_petroleum_products_supplied": "supplier_petroleum_supplied",
    "ghg_quantity_associated_with_coal_based_liquid_fuel_production": "supplier_coal_liquid",
    "ghg_quantity_associated_with_natural_gas_supplied": "supplier_ng_supplied",
    "ghg_quantity_associated_with_natural_gas_supply": "supplier_ng_supplied",
    "ghg_quantity_associated_with_natural_gas_imported": "supplier_ng_imported",
    "ghg_quantity_associated_with_natural_gas_exported": "supplier_ng_exported",
    "ghg_quantity_associated_with_natural_gas_liquids_supply": "supplier_ngl_supplied",
    "ghg_quantity_associated_with_co2_supply": "supplier_co2_supplied",
}

SUPPLY_COLS = [c for c in set(CANON.values()) if c.startswith("supplier_")]

TIER_OF_SHEET = {
    "direct point emitters": "direct",
    "direct emitters": "direct",
    "onshore oil & gas prod.": "fugitive",
    "gathering & boosting": "fugitive",
    "transmission pipelines": "fugitive",
    "ldc - direct emissions": "fugitive",
    "sf6 from elec. equip.": "fugitive",
    "suppliers": "supply",
    "co2 injection": "other",
    "geologic sequestration of co2": "other",
}


def download(url: str, dest, min_bytes: int = 10_000) -> bool:
    if dest.exists() and dest.stat().st_size > min_bytes:
        print(f"[skip] {dest.name}")
        return True
    for attempt in range(3):
        try:
            with requests.get(url, headers={"User-Agent": USER_AGENT},
                              stream=True, timeout=300) as r:
                r.raise_for_status()
                tmp = dest.with_suffix(dest.suffix + ".part")
                with open(tmp, "wb") as fh:
                    for ch in r.iter_content(1 << 20):
                        fh.write(ch)
                tmp.replace(dest)
            print(f"[ok]   {dest.name} {dest.stat().st_size/1e6:.1f} MB")
            return True
        except Exception as exc:  # noqa: BLE001
            print(f"[retry {attempt+1}] {url}: {type(exc).__name__} {exc}")
    return False


def find_header_row(raw: pd.DataFrame) -> int | None:
    for i in range(min(12, len(raw))):
        row = [norm(x) for x in raw.iloc[i].tolist()]
        if "facility_id" in row:
            return i
    return None


def load_sheet(xl, sheet: str) -> pd.DataFrame | None:
    raw = xl.parse(sheet, header=None)
    if raw.empty:
        return None
    hdr = find_header_row(raw)
    if hdr is None:
        return None
    names = []
    for j, v in enumerate(raw.iloc[hdr].tolist()):
        n = norm(v) if pd.notna(v) else ""
        if not n or n.startswith("unnamed"):
            n = f"col{j}"
        if n in names:
            n = f"{n}_{j}"
        names.append(n)
    body = raw.iloc[hdr + 1:].reset_index(drop=True)
    body.columns = names
    keep = {c: CANON.get(c) for c in names if CANON.get(c)}
    if "latitude" not in keep.values() or "longitude" not in keep.values():
        return None
    out = body[list(keep)].rename(columns=keep)
    out = out.loc[:, ~out.columns.duplicated()]
    return out


NUMERIC = {"latitude", "longitude", "scope1_co2e", "co2", "ch4", "n2o", "hfc",
           "pfc", "sf6", "nf3", "biogenic_co2", "stationary_combustion",
           "electricity_generation", "petroleum_refining", "municipal_landfills",
           "underground_coal_mines", "png_processing", "png_transmission",
           "png_storage", "png_offshore", "supplier_petroleum_produced",
           "supplier_petroleum_imported", "supplier_petroleum_exported",
           "supplier_petroleum_supplied", "supplier_coal_liquid",
           "supplier_ng_supplied", "supplier_ng_imported", "supplier_ng_exported"}


def build_ghgrp() -> pd.DataFrame:
    frames = []
    for year in GHGRP_YEARS:
        path = GH_DIR / f"ghgp_data_{year}.xlsx"
        if not path.exists():
            print(f"[warn] missing {path.name}")
            continue
        xl = pd.ExcelFile(path)
        for sheet in xl.sheet_names:
            df = load_sheet(xl, sheet)
            if df is None:
                continue
            df["year"] = year
            df["tier"] = TIER_OF_SHEET.get(sheet.strip().lower(), "other")
            df["source_sheet"] = sheet
            frames.append(df)
        print(f"  {year}: {len(xl.sheet_names)} sheets scanned")
    if not frames:
        raise SystemExit("no GHGRP data loaded")
    ghg = pd.concat(frames, ignore_index=True, sort=False)
    for c in (NUMERIC | set(SUPPLY_COLS)) & set(ghg.columns):
        ghg[c] = pd.to_numeric(ghg[c], errors="coerce")
    # supplier sheets carry no single "total" column: their reported quantity is
    # the row-sum of the individual fossil-fuel supply columns.
    supply_present = [c for c in SUPPLY_COLS if c in ghg.columns]
    if supply_present:
        ghg["supply_co2e"] = ghg[supply_present].sum(axis=1, min_count=1)
        mask = ghg["tier"].eq("supply") & ghg["scope1_co2e"].isna()
        ghg.loc[mask, "scope1_co2e"] = ghg.loc[mask, "supply_co2e"]
    for c in ("facility_name", "city", "state", "county", "sectors", "subparts",
              "naics", "basin", "source_sheet", "tier"):
        if c in ghg.columns:
            ghg[c] = ghg[c].astype(str).str.strip().replace({"nan": np.nan})
    # dedupe: a facility can sit in two sheets; keep the row with the largest total
    ghg["_key"] = (ghg["year"].astype(str) + "|" + ghg["facility_id"].astype(str)
                   + "|" + ghg["tier"])
    ghg = (ghg.sort_values("scope1_co2e", ascending=False, na_position="last")
              .drop_duplicates("_key").drop(columns="_key"))
    return ghg.reset_index(drop=True)


def build_industry_map() -> pd.DataFrame:
    path = GH_DIR / "ghgp_data_2023.xlsx"
    xl = pd.ExcelFile(path)
    if "Industry Type" not in xl.sheet_names:
        return pd.DataFrame()
    raw = xl.parse("Industry Type", header=None)
    rows = []
    for _, r in raw.iterrows():
        vals = [str(x).strip() for x in r.tolist() if pd.notna(x)]
        if len(vals) >= 3 and re.fullmatch(r"[A-Z]{1,3}", vals[0]):
            rows.append({"subpart": vals[0], "subpart_name": vals[1],
                         "subpart_group": vals[2]})
    return pd.DataFrame(rows).drop_duplicates("subpart")


def build_sbti() -> None:
    for name, url in SBTI_FILES.items():
        download(url, SBTI_DIR / name)


def main() -> int:
    zpath = RAW / "ghgrp_2023_summary.zip"
    if not download(GHGRP_ZIP_URL, zpath, min_bytes=1_000_000):
        print("[FATAL] GHGRP archive unavailable")
        return 1
    with zipfile.ZipFile(zpath) as z:
        for member in z.namelist():
            m = re.match(r"ghgp_data_(\d{4})\.xlsx$", member, re.I)
            if m:
                target = GH_DIR / f"ghgp_data_{m.group(1)}.xlsx"
                if not target.exists():
                    with z.open(member) as src, open(target, "wb") as dst:
                        dst.write(src.read())
    print(f"workbooks present: {len(list(GH_DIR.glob('*.xlsx')))}")

    ghg = build_ghgrp()
    ghg.drop(columns=[c for c in ("col0",) if c in ghg.columns], inplace=True)
    ghg.to_csv(INTERIM / "ghgrp_reporter_year.csv", index=False)
    print(f"\nGHGRP: {len(ghg):,} reporter-year rows, "
          f"{ghg['facility_id'].nunique():,} unique facilities")
    print(ghg.groupby(["tier", "year"]).size().unstack(fill_value=0).tail(3).to_string())

    ind = build_industry_map()
    ind.to_csv(INTERIM / "ghgrp_industry_map.csv", index=False)
    print(f"industry map rows: {len(ind)}")

    build_sbti()
    return 0


if __name__ == "__main__":
    sys.exit(main())
