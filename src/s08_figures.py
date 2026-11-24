"""Figures.  Everything is drawn from the SQLite database or from the grids on
disk, so a figure can always be traced back to a query or a mask.

Labels are English-only on purpose: the report is bilingual (the prose is
Chinese, the axis labels are not) and a missing CJK glyph would silently render
as a box.  Colour convention used throughout: red = loss / worse, blue = gain /
better, grey = reference or assumption.
"""
from __future__ import annotations

import os
import sqlite3
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (BIOMES, DB, FIG, GRID_RES, INTERIM, LAT_MAX, LON_MIN,  # noqa: E402
                    MASK_DIR, OUT)

plt.rcParams.update({"figure.dpi": 130, "font.size": 9,
                     "axes.grid": True, "grid.alpha": 0.25,
                     "axes.spines.top": False, "axes.spines.right": False})
FIG.mkdir(parents=True, exist_ok=True)

RED, BLUE, GREEN, GREY, ORANGE = "#b2182b", "#2166ac", "#1b7837", "#777777", "#e08214"


def q(con, sql, **kw):
    return pd.read_sql_query(sql, con, params=kw)


def save(fig, name):
    fig.tight_layout()
    fig.savefig(FIG / name)
    plt.close(fig)


# ---------------------------------------------------------------------------
# carbon
# ---------------------------------------------------------------------------
def fig_carbon_tiers(con):
    df = q(con, "SELECT year, tier, SUM(scope1_co2e)/1e6 AS mt, COUNT(*) n "
                "FROM fact_carbon_year GROUP BY year, tier ORDER BY year")
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.6))
    for tier, g in df.groupby("tier"):
        axes[0].plot(g.year, g.mt, marker="o", ms=3, label=tier)
        axes[1].plot(g.year, g.n, marker="o", ms=3, label=tier)
    axes[0].set_yscale("log")
    axes[0].set_ylabel("reported Mt CO2e (log)")
    axes[0].set_title("GHGRP strata: what facility data observes")
    axes[1].set_ylabel("reporters")
    axes[1].set_title("reporting frame size")
    for ax in axes:
        ax.set_xlabel("reporting year")
    axes[0].legend(ncol=2, fontsize=7)
    save(fig, "fig01_carbon_tiers.png")


def fig_pathway(con):
    df = pd.read_csv(OUT / "ind_carbon_pathway_gap.csv")
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.plot(df.year, df.actual_mt, marker="o", label="reported direct", color=RED)
    ax.plot(df.year, df.required_mt, ls="--", label="net-zero pathway", color=BLUE)
    ax.fill_between(df.year, df.actual_mt, df.required_mt,
                    where=df.actual_mt > df.required_mt, alpha=0.2, color=RED,
                    label="gap")
    ax.set_xlabel("year")
    ax.set_ylabel("Mt CO2e")
    ax.set_title("Direct emitters vs a 2019->net-zero-2050 pathway")
    ax.legend(fontsize=8)
    save(fig, "fig02_pathway_gap.png")


def fig_trajectory(con):
    df = pd.read_csv(OUT / "ind_carbon_facility_trajectory.csv")
    df = df[df.trajectory != "excluded_small"]
    fig, ax = plt.subplots(figsize=(7, 3.2))
    colors = [GREEN if v < 0 else RED for v in df.net_delta_mt]
    ax.bar(df.trajectory, df.n_facilities, color=colors)
    for i, (n, t) in enumerate(zip(df.n_facilities, df.mt_2023)):
        ax.text(i, n, f"{n:,}\n{t:.0f} Mt", ha="center", va="bottom", fontsize=7)
    ax.set_ylabel("facilities (present 2015 and 2023)")
    ax.set_title("Facility-level trajectory classes")
    plt.setp(ax.get_xticklabels(), rotation=15, ha="right")
    save(fig, "fig03_facility_trajectory.png")


def fig_state_movers(con):
    df = pd.read_csv(OUT / "ind_carbon_state_movers.csv").head(12)
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    ax.barh(df.state, df.delta_mt, color=[RED if v > 0 else BLUE for v in df.delta_mt])
    ax.axvline(0, color="k", lw=0.8)
    ax.set_xlabel("change in reported direct emissions 2019->2023 (Mt CO2e)")
    ax.set_title("Geography of the change")
    ax.invert_yaxis()
    save(fig, "fig04_state_movers.png")


def fig_carbon_map(con):
    df = q(con, "SELECT r.latitude lat, r.longitude lon, f.scope1_co2e e "
                "FROM fact_carbon_year f JOIN dim_reporter r USING (reporter_id) "
                "WHERE f.year = 2023 AND f.tier = 'direct' AND r.latitude IS NOT NULL")
    df = df[(df.lat.between(24, 50)) & (df.lon.between(-125, -66)) & (df.e > 0)]
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    size = np.clip(np.log10(df.e) * 4, 2, 60)
    sc = ax.scatter(df.lon, df.lat, s=size, c=np.log10(df.e), cmap="inferno",
                    alpha=0.6, linewidths=0)
    cb = fig.colorbar(sc, ax=ax, shrink=0.85)
    cb.set_label("log10 t CO2e (2023)")
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title(f"Direct emitters, 2023 ({len(df):,} facilities)")
    ax.set_aspect(1.25)
    save(fig, "fig05_carbon_map.png")


def fig_sector_trend(con):
    df = pd.read_csv(OUT / "ind_carbon_sector_trend.csv").head(10)
    fig, ax = plt.subplots(figsize=(7, 4))
    y = np.arange(len(df))
    ax.hlines(y, df.mt_2019, df.mt_2023, color=GREY, lw=1.2, zorder=1)
    ax.scatter(df.mt_2019, y, color=BLUE, s=30, label="2019", zorder=2)
    ax.scatter(df.mt_2023, y, color=RED, s=30, label="2023", zorder=2)
    ax.set_yticks(y)
    ax.set_yticklabels([s[:34] for s in df.sector], fontsize=7)
    ax.set_xlabel("Mt CO2e")
    ax.set_title("Sector-level direct emissions (top movers)")
    ax.legend(fontsize=8)
    save(fig, "fig06_sector_trend.png")


def fig_lmdi(con):
    df = pd.read_csv(OUT / "ind_carbon_lmdi.csv")
    df = df[df.sector != "ALL"].head(10)
    fig, ax = plt.subplots(figsize=(8.2, 4.2))
    x = np.arange(len(df))
    ax.bar(x - 0.26, df.activity_effect_mt, width=0.26, label="activity", color=GREY)
    ax.bar(x, df.structure_effect_mt, width=0.26, label="structure", color=ORANGE)
    ax.bar(x + 0.26, df.intensity_effect_mt, width=0.26, label="intensity", color=BLUE)
    ax.scatter(x, df.delta_mt, color=RED, zorder=3, label="observed delta", s=28)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels([s[:22] for s in df.sector], rotation=28, ha="right",
                       fontsize=6.5)
    ax.set_ylabel("Mt CO2e (2019->2023)")
    ax.set_title("LMDI: what moved, decomposed")
    ax.legend(fontsize=7, ncol=4, loc="lower center", bbox_to_anchor=(0.5, -0.42),
              frameon=False)
    fig.tight_layout()
    fig.savefig(FIG / "fig07_lmdi.png", bbox_inches="tight")
    plt.close(fig)


def fig_units(con):
    df = q(con, "SELECT year, fuel_group, co2e_mt, capacity, n_units, "
                "co2e_per_capacity FROM fact_unit_aggregate WHERE fuel_group IN "
                "('Coal','Natural Gas','Petroleum') ORDER BY year")
    if df.empty:
        return
    # nameplate capacity is only internally consistent from 2013: the 2011-2012
    # vintages contain impossible values (one unit at 19.6 M mmBTU/hr, ~6 GW
    # thermal), so the capacity panels start there while emissions show all years
    cap = df[df.year >= 2013]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.4))
    for fuel, g in df.groupby("fuel_group"):
        axes[0].plot(g.year, g.co2e_mt, marker="o", ms=3, label=fuel)
    for fuel, g in cap.groupby("fuel_group"):
        axes[1].plot(g.year, g.capacity / 1e6, marker="o", ms=3, label=fuel)
        axes[2].plot(g.year, g.co2e_per_capacity, marker="o", ms=3, label=fuel)
    axes[0].set_title("unit-level emissions (Mt)")
    axes[1].set_title("reported capacity, 2013+ (1e6 mmBTU/hr)")
    axes[2].set_title("emissions per unit capacity, 2013+")
    for ax in axes:
        ax.set_xlabel("year")
    axes[0].legend(fontsize=7)
    fig.suptitle("Mechanism: coal vs gas units", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(FIG / "fig08_unit_mechanism.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# forest
# ---------------------------------------------------------------------------
def fig_biome_maps():
    fig, axes = plt.subplots(1, 3, figsize=(12, 4.4))
    for ax, tag in zip(axes, BIOMES):
        border = np.load(MASK_DIR / f"border_{tag}.npy")
        rows = np.flatnonzero(border.any(axis=1))
        cols = np.flatnonzero(border.any(axis=0))
        if not len(rows):
            continue
        i0, i1, j0, j1 = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1
        f16 = MASK_DIR / f"forest_{tag}_2016.npy"
        f23 = MASK_DIR / f"forest_{tag}_2023.npy"
        if not (f16.exists() and f23.exists()):
            ax.set_title(f"{tag}\n(no masks)")
            continue
        base = np.load(f16)[i0:i1, j0:j1]
        last = np.load(f23)[i0:i1, j0:j1]
        img = np.zeros(base.shape, dtype=np.int8)
        img[base] = 1
        img[base & ~last] = 2
        west, east = LON_MIN + j0 * GRID_RES, LON_MIN + j1 * GRID_RES
        north, south = LAT_MAX - i0 * GRID_RES, LAT_MAX - i1 * GRID_RES
        ax.imshow(img, cmap=ListedColormap(["white", GREEN, RED]), vmin=0, vmax=2,
                  extent=(west, east, south, north), aspect="equal",
                  interpolation="nearest")
        loss = int((base & ~last).sum())
        ax.set_title(f"{tag}\nforest 2016, cells lost by 2023: {loss:,}", fontsize=8)
        ax.grid(False)
    handles = [mpatches.Patch(color=GREEN, label="forest 2016"),
               mpatches.Patch(color=RED, label="lost by 2023")]
    fig.legend(handles=handles, loc="lower center", ncol=2, frameon=False)
    fig.suptitle("Remaining forest and loss, 2.2 km analysis grid", fontsize=10)
    fig.tight_layout(rect=(0, 0.06, 1, 0.95))
    fig.savefig(FIG / "fig09_biome_maps.png")
    plt.close(fig)


def fig_biome_annual(con):
    df = q(con, "SELECT biome, year, tier, forest_km2, core_pct, edge_density, "
                "n_patches FROM fact_biome_year ORDER BY biome, year")
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.4))
    for biome, g in df.groupby("biome"):
        g = g.sort_values("year")
        axes[0].plot(g.year, g.forest_km2 / 1e3, marker="o", ms=2.5, label=biome)
        axes[1].plot(g.year, g.core_pct, marker="o", ms=2.5, label=biome)
        axes[2].plot(g.year, g.edge_density, marker="o", ms=2.5, label=biome)
    axes[0].set_title("remaining forest (kha)")
    axes[1].set_title("core habitat share (%)")
    axes[2].set_title("edge density (index)")
    for ax in axes:
        ax.set_xlabel("year")
    axes[0].legend(fontsize=7)
    fig.suptitle("Annual PRODES series, every published year", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(FIG / "fig10_biome_annual.png")
    plt.close(fig)


def fig_connectivity(con):
    df = pd.read_csv(OUT / "ind_corridor_annual.csv")
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.4))
    for biome, g in df.groupby("biome"):
        axes[0].plot(g.year, g.eca_mha, marker="o", ms=3, label=biome)
        axes[1].plot(g.year, g.corridor_eca_mha, marker="o", ms=3, label=biome)
    axes[0].set_title("total connected forest (Mha)")
    axes[1].set_title("connected area outside the dominant block (Mha)")
    for ax in axes:
        ax.set_xlabel("year")
    axes[0].legend(fontsize=8)
    fig.suptitle("Connectivity signal, d = 5 km", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig.savefig(FIG / "fig11_connectivity.png")
    plt.close(fig)


def fig_sensitivity(con):
    df = q(con, "SELECT biome, year, tier, eca_minor_2km, eca_minor_5km, "
                "eca_minor_10km, eca_minor_20km FROM v_sensitivity_wide "
                "ORDER BY biome, year")
    if df.empty:
        return
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.4), sharey=False)
    for ax, (biome, g) in zip(axes, df.groupby("biome")):
        g = g.sort_values("year")
        for col, lab, c in [("eca_minor_2km", "d=2 km", RED),
                            ("eca_minor_5km", "d=5 km", ORANGE),
                            ("eca_minor_10km", "d=10 km", BLUE),
                            ("eca_minor_20km", "d=20 km", GREEN)]:
            if col in g and g[col].notna().any():
                ax.plot(g.year, g[col] / 1e6, marker="o", ms=2.5, label=lab, color=c)
        ax.set_title(biome)
        ax.set_xlabel("year")
        ax.set_ylabel("Mha")
    axes[0].legend(fontsize=7)
    fig.suptitle("Connected area outside the main block, four dispersal distances",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(FIG / "fig12_sensitivity.png")
    plt.close(fig)


def fig_loss_geometry(con):
    df = q(con, "SELECT * FROM fact_loss_annual ORDER BY biome, year")
    if df.empty:
        return
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.4))
    for biome, g in df.groupby("biome"):
        g = g.sort_values("year")
        axes[0].plot(g.year, g.loss_km2, marker="o", ms=2.5, label=biome)
        axes[1].plot(g.year, g.loss_inside_core_pct, marker="o", ms=2.5, label=biome)
        axes[2].plot(g.year, g.loss_touching_previous_pct, marker="o", ms=2.5,
                     label=biome)
    axes[0].set_title("annual loss (km2)")
    axes[1].set_title("share of loss inside core habitat (%)")
    axes[2].set_title("share of loss touching previous front (%)")
    for ax in axes:
        ax.set_xlabel("year")
    axes[0].legend(fontsize=7)
    fig.suptitle("What the clearing actually did, not just how much", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(FIG / "fig13_loss_geometry.png")
    plt.close(fig)


def fig_calibration(con):
    df = q(con, "SELECT * FROM fact_calibration ORDER BY biome, year")
    if df.empty:
        return
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.4))
    for biome, g in df.groupby("biome"):
        g = g.sort_values("year")
        axes[0].plot(g.year, g.raster_km2 / 1e3, ls="--", marker="o", ms=2,
                     label=f"{biome} raster")
        axes[0].plot(g.year, g.official_km2 / 1e3, ls="-", marker="s", ms=2,
                     label=f"{biome} official")
        axes[1].plot(g.year, g.raster_km2 / g.official_km2, marker="o", ms=2.5,
                     label=biome)
    axes[0].set_ylabel("cumulative deforested area (kha)")
    axes[1].axhline(1.0, color="k", lw=0.8, ls=":")
    axes[1].set_ylabel("raster / official")
    for ax in axes:
        ax.set_xlabel("year")
    axes[0].legend(fontsize=6, ncol=2)
    fig.suptitle("WMS rasterisation bias and the thinning that corrects it", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(FIG / "fig14_calibration.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# fire / DETER
# ---------------------------------------------------------------------------
def fig_fire(con):
    df = q(con, "SELECT * FROM fact_fire_biome_year ORDER BY biome_norm, year")
    if df.empty:
        return
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.4))
    for biome, g in df.groupby("biome_norm"):
        g = g.sort_values("year")
        axes[0].plot(g.year, g.n_foci, marker="o", ms=2.5, label=biome)
        axes[1].plot(g.year, g.foci_per_100km2_forest, marker="o", ms=2.5, label=biome)
        axes[2].plot(g.year, g.dry_season_share, marker="o", ms=2.5, label=biome)
    axes[0].set_title("active-fire detections")
    axes[1].set_title("detections per 100 km2 forest")
    axes[2].set_title("August-October share")
    for ax in axes:
        ax.set_xlabel("year")
    axes[0].legend(fontsize=7)
    fig.suptitle("Fire regime by biome, 2003-2024", fontsize=10)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(FIG / "fig15_fire_regime.png")
    plt.close(fig)


def fig_fire_seasonality(con):
    df = q(con, "SELECT * FROM fact_fire_climatology ORDER BY biome_norm, month")
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(6.6, 3.4))
    for biome, g in df.groupby("biome_norm"):
        ax.plot(g.month, g.n_foci_2003_2025 / 1e3, marker="o", ms=3, label=biome)
    ax.set_xticks(range(1, 13))
    ax.set_xlabel("month")
    ax.set_ylabel("detections, 2003-2024 (thousand)")
    ax.set_title("Fire season")
    ax.legend(fontsize=8)
    save(fig, "fig16_fire_seasonality.png")


def fig_fire_map(con):
    df = q(con, "SELECT grid_row, grid_col, SUM(n_foci) n FROM fact_fire_cell_year "
                "GROUP BY grid_row, grid_col")
    if df.empty:
        return
    rows = int(df.grid_row.max()) + 1
    cols = int(df.grid_col.max()) + 1
    grid = np.zeros((rows, cols), dtype=float)
    grid[df.grid_row.values, df.grid_col.values] = df.n.values
    fig, ax = plt.subplots(figsize=(7.2, 5.2))
    im = ax.imshow(np.log10(grid + 1), origin="upper", cmap="inferno",
                   extent=(LON_MIN, LON_MIN + cols * 0.1, LAT_MAX - rows * 0.1, LAT_MAX),
                   aspect="equal")
    cb = fig.colorbar(im, ax=ax, shrink=0.8)
    cb.set_label("log10 detections 2003-2024 (0.1 deg cell)")
    ax.set_xlabel("longitude")
    ax.set_ylabel("latitude")
    ax.set_title("Where the fire is")
    ax.grid(False)
    save(fig, "fig17_fire_map.png")


def fig_deter(con):
    df = q(con, "SELECT biome, year, month, n_alerts, alert_area_km2 FROM fact_deter_month")
    if df.empty:
        return
    df["t"] = df.year + (df.month - 0.5) / 12
    fig, axes = plt.subplots(1, 2, figsize=(11, 3.4))
    for biome, g in df.groupby("biome"):
        axes[0].plot(g.t, g.n_alerts, lw=0.9, label=biome)
        axes[1].plot(g.t, g.alert_area_km2, lw=0.9, label=biome)
    axes[0].set_title("DETER alerts per month")
    axes[1].set_title("alert area per month (km2)")
    for ax in axes:
        ax.set_xlabel("year")
    axes[0].legend(fontsize=8)
    fig.suptitle("Monthly clearing signal (note: no Atlantic Forest DETER layer exists)",
                 fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(FIG / "fig18_deter.png")
    plt.close(fig)


# ---------------------------------------------------------------------------
# species
# ---------------------------------------------------------------------------
def fig_species(con):
    df = pd.read_csv(OUT / "ind_species_retention_by_class.csv")
    if df.empty:
        return
    order = ["1_lost>5km2", "2_lost1-5km2", "3_stable", "4_gained"]
    groups = list(df.grp.unique())
    fig, axes = plt.subplots(1, len(groups), figsize=(12, 3.2), sharey=True,
                             squeeze=False)
    for ax, (grp, g) in zip(axes[0], df.groupby("grp")):
        piv = g.pivot_table(index="forest_change_class", columns="biome",
                            values="mean_retention")
        piv = piv.reindex([o for o in order if o in piv.index])
        piv.plot(kind="bar", ax=ax, legend=(grp == groups[0]))
        ax.set_title(grp)
        ax.set_xlabel("forest change class")
        ax.tick_params(axis="x", rotation=25)
    axes[0][0].set_ylabel("share of testable species retained")
    fig.suptitle("Species persistence by forest-loss class (0.1 deg test grid)",
                 fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(FIG / "fig19_species_retention.png")
    plt.close(fig)


def fig_species_scatter(con):
    df = q(con, "SELECT grp, d_forest_km2, retention, effort_p2 FROM fact_species_turnover")
    if df.empty:
        return
    fig, axes = plt.subplots(1, len(df.grp.unique()), figsize=(12, 3.2), sharey=True,
                             squeeze=False)
    for ax, (grp, g) in zip(axes[0], df.groupby("grp")):
        g = g.copy()
        g["bin"] = pd.cut(g.d_forest_km2, [-np.inf, -20, -10, -5, -1, 1, 10, np.inf])
        m = g.groupby("bin").agg(ret=("retention", "mean"), n=("retention", "size"))
        x = np.arange(len(m))
        ax.bar(x, m.ret, color=[RED if i < 4 else (GREY if i == 4 else GREEN)
                                for i in range(len(m))])
        for i, (r, n) in enumerate(zip(m.ret, m.n)):
            ax.text(i, r, f"{n}", ha="center", va="bottom", fontsize=6)
        ax.set_xticks(x)
        ax.set_xticklabels([str(i)[:12] for i in m.index], rotation=30, ha="right",
                           fontsize=6)
        ax.set_title(grp)
        ax.set_xlabel("forest change class (km2 per 11 km cell)")
    axes[0][0].set_ylabel("mean retention")
    fig.suptitle("Cell-level retention vs forest change (bar labels = cell counts)",
                 fontsize=9)
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(FIG / "fig20_species_forest.png")
    plt.close(fig)


def fig_logit(con):
    df = q(con, "SELECT * FROM fact_species_logit WHERE term = 'forest_loss_10km2'")
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(6.4, 3.2))
    y = np.arange(len(df))
    ax.errorbar(df.coef, y,
                xerr=[df.coef - df.ci_lo, df.ci_hi - df.coef],
                fmt="o", color=BLUE, ecolor=GREY, capsize=3)
    ax.axvline(0, color="k", lw=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels([f"{m} (n={int(n)})" for m, n in zip(df.model, df.n_cells)],
                       fontsize=8)
    ax.set_xlabel("log-odds of persistence per 10 km2 of forest lost")
    ax.set_title("Does losing forest predict losing species? (bootstrap 95% CI)")
    save(fig, "fig21_species_logit.png")


# ---------------------------------------------------------------------------
# context / meta
# ---------------------------------------------------------------------------
def fig_national(con):
    df = q(con, "SELECT year, co2, land_use_change_co2, total_ghg "
                "FROM fact_national_co2 WHERE country='Brazil' AND year BETWEEN 1990 AND 2025")
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(7, 3.6))
    ax.plot(df.year, df.co2, color=BLUE, label="fossil CO2", lw=1.4)
    ax.plot(df.year, df.land_use_change_co2, color=GREEN, label="land-use CO2", lw=1.4)
    ax.plot(df.year, df.total_ghg, color=GREY, ls="--", label="total GHG", lw=1.0)
    ax.axhline(0, color="k", lw=0.7)
    ax.set_xlabel("year")
    ax.set_ylabel("Mt CO2")
    ax.set_title("Brazil: the national frame the forest series sits in")
    ax.legend(fontsize=8)
    save(fig, "fig22_national_co2.png")


def fig_trend_forest(con):
    df = pd.read_csv(OUT / "ind_trend_summary.csv")
    df = df[df.series.isin(["forest_km2", "eca_minor_ha", "fire_foci", "loss_km2"])]
    if df.empty:
        return
    df = df[np.abs(df.pct_per_year) > 1e-9].copy()
    df["mean_value"] = df.slope_per_year / df.pct_per_year * 100.0
    df["ci_lo_pct"] = df.slope_ci_lo / df.mean_value * 100.0
    df["ci_hi_pct"] = df.slope_ci_hi / df.mean_value * 100.0
    df = df.dropna(subset=["ci_lo_pct", "ci_hi_pct"])
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(7, 3.6))
    labels = [f"{r.series} | {r.entity}" for r in df.itertuples()]
    y = np.arange(len(df))
    ax.errorbar(df.pct_per_year, y,
                xerr=[df.pct_per_year - df.ci_lo_pct, df.ci_hi_pct - df.pct_per_year],
                fmt="o", color=BLUE, ecolor=GREY, capsize=3)
    ax.axvline(0, color="k", lw=0.8)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel("Theil-Sen slope, % of the series mean per year (95% CI)")
    ax.set_title("Annual trend tests (Mann-Kendall)")
    save(fig, "fig23_trend.png")


def fig_coverage(con):
    df = pd.read_csv(OUT / "ind_data_coverage.csv")
    if df.empty:
        return
    df["period"] = pd.to_numeric(df.period, errors="coerce")
    df = df.dropna(subset=["period"])
    piv = df.pivot_table(index="source", columns="period", values="n", aggfunc="max")
    fig, ax = plt.subplots(figsize=(9, 3.2))
    data = np.log10(piv.fillna(0).values + 1)
    im = ax.imshow(data, aspect="auto", cmap="viridis")
    ax.set_xticks(np.arange(len(piv.columns)))
    ax.set_xticklabels([str(int(c)) for c in piv.columns], rotation=90, fontsize=6)
    ax.set_yticks(np.arange(len(piv.index)))
    ax.set_yticklabels(piv.index, fontsize=7)
    cb = fig.colorbar(im, ax=ax, shrink=0.85)
    cb.set_label("log10 rows (0 = absent)")
    ax.set_title("What each source contributes, per year")
    ax.grid(False)
    save(fig, "fig24_coverage.png")


def main() -> int:
    con = sqlite3.connect(DB)
    funcs = [fig_carbon_tiers, fig_pathway, fig_trajectory, fig_state_movers,
             fig_carbon_map, fig_sector_trend, fig_lmdi, fig_units, fig_biome_maps,
             fig_biome_annual, fig_connectivity, fig_sensitivity, fig_loss_geometry,
             fig_calibration, fig_fire, fig_fire_seasonality, fig_fire_map, fig_deter,
             fig_species, fig_species_scatter, fig_logit, fig_national,
             fig_trend_forest, fig_coverage]
    for fn in funcs:
        try:
            if fn.__code__.co_argcount == 1:
                fn(con)
            else:
                fn()
            print(f"  ok   {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {fn.__name__}: {type(exc).__name__} {str(exc)[:100]}")
    con.close()
    n = len(list(FIG.glob("*.png")))
    print(f"\n{n} figures in {FIG}")
    for p in sorted(FIG.glob("*.png")):
        print("  ", p.name, f"{p.stat().st_size/1e3:.0f} kB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
