"""Build the SQLite database and run the indicator queries.

All analytical SQL lives in sql/02_indicators.sql; this script only loads the
cleaned tables (rebuilt from scratch every run, so a stale DB cannot survive a
code change) and executes those blocks, writing one CSV per indicator.

The database is deliberately wider than the first version: carbon now has a
unit-level fact table, land cover has annual loss and sensitivity tables, and
fire, DETER alerts and national CO2 each get their own facts.  Every table is
listed in dim_source so it is clear which dataset it came from.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (BIOMES, DB, INTERIM, MASK_DIR, OUT, RAW, ROOT)  # noqa: E402

SQL_DIR = ROOT / "sql"
OUT.mkdir(parents=True, exist_ok=True)
QUERY_BUDGET_S = 180   # per-indicator wall-clock budget

INDICATORS = {
    # ---- carbon -----------------------------------------------------------
    "carbon_tier_trend": ("Reported emissions by carbon stratum", "Mt CO2e / %",
                          "Reported GHGRP totals indexed to 2019.",
                          "GHGRP facility-year", "year x tier"),
    "carbon_pathway_gap": ("Direct-emitter emissions vs net-zero pathway", "Mt CO2e",
                           "Actual vs linearised 2019->net-zero-2050 pathway.",
                           "GHGRP facility-year", "year"),
    "carbon_kyoto_benchmark": ("Direct emissions against the first reporting year",
                               "Mt / %", "Absolute change 2010->latest.",
                               "GHGRP facility-year", "year"),
    "carbon_benchmark": ("Direct emissions against the first reporting year",
                         "Mt / %", "Absolute change from the first year to latest.",
                         "GHGRP facility-year", "year"),
    "carbon_state_movers": ("State-level change in direct emissions", "Mt CO2e",
                            "2019 vs 2023 by reported state.",
                            "GHGRP facility-year", "state"),
    "carbon_facility_trajectory": ("Facility trajectory classes", "count / Mt CO2e",
                                   "Facilities present in 2015 and 2023.",
                                   "GHGRP facility-year", "facility"),
    "carbon_concentration": ("Concentration of emissions", "% / HHI",
                             "Top-20 share and Herfindahl index per stratum.",
                             "GHGRP facility-year", "tier"),
    "carbon_sector_trend": ("Sector-level direct emissions", "Mt CO2e",
                            "Top sectors by 2023 emissions, 2019 vs 2023.",
                            "GHGRP facility-year", "sector"),
    "carbon_gas_mix": ("Reported gas mix", "Mt / %",
                       "CO2, CH4, N2O, fluorinated gases by year.",
                       "GHGRP facility-year", "year"),
    "carbon_entry_exit": ("Facilities entering and leaving the reporting frame",
                          "count / Mt", "Reporters present in year Y but not Y-1.",
                          "GHGRP facility-year", "year"),
    "carbon_unit_mechanism": ("Unit-level capacity and emissions by fuel",
                              "count / Mt / t per mmBTU-h",
                              "Coal vs gas vs other units, capacity and utilisation.",
                              "GHGRP unit-level (C/D/AA)", "year x fuel"),
    "carbon_lmdi": ("LMDI decomposition of the 2019->2023 change", "Mt CO2e",
                    "Activity + structure + intensity effects by sector.",
                    "GHGRP facility-year", "sector"),
    "carbon_bootstrap": ("Bootstrap uncertainty of the carbon quantities", "Mt / index",
                         "Facility-resampled confidence intervals.",
                         "GHGRP facility-year", "quantity"),
    # ---- forest -----------------------------------------------------------
    "biome_change": ("Biome forest / fragmentation / connectivity change", "km2, ha, index",
                     "Baseline year vs latest PRODES year.",
                     "PRODES (WMS+WFS)", "biome"),
    "corridor_status": ("Connectivity target status", "kha / %",
                        "Net connected-area gain against 500 000 ha.",
                        "PRODES (WMS+WFS)", "biome"),
    "corridor_annual": ("Annual connected-area series", "Mha / kha",
                        "Effective connected area by year.",
                        "PRODES (WMS+WFS)", "biome x year"),
    "fragmentation_yoy": ("Year-on-year fragmentation", "count / index",
                          "Patch count, edge density, core share.",
                          "PRODES (WMS+WFS)", "biome x year"),
    "forest_loss_annual": ("Annual forest loss and its geometry", "km2 / %",
                           "Increment area, share inside core, front adjacency.",
                           "PRODES increments", "biome x year"),
    "calibration_summary": ("Raster vs official area calibration", "km2 / ratio",
                            "Per biome-year bias before and after thinning.",
                            "PRODES (WMS+WFS)", "biome x year"),
    "trend_summary": ("Trend tests on every annual series", "slope / p",
                      "Mann-Kendall p and Theil-Sen slope.",
                      "pipeline", "series x entity"),
    "trend_significant": ("Series with a significant monotone trend", "count",
                          "The Theil-Sen slopes that survive at the 5% level.",
                          "pipeline", "series"),
    "sensitivity_dispersal": ("Connectivity sensitivity to dispersal distance",
                              "Mha", "ECA outside the dominant block at 2/5/10/20 km.",
                              "PRODES (WMS+WFS)", "biome x year"),
    "sensitivity_summary": ("Sensitivity ranges", "range",
                            "How far each conclusion moves across thresholds.",
                            "pipeline", "variant"),
    # ---- fire -------------------------------------------------------------
    "fire_biome_year": ("Active-fire detections by biome", "count",
                        "Annual foci per biome, 2003-2024.", "INPE BDQueimadas",
                        "biome x year"),
    "fire_seasonality": ("Fire seasonality", "count / share",
                         "Monthly climatology and August-October share.",
                         "INPE BDQueimadas", "biome x month"),
    "fire_per_forest": ("Fire intensity relative to remaining forest", "foci / 100 km2",
                        "Detections normalised by forest area.", "INPE BDQueimadas",
                        "biome x year"),
    "fire_vs_loss": ("Fire and deforestation co-movement", "spearman r",
                     "Same-year and fire-leads-loss correlations.",
                     "INPE BDQueimadas + PRODES", "biome"),
    "fire_top_states": ("Where the fire is", "count / share", "Top states by foci.",
                        "INPE BDQueimadas", "state"),
    # ---- DETER ------------------------------------------------------------
    "deter_annual": ("DETER alerts by biome and year", "count / km2",
                     "Near-real-time clearing alerts.", "INPE DETER (WFS)",
                     "biome x year"),
    "deter_monthly": ("DETER monthly signal", "count",
                      "Monthly alert counts, the only monthly series here.",
                      "INPE DETER (WFS)", "biome x month"),
    "deter_class_mix": ("DETER alert classes", "count / km2",
                        "Clearing vs degradation classes.", "INPE DETER (WFS)",
                        "biome x class"),
    # ---- species ----------------------------------------------------------
    "species_retention_by_class": ("Species persistence by forest-loss class", "share",
                                   "Share of testable species retained per cell.",
                                   "GBIF occurrence", "group x biome x class"),
    "species_retention_pooled": ("Pooled species persistence", "share / pp",
                                 "Area-weighted retention, contrast vs stable.",
                                 "GBIF occurrence", "group x class"),
    "species_logit": ("Persistence model", "log-odds",
                      "Forest loss and effort as predictors of persistence.",
                      "GBIF occurrence", "group x term"),
    "species_cross_taxon": ("Cross-taxon agreement", "spearman r",
                            "Do the four groups agree within the same cells?",
                            "GBIF occurrence", "group pair"),
    "species_iucn": ("Persistence by IUCN category", "count / share",
                     "Threatened vs non-threatened species.", "GBIF occurrence",
                     "group x iucn"),
    "species_range_summary": ("Species range change summary", "share / km",
                              "Occupied-cell change and centroid shift.",
                              "GBIF occurrence", "group"),
    # ---- national context -------------------------------------------------
    "national_co2_brazil": ("Brazil national CO2 panel", "Mt CO2",
                            "Fossil and land-use CO2, 1990-2025.",
                            "OWID / Global Carbon Budget", "country x year"),
    "national_co2_compare": ("Brazil vs world fossil CO2", "Mt / t per capita",
                             "The national frame the facility data sits in.",
                             "OWID / Global Carbon Budget", "country x year"),
    "wb_indicators": ("World Bank forest and protection indicators", "km2 / %",
                      "Official forest area and protected-area share.",
                      "World Bank", "country x year"),
    # ---- meta -------------------------------------------------------------
    "data_coverage": ("Open-data coverage per year", "count",
                      "Rows available per source per year.",
                      "pipeline", "source x year"),
    "source_catalogue": ("Source catalogue", "text",
                         "Every dataset, its URL, grain and row count.",
                         "pipeline", "source"),
    "scope_boundary": ("Which scopes the target register constrains", "count",
                       "SBTi targets by scope - where scope 2/3 are observable.",
                       "SBTi target register", "scope"),
    "target_ambition_by_sector": ("Target ambition by sector", "% / count",
                                  "Mean reduction and target year by sector.",
                                  "SBTi target register", "sector"),
}

SOURCES = [
    dict(source="GHGRP facility-year", url="https://www.epa.gov/ghgreporting/data-sets",
         grain="facility x year x stratum", licence="public domain (US EPA)",
         note="Annual summary workbooks 2010-2023, all sheets with id+coordinates."),
    dict(source="GHGRP unit-level C/D/AA",
         url="https://www.epa.gov/system/files/other-files/2024-10/emissions_by_unit_and_fuel_type_c_d_aa.zip",
         grain="facility x unit x fuel x year", licence="public domain (US EPA)",
         note="Combustion units with nameplate capacity and fuel mix."),
    dict(source="SBTi target register",
         url="https://sciencebasedtargets.org/target-dashboard",
         grain="target", licence="open (SBTi terms)",
         note="Commitment side: base year, target year, scope, ambition."),
    dict(source="PRODES (INPE) via TerraBrasilis", url="http://terrabrasilis.dpi.inpe.br/geoserver",
         grain="biome x year (raster 0.02 deg)", licence="open (INPE)",
         note="Annual increments + accumulated baseline, rasterised through WMS."),
    dict(source="PRODES official area", url="http://terrabrasilis.dpi.inpe.br/geoserver",
         grain="biome x year", licence="open (INPE)",
         note="Exact sum of the area_km attribute, used to calibrate raster area."),
    dict(source="DETER (INPE) via TerraBrasilis", url="http://terrabrasilis.dpi.inpe.br/geoserver",
         grain="alert", licence="open (INPE)",
         note="Monthly clearing alerts, Amazonia 2016+ and Cerrado 2018+."),
    dict(source="BDQueimadas (INPE)", url="https://dataserver-coids.inpe.br/queimadas/",
         grain="fire detection", licence="open (INPE)",
         note="Reference-satellite active fire foci, 2003-2024."),
    dict(source="GBIF occurrence", url="https://api.gbif.org/v1",
         grain="occurrence record", licence="CC0/CC-BY per dataset",
         note="Four taxa, Brazil, with coordinates; effort kept as a covariate."),
    dict(source="OWID / Global Carbon Budget", url="https://github.com/owid/co2-data",
         grain="country x year", licence="CC BY 4.0",
         note="National fossil and land-use CO2 for triangulation."),
    dict(source="World Bank", url="https://api.worldbank.org/v2",
         grain="country x year", licence="CC BY 4.0",
         note="Forest area, protected-area share, renewable electricity share."),
]


def read(name: str, **kw) -> pd.DataFrame | None:
    p = INTERIM / name
    if not p.exists():
        print(f"  [skip] {name} missing")
        return None
    return pd.read_csv(p, low_memory=False, **kw)


def load(df: pd.DataFrame, table: str, con: sqlite3.Connection,
         fast: bool = False) -> None:
    """Insert a frame.  `fast` batches rows into multi-row INSERTs.

    pandas' default to_sql emits one INSERT per row; on the 600 k-row DETER fact
    table that is minutes of round-trips.  The batched path keeps chunks under
    SQLite's bound-variable limit (32 766 on 3.32) so nothing silently truncates.
    """
    if fast and len(df) > 20_000:
        cols = max(len(df.columns), 1)
        chunk = max(200, min(2000, 32000 // cols))
        df.to_sql(table, con, if_exists="append", index=False,
                  method="multi", chunksize=chunk)
    else:
        df.to_sql(table, con, if_exists="append", index=False)


def biome_areas() -> dict:
    from config import GRID_RES, LAT_MAX, NROWS
    lat_c = LAT_MAX - (np.arange(NROWS) + 0.5) * GRID_RES
    cell = (111.32 * GRID_RES) ** 2 * np.cos(np.radians(lat_c))
    out = {}
    for tag in BIOMES:
        f = MASK_DIR / f"border_{tag}.npy"
        if f.exists():
            m = np.load(f)
            out[tag] = float((m.sum(axis=1) * cell).sum())
    return out


def main() -> int:
    if DB.exists():
        DB.unlink()
    con = sqlite3.connect(DB)
    # the file is written once per run and never used concurrently: trade
    # durability for load speed
    con.execute("PRAGMA journal_mode = OFF")
    con.execute("PRAGMA synchronous = OFF")
    con.executescript((SQL_DIR / "01_schema.sql").read_text(encoding="utf-8"))
    con.commit()
    print(f"database created: {DB}")

    counts = {}

    # ---- dimensions -------------------------------------------------------
    meta = (json.loads((INTERIM / "biome_meta.json").read_text(encoding="utf-8"))
            if (INTERIM / "biome_meta.json").exists() else {})
    areas = biome_areas()
    pd.DataFrame([dict(biome=k, baseline_year=v["baseline_year"],
                       baseline_label=v["baseline_label"],
                       area_km2=areas.get(k), n_years=v.get("n_years"))
                  for k, v in meta.items()]).to_sql("dim_biome", con,
                                                    if_exists="append", index=False)

    ghg = read("ghgrp_reporter_year.csv")
    if ghg is not None:
        ghg["reporter_id"] = ghg.facility_id.astype(str) + "|" + ghg.tier.astype(str)
        latest = ghg.sort_values("year").groupby("reporter_id").last().reset_index()
        latest[["reporter_id", "facility_id", "tier", "facility_name", "state",
                "county", "latitude", "longitude", "naics", "sectors"]].to_sql(
            "dim_reporter", con, if_exists="append", index=False)
        fact_cols = ["reporter_id", "year", "tier", "scope1_co2e", "co2", "ch4",
                     "n2o", "hfc", "pfc", "sf6", "nf3", "biogenic_co2",
                     "stationary_combustion", "electricity_generation"]
        f = ghg[[c for c in fact_cols if c in ghg.columns]].copy()
        for c in fact_cols:
            if c not in f.columns:
                f[c] = np.nan
        f = f[fact_cols].dropna(subset=["scope1_co2e"])
        load(f, "fact_carbon_year", con, fast=True)
        counts["fact_carbon_year"] = len(f)
        counts["dim_reporter"] = len(latest)
        print(f"  carbon: {len(latest):,} reporters, {len(f):,} reporter-years")

        # sector mix per reporter-year, for the LMDI / sector indicators
        sec = ghg[["reporter_id", "year", "sectors", "scope1_co2e"]].copy()
        sec["sector"] = (sec.sectors.fillna("unknown").astype(str)
                         .str.split(",").str[0].str.strip().replace("", "unknown"))
        sec[["reporter_id", "year", "sector"]].to_sql(
            "dim_reporter_sector", con, if_exists="append", index=False)

    # ---- SBTi -------------------------------------------------------------
    tpath = RAW / "sbti" / "sbti_targets.xlsx"
    if tpath.exists():
        raw_t = pd.read_excel(tpath, sheet_name=0)
        if "full_target_language" in raw_t.columns:
            pct = pd.to_numeric(raw_t["full_target_language"].astype(str).str.extract(
                r"(\d+(?:\.\d+)?)\s*%", expand=False), errors="coerce")
            raw_t["target_value"] = pd.to_numeric(
                raw_t.get("target_value"), errors="coerce").fillna(pct)
        keep = {"company_name": "company_name", "sector": "sector",
                "location": "location", "region": "region", "scope": "scope",
                "target_value": "target_value", "base_year": "base_year",
                "target_year": "target_year", "status": "status",
                "target_classification_short": "classification",
                "date_published": "date_published"}
        t = raw_t[[c for c in keep if c in raw_t.columns]].rename(columns=keep)
        for c in ("target_value", "base_year", "target_year"):
            t[c] = pd.to_numeric(t[c], errors="coerce")
        t.to_sql("dim_target", con, if_exists="append", index=False)
        counts["dim_target"] = len(t)
        print(f"  targets: {len(t):,} rows")

    # ---- land cover -------------------------------------------------------
    bym = read("biome_year_metrics.csv")
    if bym is not None:
        cols = [c for c in bym.columns if c not in ("tier",) or True]
        bym[cols].to_sql("fact_biome_year", con, if_exists="append", index=False)  # noqa: E501
        counts["fact_biome_year"] = len(bym)
    for name, table in (("corridor_progress.csv", "fact_corridor_progress"),
                        ("biome_loss_annual.csv", "fact_loss_annual"),
                        ("connectivity_sensitivity.csv", "fact_sensitivity"),
                        ("mask_calibration.csv", "fact_calibration")):
        df = read(name)
        if df is not None:
            df.to_sql(table, con, if_exists="append", index=False)
            counts[table] = len(df)
    print("  land cover:",
          {k: v for k, v in counts.items() if k.startswith("fact_b") or
           k in ("fact_corridor_progress", "fact_loss_annual", "fact_sensitivity")})

    # ---- units ------------------------------------------------------------
    u = read("ghgrp_units.csv")
    if u is not None:
        u["facility_id"] = pd.to_numeric(u.facility_id, errors="coerce").astype("Int64").astype(str)
        for c in ("co2", "ch4", "n2o", "biogenic_co2", "capacity_mmbtu_hr"):
            u[c] = pd.to_numeric(u[c], errors="coerce")
        u["co2e"] = u[["co2", "ch4", "n2o"]].fillna(0).sum(axis=1)
        u["unit_key"] = u.facility_id + "|" + u.unit_name.fillna("")
        fuels = read("ghgrp_unit_fuel.csv")
        if fuels is not None:
            fuels["facility_id"] = pd.to_numeric(fuels.facility_id,
                                                 errors="coerce").astype("Int64").astype(str)
            fuels["unit_key"] = fuels.facility_id + "|" + fuels.unit_name.fillna("")
            mapping = {
                "coal": "Coal", "natural gas": "Natural Gas",
                "petroleum products": "Petroleum", "petroleum coke": "Petroleum",
            }
            fuels["fuel_group"] = (fuels.general_fuel.fillna("").str.lower()
                                   .map(mapping).fillna("Other"))
            dom = (fuels.groupby("unit_key").fuel_group
                   .agg(lambda s: s.mode().iat[0] if len(s.mode()) else "Other")
                   .rename("fuel_group").reset_index())
            u = u.merge(dom, on="unit_key", how="left")
            load(fuels.drop(columns=["unit_key", "fuel_group"]),
                 "fact_unit_fuel", con, fast=True)
            counts["fact_unit_fuel"] = len(fuels)
        u["fuel_group"] = u.get("fuel_group", "Other").fillna("Other")
        keep = ["facility_id", "frs_id", "state", "naics", "year", "subparts",
                "sectors", "unit_key", "unit_type", "reporting_method",
                "capacity_mmbtu_hr", "co2", "ch4", "n2o", "biogenic_co2",
                "co2e", "fuel_group"]
        load(u[keep], "fact_unit_year", con, fast=True)
        counts["fact_unit_year"] = len(u)
        print(f"  units: {len(u):,} unit-years, fuel groups: "
              f"{u.fuel_group.value_counts().to_dict()}")

    # ---- fire -------------------------------------------------------------
    fire = read("fire_biome_month.csv")
    if fire is not None:
        fire.rename(columns={"biome": "biome_name"}).to_sql(
            "fact_fire_biome_month", con, if_exists="append", index=False)
        counts["fact_fire_biome_month"] = len(fire)
    fcell = read("fire_cell_year.csv")
    if fcell is not None:
        fcell = fcell.rename(columns={"row": "grid_row", "col": "grid_col"})
        load(fcell, "fact_fire_cell_year", con, fast=True)
        counts["fact_fire_cell_year"] = len(fcell)
        print(f"  fire: {counts.get('fact_fire_biome_month', 0):,} biome-months, "
              f"{len(fcell):,} cell-years")

    # ---- DETER ------------------------------------------------------------
    det = read("deter_biome_month.csv")
    if det is not None:
        det.to_sql("fact_deter_month", con, if_exists="append", index=False)
        counts["fact_deter_month"] = len(det)
    alerts = read("deter_alerts.csv")
    if alerts is not None:
        alerts["view_date"] = alerts["view_date"].astype(str)
        load(alerts.drop(columns=[c for c in ("month",) if c in alerts.columns]),
             "fact_deter_alert", con, fast=True)
        counts["fact_deter_alert"] = len(alerts)
        print(f"  DETER: {len(alerts):,} alerts")

    # ---- species ----------------------------------------------------------
    cellp = read("species_cell_occupancy.csv")
    if cellp is not None:
        cellp = cellp.rename(columns={"scell": "cell"})
        load(cellp, "fact_species_cell", con, fast=True)
        counts["fact_species_cell"] = len(cellp)
    turn = read("species_turnover_cells.csv")
    if turn is not None:
        turn = turn.rename(columns={"scell": "cell"})
        turn["status"] = np.where(turn.retention >= 1.0, "persistent_all",
                                  np.where(turn.retention < 0.5, "mostly_lost",
                                           "partial"))
        turn.to_sql("fact_species_turnover", con, if_exists="append", index=False)
        counts["fact_species_turnover"] = len(turn)
    for name, table in (("species_by_forest_change.csv", "fact_species_retention"),
                        ("species_logit.csv", "fact_species_logit"),
                        ("species_cross_taxon.csv", "fact_species_cross_taxon"),
                        ("species_iucn.csv", "fact_species_iucn")):
        df = read(name)
        if df is not None:
            df.to_sql(table, con, if_exists="append", index=False)
            counts[table] = len(df)
    print("  species:", {k: v for k, v in counts.items() if "species" in k})

    # ---- national context -------------------------------------------------
    owid = read("owid_co2.csv")
    if owid is not None:
        load(owid, "fact_national_co2", con, fast=True)
        counts["fact_national_co2"] = len(owid)
    wb = read("wb_indicators.csv")
    if wb is not None:
        wb.to_sql("fact_wb_indicator", con, if_exists="append", index=False)
        counts["fact_wb_indicator"] = len(wb)

    # ---- statistics -------------------------------------------------------
    for name, table in (("stats_trends.csv", "fact_trend"),
                        ("stats_bootstrap.csv", "fact_bootstrap"),
                        ("stats_lmdi.csv", "fact_lmdi"),
                        ("stats_units.csv", "fact_unit_aggregate"),
                        ("stats_fire.csv", "fact_fire_biome_year"),
                        ("stats_fire_corr.csv", "fact_fire_corr"),
                        ("stats_fire_climatology.csv", "fact_fire_climatology"),
                        ("stats_triangulation.csv", "fact_triangulation")):
        df = read(name)
        if df is not None:
            df.to_sql(table, con, if_exists="append", index=False)
            counts[table] = len(df)
    print("  stats:", {k: v for k, v in counts.items()
                       if k.startswith(("fact_trend", "fact_boot", "fact_lmdi",
                                        "fact_unit_agg", "fact_fire_b", "fact_tri"))})

    # ---- metadata ---------------------------------------------------------
    ind = pd.DataFrame([dict(indicator=k, label=v[0], unit=v[1], definition=v[2],
                             source=v[3], grain=v[4])
                        for k, v in INDICATORS.items()])
    ind.to_sql("dim_indicator", con, if_exists="append", index=False)

    src = pd.DataFrame(SOURCES)
    src["retrieved"] = pd.Timestamp.now().strftime("%Y-%m-%d")
    src.to_sql("dim_source", con, if_exists="append", index=False)
    counts["dim_source"] = len(src)

    cov = []
    for tbl in counts:
        try:
            n = pd.read_sql_query(f'SELECT COUNT(*) n FROM "{tbl}"', con).n.iloc[0]
        except Exception:  # noqa: BLE001
            n = None
        cov.append(dict(table_name=tbl, rows=n))
    pd.DataFrame(cov).to_sql("dim_table_size", con, if_exists="append", index=False)
    con.commit()

    # ---- indexes and views ------------------------------------------------
    # strip `--` comments before splitting on `;`, otherwise a semicolon inside a
    # comment truncates the script and the remaining prose becomes SQL
    icons_sql = re.sub(r"--[^\n]*", "", (SQL_DIR / "02_views.sql").read_text(encoding="utf-8"))
    ok, failed = 0, 0
    for stmt in [s.strip() for s in icons_sql.split(";") if s.strip()]:
        body = stmt.strip()
        if not body:
            continue
        try:
            con.execute(body)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  [warn] view/index skipped: {type(exc).__name__} {str(exc)[:70]}")
    con.commit()
    print(f"views/indexes created: {ok} ok, {failed} skipped")

    # ---- run the indicator SQL -------------------------------------------
    blocks = []
    name, buf = None, []
    for line in (SQL_DIR / "03_indicators.sql").read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("-- >>>"):
            if name and buf:
                blocks.append((name, "\n".join(buf).strip()))
            name = line.split(">>>", 1)[1].strip()
            buf = []
        elif name:
            buf.append(line)
    if name and buf:
        blocks.append((name, "\n".join(buf).strip()))

    print(f"\nrunning {len(blocks)} indicator queries")
    summary = []
    for name, sql in blocks:
        t0 = time.time()
        # a pathological query must not hang the whole run: SQLite's progress
        # handler aborts it after the budget and the failure is recorded
        con.set_progress_handler(
            lambda: 1 if (time.time() - t0) > QUERY_BUDGET_S else 0, 20000)
        try:
            df = pd.read_sql_query(sql, con)
        except Exception as exc:  # noqa: BLE001
            print(f"  [!] {name}: {type(exc).__name__} {exc}")
            summary.append(dict(indicator=name, rows=0, file="", error=str(exc)[:120]))
            continue
        finally:
            con.set_progress_handler(None, 0)
        path = OUT / f"ind_{name}.csv"
        df.to_csv(path, index=False)
        summary.append(dict(indicator=name, rows=len(df),
                            file=str(path.relative_to(ROOT)), error=""))
        print(f"  {name:34s} {len(df):6d} rows -> {path.name}")
    pd.DataFrame(summary).to_csv(OUT / "indicator_index.csv", index=False)

    total_rows = int(pd.DataFrame(cov).rows.fillna(0).sum())
    print(f"\ndatabase: {DB.stat().st_size/1e6:.1f} MB, "
          f"{len(counts)} loaded tables, {total_rows:,} fact rows, "
          f"{len(blocks)} indicators")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
