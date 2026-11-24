"""Species range change, cell turnover, and how they line up with forest change.

Four groups now (primates, palms, bats, frogs), which changes what can be
claimed: with one group a 3-point retention difference could always be blamed on
that group's recording quirks, with four the question becomes whether the sign is
consistent across organisms with very different detectability and mobility.

Three things are easy to get wrong with opportunistic occurrence data:

1. Recording effort grows over time, so "absent in the 2010s" mostly means
   "nobody went there".  Handling it by requiring >=8 records in both periods
   (the first version) is a blunt filter: it keeps the well-sampled cells and
   throws away the frontier, which is exactly where forest loss happens.  Here
   effort enters as a covariate in a logistic model of persistence, and the
   retained-cell filter is kept only as a sensitivity variant.
2. A 2.2 km cell holds a fraction of a record, so occupancy is evaluated on a
   0.1 deg (~11 km) grid where sampling support is realistic.
3. Species differ in how threatened they are; the IUCN category carried by GBIF
   is used to check whether the persistence signal is concentrated in threatened
   taxa (which is what would make it a conservation signal rather than a
   sampling artefact).

Outputs
  species_range_change.csv     per-species occupied-cell change and centroid shift
  species_cell_occupancy.csv   per cell x period richness, effort, coverage
  species_turnover_cells.csv   per cell persistence + forest change + effort
  species_by_forest_change.csv retention rate grouped by forest-loss class
  species_logit.csv            persistence ~ forest loss + effort (+ per group)
  species_cross_taxon.csv      pairwise agreement of cell retention across groups
  species_iucn.csv             persistence and range change by IUCN category
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
from scipy import ndimage as ndi
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (BIOMES, BOOTSTRAP_SEED, GRID_RES, INTERIM, LAT_MAX,  # noqa: E402
                    LON_MIN, MASK_DIR, NCOLS, NROWS)

SPECIES_RES = 0.1
SCOLS = int(round(NCOLS * GRID_RES / SPECIES_RES))
SROWS = int(round(NROWS * GRID_RES / SPECIES_RES))
FACTOR = int(round(SPECIES_RES / GRID_RES))  # 5 grid cells per side
PERIOD_A, PERIOD_B = "2010-2016", "2017-2026"
YEAR_A, YEAR_B = 2016, 2023
MIN_RECORDS_SPECIES = 8   # per period, per species (main variant)
MIN_RECORDS_SPECIES_SENS = 4
MIN_SPECIES_PER_CELL = 3
CELL_KM = 111.32 * SPECIES_RES
RNG = np.random.default_rng(BOOTSTRAP_SEED)


def national_forest(year: int) -> np.ndarray:
    out = np.zeros((NROWS, NCOLS), dtype=bool)
    for tag in BIOMES:
        f = MASK_DIR / f"forest_{tag}_{year}.npy"
        if f.exists():
            out |= np.load(f)
    return out


def to_species_grid(mask: np.ndarray) -> np.ndarray:
    """Fraction of each 0.1 deg cell covered by the (bool) 0.02 deg mask."""
    h, w = SROWS * FACTOR, SCOLS * FACTOR
    m = np.zeros((h, w), dtype=np.float32)
    m[:mask.shape[0], :mask.shape[1]] = mask
    return m.reshape(SROWS, FACTOR, SCOLS, FACTOR).mean(axis=(1, 3))


def biome_grid() -> tuple[np.ndarray, dict]:
    codes = np.zeros((SROWS, SCOLS), dtype=np.int8)
    for k, tag in enumerate(BIOMES, start=1):
        f = MASK_DIR / f"border_{tag}.npy"
        if not f.exists():
            continue
        b = np.load(f)
        h, w = SROWS * FACTOR, SCOLS * FACTOR
        m = np.zeros((h, w), dtype=np.float32)
        m[:b.shape[0], :b.shape[1]] = b
        frac = m.reshape(SROWS, FACTOR, SCOLS, FACTOR).mean(axis=(1, 3))
        codes[(frac > 0.3) & (codes == 0)] = k
    return codes, {k: tag for k, tag in enumerate(BIOMES, start=1)}


def logit_fit(X: np.ndarray, y: np.ndarray, iters: int = 40) -> np.ndarray | None:
    """Plain IRLS logistic regression (no statsmodels on this interpreter)."""
    beta = np.zeros(X.shape[1])
    for _ in range(iters):
        eta = np.clip(X @ beta, -30, 30)
        mu = 1.0 / (1.0 + np.exp(-eta))
        w = np.clip(mu * (1 - mu), 1e-8, None)
        z = eta + (y - mu) / w
        XtW = X.T * w
        try:
            beta_new = np.linalg.solve(XtW @ X + 1e-6 * np.eye(X.shape[1]), XtW @ z)
        except np.linalg.LinAlgError:
            return None
        if np.max(np.abs(beta_new - beta)) < 1e-8:
            return beta_new
        beta = beta_new
    return beta


def bootstrap_coef(X: np.ndarray, y: np.ndarray, n: int = 400,
                   cluster: np.ndarray | None = None) -> np.ndarray:
    """Bootstrap distribution of the coefficient vector (rows = replicates)."""
    m = len(y)
    out = []
    for _ in range(n):
        if cluster is not None:
            idx_clusters = RNG.choice(np.unique(cluster), size=len(np.unique(cluster)),
                                      replace=True)
            idx = np.concatenate([np.flatnonzero(cluster == c) for c in idx_clusters])
        else:
            idx = RNG.integers(0, m, m)
        beta = logit_fit(X[idx], y[idx])
        if beta is not None:
            out.append(beta)
    return np.array(out) if out else np.empty((0, X.shape[1]))


def main() -> int:
    occ = pd.read_csv(INTERIM / "gbif_occurrences.csv", low_memory=False)
    occ = occ.dropna(subset=["species"])
    print(f"records loaded: {len(occ):,}  groups: "
          f"{occ.groupby('group').size().to_dict()}")
    occ["scol"] = ((occ["decimalLongitude"] - LON_MIN) / SPECIES_RES).astype(int)
    occ["srow"] = ((occ["decimalLatitude"] - LAT_MAX) / -SPECIES_RES).astype(int)
    occ = occ[(occ.scol >= 0) & (occ.scol < SCOLS) & (occ.srow >= 0) & (occ.srow < SROWS)]
    occ["scell"] = occ["srow"].astype(str) + "_" + occ["scol"].astype(str)

    codes, names = biome_grid()
    flat_codes = codes.ravel()
    occ["biome"] = [names.get(flat_codes[r * SCOLS + c]) if (0 <= r < SROWS and
                                                            0 <= c < SCOLS) else None
                    for r, c in zip(occ.srow, occ.scol)]
    print(f"records on grid: {len(occ):,}; with biome: {occ['biome'].notna().sum():,}")

    # ---------------- per cell x period -------------------------------------
    def coverage(g: pd.DataFrame) -> float:
        """Good-Turing sample coverage: 1 - (species seen once)/(all individuals)."""
        n = len(g)
        if n <= 1:
            return 0.0
        f1 = g.species.value_counts().eq(1).sum()
        return float(1 - f1 / n)

    rows = []
    for (grp, biome, cell, period), g in occ[occ.period.isin([PERIOD_A, PERIOD_B])].groupby(
            ["group", "biome", "scell", "period"], dropna=False):
        rows.append(dict(grp=grp, biome=biome, cell=cell, period=period,
                         n_records=len(g), n_species=g.species.nunique(),
                         coverage=coverage(g),
                         n_datasets=g.datasetKey.nunique() if "datasetKey" in g else np.nan))
    cellp = pd.DataFrame(rows)
    cellp.to_csv(INTERIM / "species_cell_occupancy.csv", index=False)
    print(f"cell-period rows: {len(cellp):,}")

    # ---------------- per species x period ----------------------------------
    sp = (occ[occ.period.isin([PERIOD_A, PERIOD_B])]
          .groupby(["group", "species", "period"])
          .agg(n_records=("species", "size"),
               n_cells=("scell", "nunique"),
               mean_lat=("decimalLatitude", "mean"),
               mean_lon=("decimalLongitude", "mean"),
               n_datasets=("datasetKey", "nunique"))
          .reset_index())
    wide = sp.pivot_table(index=["group", "species"], columns="period",
                          values=["n_records", "n_cells", "mean_lat", "mean_lon",
                                  "n_datasets"])
    wide.columns = [f"{a}_{b}" for a, b in wide.columns]
    wide = wide.reset_index()
    for c in wide.columns[2:]:
        wide[c] = pd.to_numeric(wide[c], errors="coerce")
    wide = wide.fillna(0)
    wide["testable"] = ((wide["n_records_" + PERIOD_A] >= MIN_RECORDS_SPECIES)
                        & (wide["n_records_" + PERIOD_B] >= MIN_RECORDS_SPECIES))
    wide["testable_sens"] = ((wide["n_records_" + PERIOD_A] >= MIN_RECORDS_SPECIES_SENS)
                             & (wide["n_records_" + PERIOD_B] >= MIN_RECORDS_SPECIES_SENS))
    wide["cell_change"] = wide["n_cells_" + PERIOD_B] - wide["n_cells_" + PERIOD_A]
    wide["cell_change_pct"] = np.where(
        wide["n_cells_" + PERIOD_A] > 0,
        100 * wide["cell_change"] / wide["n_cells_" + PERIOD_A], np.nan)
    dy = (wide["mean_lat_" + PERIOD_B] - wide["mean_lat_" + PERIOD_A]) * 111.32
    dx = (wide["mean_lon_" + PERIOD_B] - wide["mean_lon_" + PERIOD_A]) * 111.32
    wide["centroid_shift_km"] = np.hypot(dx, dy)
    wide["latitude_shift_km"] = dy

    # IUCN category: the modal category across a species' records
    if "iucn" in occ.columns:
        iucn = (occ[occ.iucn.fillna("") != ""]
                .groupby(["group", "species"]).iucn
                .agg(lambda s: s.mode().iat[0] if len(s.mode()) else ""))
        wide = wide.merge(iucn.rename("iucn").reset_index(),
                          on=["group", "species"], how="left")
    if "iucn" not in wide.columns:
        wide["iucn"] = ""
    wide["iucn"] = wide["iucn"].fillna("")
    wide.to_csv(INTERIM / "species_range_change.csv", index=False)
    testable = wide[wide.testable]
    print(f"testable species (>= {MIN_RECORDS_SPECIES} records in both periods): "
          f"{len(testable):,} / {len(wide):,}  "
          f"by group: {testable.groupby('group').size().to_dict()}")

    # ---------------- cell turnover over testable species -------------------
    keep = testable[["group", "species"]]
    o2 = occ[occ.period.isin([PERIOD_A, PERIOD_B])].merge(keep, on=["group", "species"])
    presence = (o2.groupby(["group", "species", "period", "scell", "biome"],
                           dropna=False).size().rename("n").reset_index())
    a = presence[presence.period == PERIOD_A]
    b = presence[presence.period == PERIOD_B]
    tested = (a[["group", "biome", "scell", "species"]]
              .merge(b[["scell", "species", "n"]].rename(columns={"n": "n_p2"}),
                     on=["scell", "species"], how="left"))
    tested["persisted"] = tested["n_p2"].notna()
    turn = (tested.groupby(["group", "biome", "scell"])
            .agg(n_species_tested=("species", "size"),
                 n_persisted=("persisted", "sum"))
            .reset_index())
    turn = turn[turn.n_species_tested >= MIN_SPECIES_PER_CELL]
    turn["retention"] = turn.n_persisted / turn.n_species_tested
    print(f"cells scored: {len(turn):,}")

    # ---------------- attach forest change and effort -----------------------
    fa, fb = national_forest(YEAR_A), national_forest(YEAR_B)
    frac_a, frac_b = to_species_grid(fa), to_species_grid(fb)
    cell_area = (111.32 * SPECIES_RES) ** 2 * np.cos(
        np.radians(LAT_MAX - (np.arange(SROWS) + 0.5) * SPECIES_RES))
    rc = turn["scell"].str.split("_", expand=True).astype(int)
    turn["srow"], turn["scol"] = rc[0].values, rc[1].values
    turn["forest_frac_p1"] = frac_a[turn.srow.values, turn.scol.values]
    turn["forest_frac_p2"] = frac_b[turn.srow.values, turn.scol.values]
    turn["cell_area_km2"] = cell_area[turn.srow.values]
    turn["forest_km2_p1"] = turn.forest_frac_p1 * turn.cell_area_km2
    turn["forest_km2_p2"] = turn.forest_frac_p2 * turn.cell_area_km2
    turn["d_forest_km2"] = turn.forest_km2_p2 - turn.forest_km2_p1

    # distance to the nearest forest edge in the later period, in km
    edge_km = np.full((SROWS, SCOLS), np.nan)
    if fb.any():
        dist = ndi.distance_transform_edt(~fb, sampling=(CELL_KM, CELL_KM))
        edge_small = dist.reshape(SROWS, FACTOR, SCOLS, FACTOR).mean(axis=(1, 3))
        edge_km = edge_small
    turn["dist_to_forest_km"] = edge_km[turn.srow.values, turn.scol.values]

    eff = (cellp[cellp.period == PERIOD_B]
           .groupby("cell")
           .agg(effort_p2=("n_records", "sum"), richness_p2=("n_species", "sum"),
                coverage_p2=("coverage", "mean")).reset_index())
    eff_a = (cellp[cellp.period == PERIOD_A]
             .groupby("cell")
             .agg(effort_p1=("n_records", "sum")).reset_index())
    turn = turn.merge(eff.rename(columns={"cell": "scell"}), on="scell", how="left")
    turn = turn.merge(eff_a.rename(columns={"cell": "scell"}), on="scell", how="left")
    turn["effort_p1"] = turn.effort_p1.fillna(0)
    turn["effort_p2"] = turn.effort_p2.fillna(0)
    turn = turn.rename(columns={"group": "grp"})
    turn.to_csv(INTERIM / "species_turnover_cells.csv", index=False)

    # ---------------- retention by forest-loss class ------------------------
    def cls(x):
        if x < -5:
            return "1_lost>5km2"
        if x < -1:
            return "2_lost1-5km2"
        if x <= 1:
            return "3_stable"
        return "4_gained"

    t = turn.copy()
    t["forest_change_class"] = t.d_forest_km2.map(cls)
    by = (t.groupby(["grp", "biome", "forest_change_class"])
          .agg(n_cells=("scell", "size"),
               mean_retention=("retention", "mean"),
               mean_species=("n_species_tested", "mean"),
               mean_effort=("effort_p2", "mean"))
          .reset_index())
    by.to_csv(INTERIM / "species_by_forest_change.csv", index=False)
    print("\nretention by forest-change class (unweighted cell means):")
    print(by.pivot_table(index="forest_change_class", columns="grp",
                         values="mean_retention").round(3).to_string())

    # ---------------- logistic model of persistence -------------------------
    # one row per (cell, group); persistence rate as binomial outcome
    X_rows, y_rows, grp_rows = [], [], []
    for _, r in t.iterrows():
        X_rows.append([1.0,
                       r.d_forest_km2 / 10.0,          # per 10 km2 of forest lost
                       np.log1p(r.effort_p2),          # sampling effort, later period
                       np.log1p(r.effort_p1),
                       float(r.forest_frac_p2 * 100)])  # remaining forest share
        y_rows.append(r.retention)                      # share persisted (0..1)
        grp_rows.append(r.grp)
    X = np.array(X_rows)
    y = np.array(y_rows)
    # weight each cell by how many species it contributes to the test
    wgt = t.n_species_tested.values / t.n_species_tested.values.sum()
    coef_names = ["intercept", "forest_loss_10km2", "log_effort_p2",
                  "log_effort_p1", "forest_share_pct"]

    def weighted_fit(Xb, yb, wb):
        """IRLS with binomial weights (cells carry different numbers of species)."""
        beta = np.zeros(Xb.shape[1])
        for _ in range(40):
            eta = np.clip(Xb @ beta, -30, 30)
            mu = 1.0 / (1.0 + np.exp(-eta))
            w = wb * np.clip(mu * (1 - mu), 1e-8, None)
            z = eta + (yb - mu) / np.clip(mu * (1 - mu), 1e-8, None)
            XtW = Xb.T * w
            try:
                beta_new = np.linalg.solve(XtW @ Xb + 1e-6 * np.eye(Xb.shape[1]), XtW @ z)
            except np.linalg.LinAlgError:
                return None
            if np.max(np.abs(beta_new - beta)) < 1e-8:
                return beta_new
            beta = beta_new
        return beta

    out_rows = []
    for label, mask in [("pooled", np.ones(len(y), dtype=bool))] + [
            (g, (t.grp == g).values) for g in sorted(t.grp.unique())]:
        if mask.sum() < 30:
            continue
        beta = weighted_fit(X[mask], y[mask], wgt[mask])
        if beta is None:
            continue
        # bootstrap over cells
        reps = []
        idx_pool = np.flatnonzero(mask)
        for _ in range(250):
            idx = RNG.choice(idx_pool, size=len(idx_pool), replace=True)
            b = weighted_fit(X[idx], y[idx], wgt[idx])
            if b is not None:
                reps.append(b)
        reps = np.array(reps)
        lo = np.percentile(reps, 2.5, axis=0) if len(reps) else np.full(len(beta), np.nan)
        hi = np.percentile(reps, 97.5, axis=0) if len(reps) else np.full(len(beta), np.nan)
        for j, name in enumerate(coef_names):
            out_rows.append(dict(model=label, n_cells=int(mask.sum()),
                                 term=name, coef=float(beta[j]),
                                 ci_lo=float(lo[j]), ci_hi=float(hi[j]),
                                 n_boot=len(reps)))
    pd.DataFrame(out_rows).to_csv(INTERIM / "species_logit.csv", index=False)
    print("\npersistence model, forest-loss term (per 10 km2), pooled and by group:")
    got = pd.DataFrame(out_rows)
    if not got.empty:
        sel = got[(got.term == "forest_loss_10km2")][["model", "coef", "ci_lo", "ci_hi"]]
        print(sel.round(4).to_string(index=False))

    # ---------------- cross-taxon agreement ---------------------------------
    # Pairwise on each pair's own intersection: a single pivot needing all three
    # groups at once leaves only a handful of cells (bats cover 555 cells, palms
    # 188 and primates 121, and their intersections are 26/33/11), so the pooled
    # version would silently report a correlation from four cells.
    wide_ret = t.pivot_table(index="scell", columns="grp", values="retention")
    groups = [c for c in wide_ret.columns]
    cross_rows = []
    for i, a in enumerate(groups):
        for b in groups[i + 1:]:
            sub = wide_ret[[a, b]].dropna()
            if len(sub) < 8:
                cross_rows.append(dict(grp_a=a, grp_b=b, spearman_r=np.nan,
                                       n_cells=len(sub)))
                continue
            r, p = stats.spearmanr(sub[a], sub[b])
            cross_rows.append(dict(grp_a=a, grp_b=b, spearman_r=r,
                                   n_cells=len(sub), p_value=p))
    cross = pd.DataFrame(cross_rows)
    cross.to_csv(INTERIM / "species_cross_taxon.csv", index=False)
    print("\ncross-taxon cell-retention correlation (pairwise intersections):")
    print(cross.round(3).to_string(index=False))

    # ---------------- IUCN --------------------------------------------------
    if "iucn" in wide.columns and wide.iucn.notna().any():
        m = wide.merge(t.groupby("grp").retention.mean().rename("cell_retention"),
                       left_on="group", right_index=True, how="left")
        order = ["CR", "EN", "VU", "NT", "LC", "DD", ""]
        g = (m[m.testable].groupby(["group", "iucn"])
             .agg(n_species=("species", "size"),
                  mean_cell_change_pct=("cell_change_pct", "mean"),
                  mean_centroid_shift_km=("centroid_shift_km", "mean"),
                  mean_cell_retention=("cell_retention", "mean"))
             .reset_index()
             .rename(columns={"group": "grp"}))
        g["iucn"] = pd.Categorical(g.iucn, categories=order, ordered=True)
        g.sort_values(["grp", "iucn"]).to_csv(INTERIM / "species_iucn.csv",
                                              index=False)
        print("\ntestable species by IUCN category:")
        print(g.groupby("iucn").n_species.sum().to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
