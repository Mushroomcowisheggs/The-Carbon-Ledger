"""Unit- and fuel-level GHGRP data (subparts C, D, AA).

The facility-year summary used by s01 is the *reported* total per facility.  The
unit-level workbook splits that total into combustion units and, for each unit,
into the fuels burned.  That is the difference between "this plant emitted 4 Mt"
and "this plant runs four coal units and one gas unit, and the coal units are
the ones that moved".  Two things become possible that the summary table cannot
support:

  * capacity-weighted utilisation (`unit maximum rated heat input capacity` is
    published per unit), which separates "ran less" from "closed";
  * fuel-mix change, which is the mechanism behind most of the observed
    reductions in the power sector.

The workbook ships as a single .xlsb sheet pair with a six-row preamble, so it
is streamed row by row (the sheet is far too large to parse into a DataFrame on
a laptop) and written out as two tidy CSVs.

Outputs
  data/interim/ghgrp_units.csv       facility x unit x year emissions + capacity
  data/interim/ghgrp_unit_fuel.csv   facility x unit x fuel x year CH4/N2O
"""
from __future__ import annotations

import csv
import os
import sys
import time
import zipfile

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import GHGRP_UNIT_ZIP_URL, INTERIM, RAW, USER_AGENT  # noqa: E402

RAW.mkdir(parents=True, exist_ok=True)
ZIP = RAW / "ghgrp_units_c_d_aa.zip"
XLSB = RAW / "ghgrp" / "emissions_by_unit_and_fuel_type_c_d_aa.xlsb"

UNIT_COLS = ["facility_id", "frs_id", "state", "naics", "year", "subparts",
             "sectors", "unit_name", "unit_type", "reporting_method",
             "capacity_mmbtu_hr", "co2", "ch4", "n2o", "biogenic_co2"]
FUEL_COLS = ["facility_id", "frs_id", "state", "naics", "year", "subparts",
             "sectors", "unit_name", "general_fuel", "specific_fuel",
             "ch4_co2e", "n2o_co2e"]


def download() -> None:
    if ZIP.exists() and ZIP.stat().st_size > 1_000_000:
        print(f"[skip] {ZIP.name} {ZIP.stat().st_size/1e6:.1f} MB")
        return
    print(f"[get ] {GHGRP_UNIT_ZIP_URL}")
    r = requests.get(GHGRP_UNIT_ZIP_URL, timeout=900,
                     headers={"User-Agent": USER_AGENT}, stream=True)
    r.raise_for_status()
    tmp = ZIP.with_suffix(".zip.part")
    with open(tmp, "wb") as fh:
        for chunk in r.iter_content(1 << 20):
            fh.write(chunk)
    tmp.replace(ZIP)
    print(f"[ok  ] {ZIP.name} {ZIP.stat().st_size/1e6:.1f} MB")


def extract() -> None:
    if XLSB.exists() and XLSB.stat().st_size > 1_000_000:
        print(f"[skip] {XLSB.name} {XLSB.stat().st_size/1e6:.1f} MB")
        return
    with zipfile.ZipFile(ZIP) as z:
        member = [n for n in z.namelist() if n.lower().endswith(".xlsb")][0]
        with z.open(member) as src, open(XLSB, "wb") as dst:
            dst.write(src.read())
    print(f"[ok  ] extracted {member} -> {XLSB.name}")


def _num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip().replace(",", "")
    if not s or s.lower() in ("nan", "na", "n/a", "-"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _txt(v):
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() == "nan" else s


def stream_sheet(book, sheet_name: str, cols: list[str], dest, numeric: set[str],
                 key: str) -> int:
    """Stream one sheet into a CSV, skipping the preamble."""
    from pyxlsb import open_workbook  # local import: vendor path comes from config

    t0 = time.time()
    n = 0
    with open(dest, "w", newline="", encoding="utf-8") as fh:
        out = csv.writer(fh)
        out.writerow(cols)
        with open_workbook(book) as wb, wb.get_sheet(sheet_name) as sh:
            header = None
            for row in sh.rows():
                vals = [c.v for c in row]
                if header is None:
                    if vals and str(vals[0]).strip().startswith("Facility Id"):
                        header = [_txt(v).lower() for v in vals]
                        idx = {}
                        for want in cols:
                            for j, h in enumerate(header):
                                if _norm_header(h) == want:
                                    idx[want] = j
                                    break
                        missing = [c for c in cols if c not in idx]
                        if missing:
                            print(f"  [warn] {sheet_name}: no column for {missing}")
                        continue
                    continue
                rec = []
                for c in cols:
                    j = idx.get(c)
                    v = vals[j] if (j is not None and j < len(vals)) else None
                    rec.append(_num(v) if c in numeric else _txt(v))
                out.writerow(rec)
                n += 1
                if n % 250_000 == 0:
                    print(f"    {key}: {n:,} rows  {time.time()-t0:.0f}s", flush=True)
    print(f"  {key}: {n:,} rows -> {dest.name} "
          f"({os.path.getsize(dest)/1e6:.1f} MB, {time.time()-t0:.0f}s)", flush=True)
    return n


def _norm_header(h: str) -> str:
    """Map the published header text onto our snake_case column names."""
    import re
    h = h.lower().strip()
    h = h.replace("\u2013", "-")
    h = re.sub(r"[^a-z0-9]+", "_", h).strip("_")
    table = {
        "facility_id": "facility_id", "frs_id": "frs_id", "state": "state",
        "primary_naics_code": "naics", "reporting_year": "year",
        "industry_type_subparts": "subparts", "industry_type_sectors": "sectors",
        "unit_name": "unit_name", "unit_type": "unit_type",
        "unit_reporting_method": "reporting_method",
        "general_fuel_type": "general_fuel", "specific_fuel_type": "specific_fuel",
    }
    if h in table:
        return table[h]
    if h.startswith("unit_maximum_rated_heat_input_capacity"):
        return "capacity_mmbtu_hr"
    if h.startswith("unit_co2_emissions"):
        return "co2"
    if h.startswith("unit_methane"):
        return "ch4"
    if h.startswith("unit_nitrous"):
        return "n2o"
    if h.startswith("unit_biogenic"):
        return "biogenic_co2"
    if h.startswith("fuel_methane"):
        return "ch4_co2e"
    if h.startswith("fuel_nitrous"):
        return "n2o_co2e"
    return h


def main() -> int:
    download()
    extract()
    if not XLSB.exists():
        print("[FATAL] unit workbook missing")
        return 1

    numeric_unit = {"year", "capacity_mmbtu_hr", "co2", "ch4", "n2o", "biogenic_co2"}
    numeric_fuel = {"year", "ch4_co2e", "n2o_co2e"}
    n1 = stream_sheet(str(XLSB), "UNIT_DATA", UNIT_COLS,
                      INTERIM / "ghgrp_units.csv", numeric_unit, "units")
    n2 = stream_sheet(str(XLSB), "FUEL_DATA", FUEL_COLS,
                      INTERIM / "ghgrp_unit_fuel.csv", numeric_fuel, "fuels")
    print(f"\nunit rows: {n1:,}   fuel rows: {n2:,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
