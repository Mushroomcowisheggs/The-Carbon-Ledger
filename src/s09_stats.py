"""Statistics that turn the wider data into claims with error bars.

Everything here is a test on data already on disk; nothing downloads.  Five
blocks, each answering a question the descriptive indicators cannot:

1. TRENDS.  Mann-Kendall + Theil-Sen on every annual series the pipeline builds
   (forest area, connected area, patch counts, fire foci, DETER alerts).  With
   18-20 annual points, "went from A to B" becomes "slope with a confidence
   interval, and a p-value that accounts for ties".  Theil-Sen rather than OLS
   because several series have one anomalous year (2019-2021 fire, 2020 covid).
2. BOOTSTRAP.  Facility-level resampling for the carbon quantities (national
   change, state ranking, concentration) and cell-level resampling for the
   species contrast.  The point is that the uncertainty is *reported*, not that
   it is small.
3. LMDI.  Additive log-mean Divisia decomposition of the 2019->2023 change in
   direct emissions into activity (how many reporters), structure (which
   sectors) and intensity (emissions per reporter).  This is the difference
   between "emissions fell 9%" and "emissions fell 9% because 300 facilities
   stopped reporting while the survivors emitted more".
4. MECHANISM.  Unit-level capacity and fuel data: coal vs gas capacity,
   emissions per unit of capacity, and how much of the drop is units exiting
   rather than units cleaning up.
5. TRIANGULATION.  The same quantity measured by independent sources - PRODES
   increments vs DETER alerts vs OWID land-use CO2; GHGRP direct vs OWID US
   fossil CO2 - so a source-specific artefact shows up as a disagreement.

Outputs (data/interim/)
  stats_trends.csv          series, tau, p, Theil-Sen slope + CI
  stats_bootstrap.csv       quantity, estimate, CI
  stats_lmdi.csv            additive decomposition of the carbon change
  stats_units.csv           unit-level capacity / fuel / utilisation panel
  stats_fire.csv            fire regime by biome-year
  stats_triangulation.csv   cross-source comparison
  stats_summary.json        the headline numbers the report quotes
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (BIOMES, BOOTSTRAP_N, BOOTSTRAP_SEED, DATA_VINTAGE_YEAR,  # noqa: E402
                    INTERIM, OUT, PRODES_MIN_FEATURES)

RNG = np.random.default_rng(BOOTSTRAP_SEED)
# largest plausible nameplate capacity for one combustion unit, mmBTU/hr
# (10 000 mmBTU/hr ~ 3 GW thermal; the published file reaches 19.6 M by mistake)
CAPACITY_MAX = 100_000


# ---------------------------------------------------------------------------
# trend tests
# ---------------------------------------------------------------------------
def mann_kendall(x: np.ndarray) -> tuple[float, float, float]:
    """Mann-Kendall S, normal-approximation z and two-sided p (tie-corrected)."""
    n = len(x)
    if n < 4:
        return np.nan, np.nan, np.nan
    s = 0
    for i in range(n - 1):
        s += np.sign(x[i + 1:] - x[i]).sum()
    _, counts = np.unique(x, return_counts=True)
    ties = (counts * (counts - 1) * (2 * counts + 5)).sum()
    var = (n * (n - 1) * (2 * n + 5) - ties) / 18.0
    if var <= 0:
        return float(s), np.nan, np.nan
    if s > 0:
        z = (s - 1) / np.sqrt(var)
    elif s < 0:
        z = (s + 1) / np.sqrt(var)
    else:
        z = 0.0
    p = float(2 * (1 - stats.norm.cdf(abs(z))))
    return float(s), float(z), p


def theil_sen(y: np.ndarray, x: np.ndarray | None = None) -> tuple[float, float, float]:
    """Median pairwise slope with a percentile bootstrap CI."""
    n = len(y)
    if n < 4:
        return np.nan, np.nan, np.nan
    x = np.arange(n) if x is None else np.asarray(x, dtype=float)
    slopes = [(y[j] - y[i]) / (x[j] - x[i])
              for i in range(n - 1) for j in range(i + 1, n) if x[j] != x[i]]
    if not slopes:
        return np.nan, np.nan, np.nan
    slope = float(np.median(slopes))
    boots = []
    for _ in range(2000):
        idx = RNG.integers(0, n, n)
        xs, ys = x[idx], y[idx]
        if len(np.unique(xs)) < 2:
            continue
        s = [(ys[j] - ys[i]) / (xs[j] - xs[i])
             for i in range(n - 1) for j in range(i + 1, n) if xs[j] != xs[i]]
        if s:
            boots.append(np.median(s))
    boots = np.array(boots)
    lo, hi = (np.percentile(boots, [2.5, 97.5]) if len(boots) else (np.nan, np.nan))
    return slope, float(lo), float(hi)


def trends() -> pd.DataFrame:
    rows = []

    def add(series: str, entity: str, unit: str, years, values):
        d = pd.DataFrame({"year": list(years), "value": list(values)}).dropna()
        d = d.groupby("year").value.mean().sort_index()
        if len(d) < 6:
            return
        s, z, p = mann_kendall(d.values)
        slope, lo, hi = theil_sen(d.values, d.index.values)
        rows.append(dict(series=series, entity=entity, unit=unit, n_years=len(d),
                         first_year=int(d.index.min()), last_year=int(d.index.max()),
                         first_value=float(d.iloc[0]), last_value=float(d.iloc[-1]),
                         kendall_s=s, kendall_z=z, p_value=p,
                         slope_per_year=slope, slope_ci_lo=lo, slope_ci_hi=hi,
                         pct_per_year=(100 * slope / d.mean()) if d.mean() else np.nan))

    bym = pd.read_csv(INTERIM / "biome_year_metrics.csv")
    for tag, g in bym.groupby("biome"):
        g = g.sort_values("year")
        add("forest_km2", tag, "km2", g.year, g.forest_km2)
        add("eca_minor_ha", tag, "ha", g.year, g.eca_minor_ha)
        add("n_patches", tag, "count", g.year, g.n_patches)
        add("edge_density", tag, "index", g.year, g.edge_density)
        add("core_pct", tag, "%", g.year, g.core_pct)

    lp = INTERIM / "biome_loss_annual.csv"
    if lp.exists():
        loss = pd.read_csv(lp)
        for tag, g in loss.groupby("biome"):
            add("loss_km2", tag, "km2/yr", g.year, g.loss_km2)
            add("loss_inside_core_pct", tag, "%", g.year, g.loss_inside_core_pct)
            # PRODES Cerrado/Atlantic Forest publish two-year increments before
            # 2013, so a trend on the whole series measures the reporting cadence
            # as much as the clearing: the annual-only variant starts in 2013.
            ann = g[g.year >= 2013]
            add("loss_km2_annual", tag, "km2/yr", ann.year, ann.loss_km2)

    fp = INTERIM / "fire_biome_month.csv"
    if fp.exists():
        fire = pd.read_csv(fp)
        for tag in ("Amazônia", "Cerrado", "Mata Atlântica"):
            g = fire[fire.biome == tag].groupby("year").n_foci.sum()
            if len(g) > 5:
                add("fire_foci", tag, "detections", g.index, g.values)

    dp = INTERIM / "deter_biome_month.csv"
    if dp.exists():
        det = pd.read_csv(dp)
        for tag, g in det.groupby("biome"):
            g = g.groupby("year").n_alerts.sum()
            if len(g) > 4:
                add("deter_alerts", tag, "alerts", g.index, g.values)

    out = pd.DataFrame(rows)
    out.to_csv(INTERIM / "stats_trends.csv", index=False)
    return out


# ---------------------------------------------------------------------------
# bootstrap
# ---------------------------------------------------------------------------
def _boot_stat(values: np.ndarray, stat_fn, n: int = BOOTSTRAP_N):
    est = stat_fn(values)
    reps = np.array([stat_fn(values[RNG.integers(0, len(values), len(values))])
                     for _ in range(n)])
    reps = reps[np.isfinite(reps)]
    lo, hi = (np.percentile(reps, [2.5, 97.5]) if len(reps) > 10 else (np.nan, np.nan))
    return est, float(lo), float(hi)


def _hhi(x: np.ndarray) -> float:
    s = x / x.sum()
    return float((s ** 2).sum() * 10000)


def bootstraps() -> pd.DataFrame:
    rows = []
    ghg = pd.read_csv(INTERIM / "ghgrp_reporter_year.csv", low_memory=False)
    direct = ghg[(ghg.tier == "direct") & ghg.scope1_co2e.notna()]

    # national change 2019 -> 2023, resampling facilities
    pivot = (direct[direct.year.isin([2019, 2023])]
             .groupby(["facility_id", "year"]).scope1_co2e.sum()
             .unstack(fill_value=np.nan).dropna())
    delta = (pivot[2023] - pivot[2019]).values / 1e6
    est, lo, hi = _boot_stat(delta, np.sum, n=BOOTSTRAP_N)
    rows.append(dict(quantity="direct_delta_2019_2023_mt", estimate=est,
                     ci_lo=lo, ci_hi=hi, n=len(delta),
                     note="sum over facilities reporting in both years; "
                          "facility-level bootstrap"))
    # share of the change carried by the 100 largest decliners
    order = np.argsort(delta)
    rows.append(dict(quantity="decline_share_top100_facilities", estimate=float(
        -delta[order[:100]].sum() / max(-delta.sum(), 1e-9)), ci_lo=np.nan,
        ci_hi=np.nan, n=len(delta),
        note="share of the net decline contributed by the 100 largest decliners; "
             "> 1 means the rest of the panel increased"))

    # concentration (HHI) by tier in 2023
    for tier in ("direct", "supply", "fugitive"):
        x = direct[(direct.tier == tier) & (direct.year == 2023)].scope1_co2e.values
        if len(x) < 10:
            continue
        est, lo, hi = _boot_stat(x, _hhi, n=800)
        rows.append(dict(quantity=f"hhi_{tier}_2023", estimate=est, ci_lo=lo,
                         ci_hi=hi, n=len(x), note="Herfindahl index x 10000"))

    # state ranking stability: how often is the top decliner also top-3
    st = (direct[direct.year.isin([2019, 2023])]
          .groupby(["state", "year"]).scope1_co2e.sum().unstack().dropna())
    d = (st[2023] - st[2019])
    top = d.sort_values().index[0]
    reps = 0
    for _ in range(300):
        idx = RNG.integers(0, len(d), len(d))
        s = pd.Series(d.values[idx], index=d.index)
        if s.sort_values().index[0] == top:
            reps += 1
    rows.append(dict(quantity="top_declining_state_share_of_bootstraps",
                     estimate=reps / 300.0, ci_lo=np.nan, ci_hi=np.nan,
                     n=len(d), note=f"bootstrap over states; top = {top}"))

    # species retention contrast (cells)
    tp = INTERIM / "species_turnover_cells.csv"
    if tp.exists():
        t = pd.read_csv(tp)
        t["cls"] = np.where(t.d_forest_km2 < -5, "lost",
                            np.where(t.d_forest_km2 <= 1, "stable", "gained"))
        for grp, g in t.groupby("grp"):
            a = g[g.cls == "lost"].retention.values
            b = g[g.cls == "stable"].retention.values
            if len(a) < 10 or len(b) < 10:
                continue
            observed = a.mean() - b.mean()
            reps = []
            for _ in range(1000):
                ai = a[RNG.integers(0, len(a), len(a))]
                bi = b[RNG.integers(0, len(b), len(b))]
                reps.append(ai.mean() - bi.mean())
            reps = np.array(reps)
            rows.append(dict(quantity=f"retention_gap_pp_{grp}",
                             estimate=float(observed * 100),
                             ci_lo=float(np.percentile(reps, 2.5) * 100),
                             ci_hi=float(np.percentile(reps, 97.5) * 100),
                             n=len(a) + len(b),
                             note="lost(< -5km2) minus stable; cell bootstrap"))

    out = pd.DataFrame(rows)
    out.to_csv(INTERIM / "stats_bootstrap.csv", index=False)
    return out


# ---------------------------------------------------------------------------
# LMDI decomposition
# ---------------------------------------------------------------------------
def lmdi() -> pd.DataFrame:
    ghg = pd.read_csv(INTERIM / "ghgrp_reporter_year.csv", low_memory=False)
    d = ghg[(ghg.tier == "direct") & ghg.scope1_co2e.notna()
            & ghg.year.isin([2019, 2023])].copy()
    d["sector"] = (d["sectors"].fillna("unknown").astype(str)
                   .str.split(",").str[0].str.strip().replace("", "unknown"))
    g = d.groupby(["sector", "year"]).scope1_co2e.sum().unstack(fill_value=0.0)
    n = d.groupby(["sector", "year"]).scope1_co2e.size().unstack(fill_value=0)
    if g.shape[1] < 2:
        return pd.DataFrame()
    e0, e1 = g[2019].values, g[2023].values
    q0, q1 = n[2019].values.astype(float), n[2023].values.astype(float)
    tot0, tot1 = e0.sum(), e1.sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        L = np.where(np.abs(e1 - e0) > 1e-9, (e1 - e0) / np.log(e1 / e0), e0)
        L = np.nan_to_num(L)
        act = L * np.log((q1.sum() / q0.sum()) if q0.sum() and q1.sum() else 1.0)
        struct = L * np.log(np.where((q0.sum() > 0) & (q1.sum() > 0),
                                     (q1 / q1.sum()) / (q0 / q0.sum()), 1.0))
        inten = L * np.log(np.where((q0 > 0) & (q1 > 0), (e1 / q1) / (e0 / q0), 1.0))
    rows = [dict(sector="ALL", year_from=2019, year_to=2023,
                 emissions_from_mt=tot0 / 1e6, emissions_to_mt=tot1 / 1e6,
                 delta_mt=(tot1 - tot0) / 1e6,
                 activity_mt=act.sum() / 1e6, structure_mt=struct.sum() / 1e6,
                 intensity_mt=inten.sum() / 1e6)]
    for i, sec in enumerate(g.index):
        rows.append(dict(sector=sec, year_from=2019, year_to=2023,
                         emissions_from_mt=e0[i] / 1e6, emissions_to_mt=e1[i] / 1e6,
                         delta_mt=(e1[i] - e0[i]) / 1e6,
                         activity_mt=act[i] / 1e6, structure_mt=struct[i] / 1e6,
                         intensity_mt=inten[i] / 1e6))
    out = pd.DataFrame(rows).sort_values("delta_mt")
    out.to_csv(INTERIM / "stats_lmdi.csv", index=False)
    return out


# ---------------------------------------------------------------------------
# unit-level mechanism
# ---------------------------------------------------------------------------
FUEL_GROUP = {
    "coal": "Coal", "natural gas": "Natural Gas", "petroleum products": "Petroleum",
    "petroleum coke": "Petroleum", "blast furnace gas": "Process gas",
    "coke oven gas": "Process gas", "wood": "Biomass", "biomass": "Biomass",
    "landfill gas": "Biomass", "other biomass": "Biomass",
}


def units() -> pd.DataFrame:
    up = INTERIM / "ghgrp_units.csv"
    if not up.exists():
        return pd.DataFrame()
    u = pd.read_csv(up, low_memory=False)
    u["facility_id"] = pd.to_numeric(u.facility_id, errors="coerce").astype("Int64").astype(str)
    for c in ("co2", "ch4", "n2o", "capacity_mmbtu_hr"):
        u[c] = pd.to_numeric(u[c], errors="coerce")
    # The published nameplate capacity contains a handful of impossible values
    # (25 unit-years above 1e6 mmBTU/hr in 2011-2012, one at 19.6e6, which is
    # ~6 GW thermal for a single unit).  They are dropped from the aggregate and
    # counted, so the utilisation ratio is not driven by data-entry errors.
    cap_bad = int((u.capacity_mmbtu_hr > CAPACITY_MAX).sum())
    if cap_bad:
        print(f"  [warn] {cap_bad} unit-years with capacity > {CAPACITY_MAX:,.0f} "
              f"mmBTU/hr excluded from the capacity aggregate")
        u.loc[u.capacity_mmbtu_hr > CAPACITY_MAX, "capacity_mmbtu_hr"] = np.nan
    u["co2e"] = u[["co2", "ch4", "n2o"]].fillna(0).sum(axis=1)
    u["unit_key"] = u.facility_id + "|" + u.unit_name.fillna("")

    fu = INTERIM / "ghgrp_unit_fuel.csv"
    dom = pd.DataFrame()
    if fu.exists():
        f = pd.read_csv(fu, low_memory=False)
        f["facility_id"] = pd.to_numeric(f.facility_id, errors="coerce").astype("Int64").astype(str)
        f["unit_key"] = f.facility_id + "|" + f.unit_name.fillna("")
        f["fuel_group"] = (f.general_fuel.fillna("").str.lower()
                           .map(FUEL_GROUP).fillna("Other"))
        dom = (f.groupby("unit_key").fuel_group
               .agg(lambda s: s.mode().iat[0] if len(s.mode()) else "Other")
               .rename("fuel_group").reset_index())
    u = u.merge(dom, on="unit_key", how="left")
    u["fuel_group"] = u.fuel_group.fillna("Other")

    agg = (u.groupby(["year", "fuel_group"])
           .agg(n_units=("unit_key", "nunique"),
                co2e_mt=("co2e", lambda s: s.sum() / 1e6),
                capacity=("capacity_mmbtu_hr", "sum"))
           .reset_index())
    agg["co2e_per_capacity"] = agg.co2e_mt / agg.capacity.replace(0, np.nan)
    agg.to_csv(INTERIM / "stats_units.csv", index=False)

    # how much of the change is units that stopped reporting?
    panel = (u[u.year.isin([2015, 2023])]
             .groupby(["unit_key", "year"]).co2e.sum().unstack())
    panel = panel.reindex(columns=[2015, 2023])
    gone = panel[panel[2023].isna() & panel[2015].notna()][2015].sum() / 1e6
    new = panel[panel[2015].isna() & panel[2023].notna()][2023].sum() / 1e6
    same = panel.dropna()
    summary = dict(units_2015=int(panel[2015].notna().sum()),
                   units_2023=int(panel[2023].notna().sum()),
                   mt_from_units_that_stopped=float(gone),
                   mt_from_units_that_appeared=float(new),
                   mt_change_among_continuing=float((same[2023] - same[2015]).sum() / 1e6),
                   mt_2015_continuing=float(same[2015].sum() / 1e6),
                   mt_2023_continuing=float(same[2023].sum() / 1e6))
    (INTERIM / "stats_units_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    return agg


# ---------------------------------------------------------------------------
# fire regime
# ---------------------------------------------------------------------------
def biome_key(name: str) -> str:
    """'Mata Atlântica' -> 'MataAtlantica', matching the pipeline's biome keys.

    Stripping accents alone keeps the space, which silently broke the fire/forest
    join for that one biome (Amazonia and Cerrado have no space to lose).
    """
    import unicodedata
    return (unicodedata.normalize("NFKD", str(name))
            .encode("ascii", "ignore").decode("ascii")
            .replace(" ", "").replace("-", ""))


def fire_stats() -> pd.DataFrame:
    fp = INTERIM / "fire_biome_month.csv"
    if not fp.exists():
        return pd.DataFrame()
    fire = pd.read_csv(fp)
    fire = fire[fire.year <= DATA_VINTAGE_YEAR]
    fire["biome_norm"] = fire.biome.map(biome_key)
    bym = pd.read_csv(INTERIM / "biome_year_metrics.csv")
    bym["biome_norm"] = bym.biome.map(biome_key)
    forest = bym[["biome_norm", "year", "forest_km2"]]

    g = (fire.groupby(["biome_norm", "year"])
         .agg(n_foci=("n_foci", "sum"),
              n_states=("state", "nunique")).reset_index())
    dry = (fire[fire.month.isin([8, 9, 10])].groupby(["biome_norm", "year"])
           .n_foci.sum().rename("n_foci_aug_oct").reset_index())
    g = g.merge(dry, on=["biome_norm", "year"], how="left")
    g = g.merge(forest, on=["biome_norm", "year"], how="left")
    g["foci_per_100km2_forest"] = 100 * g.n_foci / g.forest_km2.replace(0, np.nan)
    g["dry_season_share"] = g.n_foci_aug_oct / g.n_foci.replace(0, np.nan)
    # monthly climatology
    clim = (fire.groupby(["biome_norm", "month"]).n_foci.sum()
            .rename("n_foci_2003_2025").reset_index())
    clim.to_csv(INTERIM / "stats_fire_climatology.csv", index=False)
    g.to_csv(INTERIM / "stats_fire.csv", index=False)

    # fire vs deforestation, same year and one year lag
    lp = INTERIM / "biome_loss_annual.csv"
    corr_rows = []
    if lp.exists():
        loss = pd.read_csv(lp)
        loss["biome_norm"] = loss.biome.map(biome_key)
        for tag, gb in g.groupby("biome_norm"):
            gl = loss[loss.biome_norm == tag][["year", "loss_km2"]]
            m = gb.merge(gl, on="year", how="inner").dropna(
                subset=["n_foci", "loss_km2"])
            if len(m) < 6:
                continue
            r0, p0 = stats.spearmanr(m.n_foci, m.loss_km2)
            m1 = m.assign(loss_lag=m.loss_km2.shift(-1)).dropna()
            r1, p1 = stats.spearmanr(m1.n_foci, m1.loss_lag)
            corr_rows.append(dict(biome=tag, n_years=len(m),
                                  spearman_same_year=r0, p_same_year=p0,
                                  spearman_fire_leads_loss=r1, p_fire_leads_loss=p1))
    if corr_rows:
        pd.DataFrame(corr_rows).to_csv(INTERIM / "stats_fire_corr.csv", index=False)
    return g


# ---------------------------------------------------------------------------
# cross-source triangulation
# ---------------------------------------------------------------------------
def triangulate() -> pd.DataFrame:
    rows = []
    owid = pd.read_csv(INTERIM / "owid_co2.csv", low_memory=False)
    brazil = owid[(owid.country == "Brazil") & owid.year.between(2000, 2025)]
    usa = owid[(owid.country == "United States") & owid.year.between(2000, 2025)]

    lp = INTERIM / "biome_loss_annual.csv"
    if lp.exists():
        loss = pd.read_csv(lp)
        tot = loss.groupby("year").loss_km2.sum()
        # 1 km2 of Amazon-type clearing ~ 0.02-0.03 Mt CO2 in the GCB accounting;
        # we deliberately do NOT impose a factor - instead the two series are
        # compared after normalising each to its own 2010-2019 mean, which tests
        # co-movement without baking in an emission factor.
        b = brazil.dropna(subset=["land_use_change_co2"]).set_index("year")
        for year in sorted(set(tot.index) & set(b.index)):
            rows.append(dict(pair="PRODES loss (km2) vs OWID land-use CO2 (Mt)",
                             year=int(year), value_a=float(tot.get(year, np.nan)),
                             value_b=float(b.loc[year, "land_use_change_co2"]),
                             unit_a="km2/year", unit_b="Mt CO2/year"))

    ghg = pd.read_csv(INTERIM / "ghgrp_reporter_year.csv", low_memory=False)
    direct = (ghg[(ghg.tier == "direct") & ghg.scope1_co2e.notna()]
              .groupby("year").scope1_co2e.sum() / 1e6)
    u = usa.dropna(subset=["co2"]).set_index("year")
    for year in sorted(set(direct.index) & set(u.index)):
        rows.append(dict(pair="GHGRP direct (Mt) vs OWID US fossil CO2 (Mt)",
                         year=int(year), value_a=float(direct.get(year, np.nan)),
                         value_b=float(u.loc[year, "co2"]),
                         unit_a="Mt CO2e (reported)", unit_b="Mt CO2 (inventory)"))

    dp = INTERIM / "deter_biome_month.csv"
    if lp.exists() and dp.exists():
        loss = pd.read_csv(lp)
        det = pd.read_csv(dp)
        det = det[det.year <= DATA_VINTAGE_YEAR]
        for biome, dg in det.groupby("biome"):
            dyear = dg.groupby("year").n_alerts.sum()
            lg = loss[loss.biome == biome].set_index("year").loss_km2
            for year in sorted(set(dyear.index) & set(lg.index)):
                rows.append(dict(pair=f"DETER alerts vs PRODES loss ({biome})",
                                 year=int(year), value_a=float(dyear.get(year)),
                                 value_b=float(lg.get(year)),
                                 unit_a="alerts", unit_b="km2"))

    out = pd.DataFrame(rows)
    out.to_csv(INTERIM / "stats_triangulation.csv", index=False)
    return out


def main() -> int:
    print("== trends ==")
    tr = trends()
    print(f"  {len(tr)} series tested; "
          f"{(tr.p_value < 0.05).sum()} significant at 5%")
    print("== bootstrap ==")
    bs = bootstraps()
    print(bs[["quantity", "estimate", "ci_lo", "ci_hi"]].round(3).to_string(index=False))
    print("== LMDI ==")
    lm = lmdi()
    if not lm.empty:
        tot = lm[lm.sector == "ALL"].iloc[0]
        print(f"  delta {tot.delta_mt:.1f} Mt = activity {tot.activity_mt:+.1f} "
              f"+ structure {tot.structure_mt:+.1f} + intensity {tot.intensity_mt:+.1f}")
    print("== units ==")
    un = units()
    if not un.empty:
        print(un[un.year.isin([2015, 2023])].round(3).to_string(index=False))
    print("== fire ==")
    fs = fire_stats()
    if not fs.empty:
        print(fs.tail(6).round(3).to_string(index=False))
    print("== triangulation ==")
    tg = triangulate()
    print(f"  {len(tg)} comparison points")

    summary = {
        "n_series_tested": int(len(tr)),
        "n_significant_trends": int((tr.p_value < 0.05).sum()),
        "n_bootstrap_quantities": int(len(bs)),
        "lmdi_total_delta_mt": (float(lm[lm.sector == "ALL"].iloc[0].delta_mt)
                                if not lm.empty else None),
        "n_triangulation_points": int(len(tg)),
    }
    (INTERIM / "stats_summary.json").write_text(json.dumps(summary, indent=2),
                                                encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
