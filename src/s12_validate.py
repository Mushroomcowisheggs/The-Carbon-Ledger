"""Post-run checks: does the pipeline actually contain what the report claims?

This stage exists because "the script ran without an exception" is not evidence
that the data arrived.  A source can answer 200 with an empty payload, a cache
can be stale, a join can silently drop everything.  Every check below compares a
number the pipeline produced against either an independent count from the same
source or a structural expectation, and writes reports/validation.md.

Checks
  presence    every expected intermediate file exists and is non-empty
  volume      each source has the row count the report is about to quote
  integrity   cross-source arithmetic (unit totals vs facility totals, DETER
              alert counts vs the layer counts, species groups = 4)
  indicators  every SQL block produced a CSV, and how many failed
  figures     every expected figure exists

Exit code is 0 even when checks fail (the report still has to be written); the
failures are listed at the top of validation.md.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (DATA_VINTAGE_YEAR, DB, FIG, GBIF_TARGETS, INTERIM,  # noqa: E402
                    OUT, RAW, ROOT)

REPORT = ROOT / "reports" / "validation.md"

EXPECTED_INTERIM = {
    "ghgrp_reporter_year.csv": ("GHGRP facility-year", 100_000),
    "ghgrp_units.csv": ("GHGRP unit-level C/D/AA", 200_000),
    "ghgrp_unit_fuel.csv": ("GHGRP unit fuel mix", 200_000),
    "prodes_official_area.csv": ("PRODES official areas", 40),
    "prodes_annual_full.csv": ("PRODES annual inventory", 40),
    "biome_year_metrics.csv": ("PRODES derived metrics", 40),
    "biome_loss_annual.csv": ("annual loss geometry", 40),
    "connectivity_sensitivity.csv": ("connectivity sensitivity", 100),
    "fire_biome_month.csv": ("INPE fire foci aggregates", 500),
    "deter_biome_month.csv": ("DETER monthly", 50),
    "owid_co2.csv": ("OWID national CO2", 20_000),
    "gbif_occurrences.csv": ("GBIF occurrences", 60_000),
    "species_turnover_cells.csv": ("species turnover cells", 100),
    "stats_trends.csv": ("trend tests", 20),
    "stats_bootstrap.csv": ("bootstrap CIs", 5),
}

EXPECTED_FIGS = [
    "fig01_carbon_tiers.png", "fig02_pathway_gap.png", "fig03_facility_trajectory.png",
    "fig04_state_movers.png", "fig05_carbon_map.png", "fig06_sector_trend.png",
    "fig07_lmdi.png", "fig08_unit_mechanism.png", "fig09_biome_maps.png",
    "fig10_biome_annual.png", "fig11_connectivity.png", "fig12_sensitivity.png",
    "fig13_loss_geometry.png", "fig14_calibration.png", "fig15_fire_regime.png",
    "fig16_fire_seasonality.png", "fig17_fire_map.png", "fig18_deter.png",
    "fig19_species_retention.png", "fig20_species_forest.png", "fig21_species_logit.png",
    "fig22_national_co2.png", "fig23_trend.png", "fig24_coverage.png",
]


def rows(path) -> int | None:
    if not path.exists():
        return None
    with open(path, encoding="utf-8", errors="replace") as fh:
        return sum(1 for _ in fh) - 1


def main() -> int:
    checks: list[dict] = []
    failures = 0

    for name, (label, min_rows) in EXPECTED_INTERIM.items():
        n = rows(INTERIM / name)
        ok = n is not None and n >= min_rows
        checks.append(dict(group="presence", check=name, expected=f">= {min_rows:,}",
                           observed=("missing" if n is None else f"{n:,}"),
                           status="ok" if ok else "FAIL"))
        failures += 0 if ok else 1

    # ---- integrity --------------------------------------------------------
    ghg = INTERIM / "ghgrp_reporter_year.csv"
    if ghg.exists():
        df = pd.read_csv(ghg, low_memory=False, usecols=["year", "tier", "scope1_co2e"])
        d23 = df[(df.year == 2023) & (df.tier == "direct")].scope1_co2e.sum() / 1e6
        checks.append(dict(group="volume", check="GHGRP direct 2023 (Mt)",
                           expected="1500-3500", observed=f"{d23:,.0f}",
                           status="ok" if 1500 <= d23 <= 3500 else "FAIL"))

    units = INTERIM / "ghgrp_units.csv"
    if units.exists() and ghg.exists():
        u = pd.read_csv(units, low_memory=False,
                        usecols=["year", "co2", "ch4", "n2o"])
        for c in ("co2", "ch4", "n2o"):
            u[c] = pd.to_numeric(u[c], errors="coerce")
        tot = u[u.year == 2023][["co2", "ch4", "n2o"]].fillna(0).sum().sum() / 1e6
        fac = df[(df.year == 2023) & (df.tier == "direct")].scope1_co2e.sum() / 1e6
        # unit-level C/D/AA covers combustion units only, so it must be < total
        ok = 0 < tot < fac
        checks.append(dict(group="integrity", check="unit C/D/AA 2023 < facility total",
                           expected=f"0 < x < {fac:,.0f}", observed=f"{tot:,.0f}",
                           status="ok" if ok else "FAIL"))

    det = INTERIM / "deter_layer_counts.csv"
    alerts = INTERIM / "deter_alerts.csv"
    if det.exists() and alerts.exists():
        c = pd.read_csv(det)
        a = pd.read_csv(alerts)
        # the layer total includes the current partial year, which the alert table
        # deliberately excludes (DATA_VINTAGE_YEAR), so compare the sums to 2025
        for biome, g in c[c.year != "all"].groupby("biome"):
            g = g[pd.to_numeric(g.year, errors="coerce") <= DATA_VINTAGE_YEAR]
            total = pd.to_numeric(g.n_features, errors="coerce").sum()
            got = int((a.biome == biome).sum())
            ok = total > 0 and abs(got - total) / total < 0.01
            checks.append(dict(group="integrity",
                               check=f"DETER {biome} alerts vs layer counts",
                               expected=f"{int(total):,}", observed=f"{got:,}",
                               status="ok" if ok else "FAIL"))
            failures += 0 if ok else 1

    gb = INTERIM / "gbif_occurrences.csv"
    if gb.exists():
        g = pd.read_csv(gb, low_memory=False, usecols=["group", "species", "year"])
        n_groups = g.group.nunique()
        checks.append(dict(group="volume", check="species groups",
                           expected=str(len(GBIF_TARGETS)), observed=str(n_groups),
                           status="ok" if n_groups >= len(GBIF_TARGETS) else "FAIL"))
        checks.append(dict(group="volume", check="species records",
                           expected="> 60,000", observed=f"{len(g):,}",
                           status="ok" if len(g) > 60_000 else "FAIL"))
        checks.append(dict(group="volume", check="unique species",
                           expected="> 500", observed=f"{g.species.nunique():,}",
                           status="ok" if g.species.nunique() > 500 else "FAIL"))

    fire = INTERIM / "fire_foci.csv"
    if fire.exists():
        n = rows(fire)
        checks.append(dict(group="volume", check="fire detections", expected="> 1,000,000",
                           observed=f"{n:,}", status="ok" if (n or 0) > 1e6 else "FAIL"))

    inv = INTERIM / "prodes_annual_full.csv"
    if inv.exists():
        v = pd.read_csv(inv)
        pub = v[v.n_features >= 10].groupby("biome").year.count()
        for biome, n in pub.items():
            ok = n >= 12
            checks.append(dict(group="volume", check=f"PRODES years {biome}",
                               expected=">= 12", observed=str(int(n)),
                               status="ok" if ok else "FAIL"))

    # ---- database ---------------------------------------------------------
    if DB.exists():
        con = sqlite3.connect(DB)
        tables = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name", con)
        views = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='view'", con)
        checks.append(dict(group="database", check="tables",
                           expected=">= 25", observed=str(len(tables)),
                           status="ok" if len(tables) >= 25 else "FAIL"))
        checks.append(dict(group="database", check="views",
                           expected=">= 5", observed=str(len(views)),
                           status="ok" if len(views) >= 5 else "FAIL"))
        try:
            size = pd.read_sql_query("SELECT * FROM dim_table_size", con)
            total = int(size.rows.fillna(0).sum())
            checks.append(dict(group="database", check="fact rows", expected="> 1,000,000",
                               observed=f"{total:,}",
                               status="ok" if total > 1e6 else "FAIL"))
        except Exception:  # noqa: BLE001
            checks.append(dict(group="database", check="dim_table_size", expected="present",
                               observed="missing", status="FAIL"))
        con.close()
    else:
        checks.append(dict(group="database", check="db file", expected="present",
                           observed="missing", status="FAIL"))
        failures += 1

    # ---- indicators and figures ------------------------------------------
    idx = OUT / "indicator_index.csv"
    if idx.exists():
        i = pd.read_csv(idx)
        bad = i[i.error.fillna("") != ""]
        checks.append(dict(group="indicators", check="indicator CSVs", expected=">= 40",
                           observed=str(len(i)),
                           status="ok" if len(i) >= 40 else "FAIL"))
        checks.append(dict(group="indicators", check="failed queries", expected="0",
                           observed=str(len(bad)),
                           status="ok" if len(bad) == 0 else "FAIL"))
        for _, r in bad.iterrows():
            checks.append(dict(group="indicators", check=f"  {r.indicator}",
                               expected="runs", observed=str(r.error)[:80],
                               status="FAIL"))
    missing_figs = [f for f in EXPECTED_FIGS if not (FIG / f).exists()]
    checks.append(dict(group="figures", check="expected figures",
                       expected=str(len(EXPECTED_FIGS)),
                       observed=str(len(EXPECTED_FIGS) - len(missing_figs)),
                       status="ok" if not missing_figs else "FAIL"))
    for f in missing_figs:
        checks.append(dict(group="figures", check=f"  {f}", expected="present",
                           observed="missing", status="FAIL"))

    df = pd.DataFrame(checks)
    fails = df[df.status == "FAIL"]
    lines = ["# Pipeline validation", "",
             f"Generated by `src/s12_validate.py`. Checks: **{len(df)}**, "
             f"failures: **{len(fails)}**.", ""]
    if len(fails):
        lines += ["## Failures", "", "| group | check | expected | observed |",
                  "|---|---|---|---|"]
        for _, r in fails.iterrows():
            lines.append(f"| {r.group} | {r.check} | {r.expected} | {r.observed} |")
        lines.append("")
    lines += ["## All checks", "", "| group | check | expected | observed | status |",
              "|---|---|---|---|---|"]
    for _, r in df.iterrows():
        lines.append(f"| {r.group} | {r.check} | {r.expected} | {r.observed} | "
                     f"{'ok' if r.status == 'ok' else '**FAIL**'} |")
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"{len(df)} checks, {len(fails)} failures -> {REPORT.relative_to(ROOT)}")
    for _, r in fails.iterrows():
        print(f"  FAIL {r.group}/{r.check}: expected {r.expected}, got {r.observed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
