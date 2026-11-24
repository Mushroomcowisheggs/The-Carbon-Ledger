"""Land-cover -> fragmentation -> connectivity, for every published PRODES year.

Two things happen here that matter for the result being trustworthy:

1. AREA CALIBRATION.  PRODES increments rasterised through WMS at ~2 km are
   inflated 3-5x (each polygon is drawn at a minimum of one pixel).  The official
   area is known from s02b, so the rendered mask is *thinned* to that area by
   keeping the cells with the highest local deforestation density.  That removes
   the isolated single-pixel artefacts and keeps the front geometry, which is
   exactly the information the connectivity metrics depend on.  When the render
   lands *below* the official area the mask is used as drawn and the shortfall
   is recorded in mask_calibration.csv.

2. PATTERN METRICS, EVERY YEAR.  The earlier version of this stage sampled five
   epochs.  That is enough to describe a difference but not to test a trend.
   With 18 (Amazonia), 20 (Cerrado) and 18 (Mata Atlantica) cumulative masks the
   pipeline can run Mann-Kendall / Theil-Sen (s09) on an annual series instead of
   comparing two points.

Sensitivity is computed in the same pass: effective connected area at four
dispersal distances (2/5/10/20 km), core share at three erosion radii, patch
metrics on a coarser 0.1 deg majority grid, and a +/-10% perturbation of the
area calibration target.  A result that only exists at one arbitrary threshold is
not a result, so the report quotes the range.

Outputs
  data/interim/biome_year_metrics.csv        one row per biome-year (+baseline)
  data/interim/biome_loss_annual.csv         annual increment, front, core erosion
  data/interim/corridor_progress.csv         annual ECA against the 500 kha target
  data/interim/mask_calibration.csv          raster vs official area, every year
  data/interim/connectivity_sensitivity.csv  d / core / coarse-grid / calibration
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd
from scipy import ndimage as ndi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (BIOMES, CORE_THRESHOLD_CELLS, CORRIDOR_TARGET_HA,  # noqa: E402
                    CORRIDOR_TARGET_YEAR, DATA_VINTAGE_YEAR, DISPERSAL_KM,
                    DISPERSAL_SENSITIVITY_KM, GRID_RES, INTERIM, LAT_MAX,
                    MASK_DIR, NROWS, PRODES_MIN_FEATURES)

CELL_KM = 111.32 * GRID_RES
CELL_KM2_EQ = CELL_KM ** 2
DISP_KM = DISPERSAL_KM["generic"]
COARSE_FACTOR = 5           # 0.1 deg majority grid for the resolution sensitivity
CALIB_PERTURBATION = 0.10   # +/-10% calibration target


def cell_area_2d() -> np.ndarray:
    lat_c = LAT_MAX - (np.arange(NROWS) + 0.5) * GRID_RES
    col = CELL_KM2_EQ * np.cos(np.radians(lat_c))
    return col[:, None]


def disk(radius: int) -> np.ndarray:
    y, x = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    return (x * x + y * y) <= radius * radius


def calibrate(raw: np.ndarray, area2d: np.ndarray, target_km2: float,
              smooth: int = 5) -> tuple[np.ndarray, float]:
    """Thin a binary mask to `target_km2`, keeping the densest clusters."""
    total = float((raw * area2d).sum())
    if total <= target_km2 or raw.sum() == 0 or target_km2 <= 0:
        return raw, total
    dens = ndi.uniform_filter(raw.astype(np.float64), size=smooth, mode="constant")
    flat_a = np.broadcast_to(area2d, raw.shape).ravel()
    idx = np.flatnonzero(raw.ravel())
    order = idx[np.argsort(-dens.ravel()[idx], kind="stable")]
    cum = np.cumsum(flat_a[order])
    k = int(np.searchsorted(cum, target_km2)) + 1
    keep = np.zeros(raw.size, dtype=bool)
    keep[order[:k]] = True
    out = keep.reshape(raw.shape)
    return out, float((out * area2d).sum())


def top_up(mask: np.ndarray, cand: np.ndarray, area2d: np.ndarray,
           target_km2: float, smooth: int = 5) -> np.ndarray:
    """Add the densest unused candidate cells until `mask` reaches target area.

    Needed because the WMS increment geometry alone does not always carry the
    official annual area: a cell lit in an earlier year's render can be the same
    cell a later polygon falls on, so some clearing has no *new* pixel to sit in.
    Without this the cumulative mask drifts below the official total (1.6% for
    the Atlantic Forest by 2025) and the forest series silently overstates.
    """
    cur = float((mask * area2d).sum())
    if cur >= target_km2:
        return mask
    need = target_km2 - cur
    dens = ndi.uniform_filter(cand.astype(np.float64), size=smooth, mode="constant")
    cand_only = cand & ~mask
    idx = np.flatnonzero(cand_only.ravel())
    if idx.size == 0:
        return mask
    order = idx[np.argsort(-dens.ravel()[idx], kind="stable")]
    flat_a = np.broadcast_to(area2d, mask.shape).ravel()
    cum = np.cumsum(flat_a[order])
    k = int(np.searchsorted(cum, need)) + 1
    out = mask.copy()
    out.flat[order[:min(k, idx.size)]] = True
    return out


def eca_components(forest: np.ndarray, area2d: np.ndarray, disp_km: float,
                   labels: np.ndarray, patch_area: np.ndarray) -> dict:
    """Distance-threshold connectivity without an O(n^2) pairwise step.

    Forest is dilated by disp/2; patches that land in the same dilated component
    form one functional cluster.  The connected area of clusters holding at least
    two patches is the 'effective connected area' (ECA).  `eca_minor` drops the
    single largest cluster, which is the part that responds to corridors and
    stepping stones rather than to how big the main block already is.
    """
    # ceil, not round: at 2.2 km cells a 2 km and a 5 km dispersal both round to
    # a one-cell dilation, which made two of the four sensitivity variants
    # identical.  Taking the smallest whole number of cells that reaches half the
    # dispersal distance keeps the variants distinct (d = 2/5/10/20 -> r = 1/2/3/5).
    r = max(1, int(np.ceil((disp_km / 2.0) / CELL_KM)))
    grown = ndi.binary_dilation(forest, structure=disk(r), border_value=0)
    glab, gn = ndi.label(grown, structure=np.ones((3, 3), dtype=bool))
    assign = np.zeros(int(labels.max()) + 1, dtype=np.int64)
    lab_flat = labels.ravel()
    sel = lab_flat > 0
    assign[lab_flat[sel]] = glab.ravel()[sel]
    cluster_area = np.bincount(assign[1:], weights=patch_area, minlength=gn + 1)
    cluster_n = np.bincount(assign[1:], minlength=gn + 1)
    multi = cluster_n >= 2
    eca = float(cluster_area[multi].sum())
    largest_i = int(np.argmax(cluster_area[1:])) + 1 if gn else 0
    big = float(cluster_area[largest_i]) if (gn and multi[largest_i]) else 0.0
    return dict(eca_km2=eca, eca_minor_km2=max(eca - big, 0.0),
                lcc_km2=float(cluster_area.max()) if gn else 0.0,
                n_clusters=int(multi.sum()), disp_km=disp_km, disp_cells=int(r))


def _labels(forest: np.ndarray, area2d: np.ndarray):
    labels, n = ndi.label(forest, structure=np.ones((3, 3), dtype=bool))
    pa = np.bincount(labels.ravel(),
                     weights=np.broadcast_to(area2d, forest.shape).ravel(),
                     minlength=n + 1)[1:]
    return labels, pa, n


def patch_metrics(forest: np.ndarray, area2d: np.ndarray) -> dict:
    if not forest.any():
        return dict(forest_km2=0.0, n_patches=0, mean_patch_ha=0.0, max_patch_ha=0.0,
                    p50_patch_ha=0.0, p90_patch_ha=0.0, pct_area_small_patches=0.0,
                    edge_density=0.0, core_pct=0.0, eca_ha=0.0, eca_minor_ha=0.0,
                    lcc_ha=0.0, n_clusters=0, perimeter_km=0.0, shape_index=0.0)
    struct = np.ones((3, 3), dtype=bool)
    labels, patch_area, n = _labels(forest, area2d)
    forest_km2 = float(patch_area.sum())

    eroded = ndi.binary_erosion(forest, structure=struct, border_value=0)
    edge_cells = float((forest & ~eroded).sum())
    perimeter_km = edge_cells * CELL_KM

    core = ndi.binary_erosion(forest, structure=disk(CORE_THRESHOLD_CELLS),
                              border_value=0)
    core_km2 = float((core * area2d).sum())

    comp = eca_components(forest, area2d, DISP_KM, labels, patch_area)
    ha = patch_area * 100.0
    p50 = float(np.percentile(ha, 50)) if n else 0.0
    p90 = float(np.percentile(ha, 90)) if n else 0.0
    small = patch_area < 10.0        # < 1000 ha
    pct_small = float(patch_area[small].sum() / forest_km2 * 100) if forest_km2 else 0.0

    return dict(
        forest_km2=forest_km2,
        n_patches=int(n),
        mean_patch_ha=float(patch_area.mean() * 100),
        max_patch_ha=float(patch_area.max() * 100),
        p50_patch_ha=p50, p90_patch_ha=p90,
        pct_area_small_patches=pct_small,
        edge_density=edge_cells / max(forest.sum(), 1),
        core_pct=float(core_km2 / max(forest_km2, 1e-9) * 100),
        eca_ha=comp["eca_km2"] * 100,
        eca_minor_ha=comp["eca_minor_km2"] * 100,
        lcc_ha=comp["lcc_km2"] * 100,
        n_clusters=comp["n_clusters"],
        perimeter_km=perimeter_km,
        shape_index=float(perimeter_km / max(np.sqrt(forest_km2), 1e-9)),
    )


def core_share(forest: np.ndarray, area2d: np.ndarray, radius: int) -> float:
    if not forest.any():
        return 0.0
    core = ndi.binary_erosion(forest, structure=disk(radius), border_value=0)
    return float((core * area2d).sum() / max((forest * area2d).sum(), 1e-9) * 100)


def coarse_patch_metrics(forest: np.ndarray, area2d: np.ndarray) -> dict:
    """Patch metrics after 5x5 majority aggregation to 0.1 deg."""
    h, w = forest.shape
    h5, w5 = h // COARSE_FACTOR, w // COARSE_FACTOR
    mean = forest[:h5 * COARSE_FACTOR, :w5 * COARSE_FACTOR].reshape(
        h5, COARSE_FACTOR, w5, COARSE_FACTOR).mean(axis=(1, 3))
    cforest = mean > 0.5
    carea = ((CELL_KM * COARSE_FACTOR) ** 2 *
             np.cos(np.radians(LAT_MAX - (np.arange(h5) + 0.5) * GRID_RES * COARSE_FACTOR))[:, None])
    labels, n = ndi.label(cforest, structure=np.ones((3, 3), dtype=bool))
    if n == 0:
        return dict(coarse_n_patches=0, coarse_mean_patch_ha=0.0, coarse_forest_km2=0.0)
    pa = np.bincount(labels.ravel(),
                     weights=np.broadcast_to(carea, cforest.shape).ravel(),
                     minlength=n + 1)[1:]
    return dict(coarse_n_patches=int(n), coarse_mean_patch_ha=float(pa.mean() * 100),
                coarse_forest_km2=float(pa.sum()))


def sensitivity_rows(tag: str, year: int, tier: str, forest: np.ndarray,
                     area2d: np.ndarray) -> list[dict]:
    labels, pa, _ = _labels(forest, area2d)
    out = []
    for d in DISPERSAL_SENSITIVITY_KM:
        comp = eca_components(forest, area2d, d, labels, pa)
        out.append(dict(biome=tag, year=year, tier=tier, variant=f"disp_{d:g}km",
                        metric="eca_minor_ha", value=comp["eca_minor_km2"] * 100))
    for radius in (3, 5, 8):
        out.append(dict(biome=tag, year=year, tier=tier, variant=f"core_r{radius}",
                        metric="core_pct", value=core_share(forest, area2d, radius)))
    return out


def main() -> int:
    official = pd.read_csv(INTERIM / "prodes_official_area.csv")
    annual = pd.read_csv(INTERIM / "prodes_annual_full.csv")
    area2d = cell_area_2d()
    rows, meta, calib, sens, loss_rows = [], {}, [], [], []

    for tag, cfg in BIOMES.items():
        print(f"\n=== {tag} ===", flush=True)
        border = np.load(MASK_DIR / f"border_{tag}.npy")
        nonforest = np.zeros_like(border)
        nf = MASK_DIR / f"nonforest_{tag}.npy"
        if nf.exists():
            nonforest = np.load(nf)
        baseline = np.load(MASK_DIR / f"baseline_{tag}.npy")
        base_year = cfg["baseline_year"]

        off = official[official.biome == tag]
        base_off = float(off[off.layer == "baseline"].total_km2.sum())
        yearly_off = (annual[annual.biome == tag]
                      .set_index("year").total_km2.sort_index())
        core_area = border & ~nonforest
        biome_km2 = float((core_area * area2d).sum())

        base_cal, base_km2 = calibrate(baseline & core_area, area2d, base_off)
        forest = core_area & ~base_cal
        m0 = patch_metrics(forest, area2d)
        m0.update(biome=tag, year=base_year, tier="baseline")
        m0.update(core_pct_r3=core_share(forest, area2d, 3),
                  core_pct_r8=core_share(forest, area2d, 8))
        m0.update(coarse_patch_metrics(forest, area2d))
        rows.append(m0)
        calib.append(dict(biome=tag, year=base_year,
                          raster_km2=float((baseline & core_area).sum() * area2d.mean()),
                          official_km2=base_off, used_km2=base_km2,
                          shortfall_km2=base_off - base_km2))
        sens.extend(sensitivity_rows(tag, base_year, "baseline", forest, area2d))
        print(f"  baseline {base_year}: forest {m0['forest_km2']/10:,.0f} kha "
              f"({m0['forest_km2']/biome_km2*100:.0f}% of {biome_km2/10:,.0f} kha) "
              f"patches {m0['n_patches']:,}  eca_minor {m0['eca_minor_ha']/1e6:.3f} Mha",
              flush=True)

        prev_forest, prev_inc, prev_patches = forest, np.zeros_like(forest), m0["n_patches"]
        last_cum_cal = base_cal
        years = sorted(int(y) for y in
                       annual[(annual.biome == tag)
                              & (annual.n_features >= PRODES_MIN_FEATURES)].year
                       if cfg["baseline_year"] <= int(y) <= DATA_VINTAGE_YEAR)
        # Nested calibration.  Calibrating each year's *cumulative* render on its
        # own re-selects which cells are deforested, so the difference between two
        # years is then dominated by re-selection rather than by clearing (with a
        # 1 M km2 baseline render and a 2 k km2 annual increment, the artefact is
        # ~300x the signal).  Instead each year's *increment* is thinned to the
        # official annual area and added to the previous year's cumulative mask,
        # which keeps the series nested and the annual loss exact.
        cum_cal = base_cal.copy()
        prev_raw = baseline.copy()
        for yr in years:
            f = MASK_DIR / f"cum{yr}_{tag}.npy"
            if not f.exists():
                print(f"  [{yr}] mask missing, skipped", flush=True)
                continue
            raw = np.load(f) | baseline
            inc_raw = raw & ~prev_raw & core_area & ~cum_cal
            inc_target = float(yearly_off.get(yr, 0.0))
            inc_cal, used_inc = calibrate(inc_raw, area2d, inc_target)
            cum_cal = cum_cal | inc_cal
            target = base_off + float(yearly_off[yearly_off.index <= yr].sum())
            # top up to the official cumulative area when the increment's raster
            # geometry cannot supply it (see top_up)
            cum_cal = top_up(cum_cal, raw & core_area, area2d, target)
            forest_yr = core_area & ~cum_cal
            last_cum_cal = cum_cal
            used = float((cum_cal * area2d).sum())
            m = patch_metrics(forest_yr, area2d)
            m.update(biome=tag, year=yr, tier="yearly")
            m.update(core_pct_r3=core_share(forest_yr, area2d, 3),
                     core_pct_r8=core_share(forest_yr, area2d, 8))
            m.update(coarse_patch_metrics(forest_yr, area2d))
            rows.append(m)
            calib.append(dict(biome=tag, year=yr,
                              raster_km2=float((raw & core_area).sum() * area2d.mean()),
                              official_km2=target, used_km2=used,
                              shortfall_km2=target - used))
            sens.extend(sensitivity_rows(tag, yr, "yearly", forest_yr, area2d))

            # ---- annual increment geometry ---------------------------------
            # cells cleared this year = cells in the cumulative mask that were
            # still forest last year
            inc = cum_cal & prev_forest
            prev_core = ndi.binary_erosion(prev_forest,
                                           structure=disk(CORE_THRESHOLD_CELLS),
                                           border_value=0)
            inc_in_core = float(((inc & prev_core) * area2d).sum())
            touching = 0.0
            if prev_inc.any():
                near = ndi.binary_dilation(prev_inc,
                                           structure=np.ones((3, 3), dtype=bool))
                touching = float(100.0 * (inc & near).sum() / max(inc.sum(), 1))
            loss_rows.append(dict(
                biome=tag, year=yr, loss_km2=float((inc * area2d).sum()),
                loss_inside_core_pct=(100.0 * inc_in_core / max((inc * area2d).sum(), 1e-9)),
                loss_touching_previous_pct=touching,
                forest_km2=float((forest_yr * area2d).sum()),
                loss_per_100km2=(100.0 * float((inc * area2d).sum()) /
                                 max(float((prev_forest * area2d).sum()), 1e-9)),
                d_patches=int(m["n_patches"] - prev_patches)))
            loss_rows[-1]["loss_kha"] = loss_rows[-1]["loss_km2"] / 10.0
            prev_forest, prev_inc, prev_patches = forest_yr, inc, m["n_patches"]
            prev_raw = raw
            print(f"  {yr}: target {target:>9,.0f} km2 "
                  f"(raster {calib[-1]['raster_km2']:>9,.0f}) -> forest "
                  f"{m['forest_km2']/10:>10,.0f} kha  patches {m['n_patches']:>6,}"
                  f"  eca_minor {m['eca_minor_ha']/1e6:>7.3f} Mha"
                  f"  loss {loss_rows[-1]['loss_km2']:>7,.0f} km2", flush=True)

        # calibration-uncertainty propagation on the latest year, applied to the
        # *cumulative* mask (perturb the total cleared area by +/-10%)
        latest = rows[-1]
        for factor in (1 - CALIB_PERTURBATION, 1 + CALIB_PERTURBATION):
            target = (base_off + float(yearly_off[yearly_off.index <= latest["year"]].sum()))
            calm, _ = calibrate(~last_cum_cal & core_area, area2d, target * factor)
            fr = core_area & ~calm
            labels, pa, _ = _labels(fr, area2d)
            comp = eca_components(fr, area2d, DISP_KM, labels, pa)
            sens.append(dict(biome=tag, year=int(latest["year"]), tier=latest["tier"],
                             variant=f"calib_{factor:.2f}", metric="eca_minor_ha",
                             value=comp["eca_minor_km2"] * 100))

        meta[tag] = {"baseline_year": cfg["baseline_year"],
                     "baseline_label": cfg["baseline_label"],
                     "years": years, "biome_km2": biome_km2, "n_years": len(years)}
        print(f"  {len(years)} annual masks used", flush=True)

    df = pd.DataFrame(rows)
    df.to_csv(INTERIM / "biome_year_metrics.csv", index=False)
    pd.DataFrame(calib).to_csv(INTERIM / "mask_calibration.csv", index=False)
    pd.DataFrame(loss_rows).to_csv(INTERIM / "biome_loss_annual.csv", index=False)
    pd.DataFrame(sens).to_csv(INTERIM / "connectivity_sensitivity.csv", index=False)

    prog = []
    for tag, g in df[df.tier == "yearly"].groupby("biome"):
        base = df[(df.biome == tag) & (df.tier == "baseline")].iloc[0]
        for _, r in g.sort_values("year").iterrows():
            prog.append(dict(biome=tag, year=int(r.year), eca_ha=float(r.eca_ha),
                             eca_baseline_ha=float(base.eca_ha),
                             eca_gain_ha=float(r.eca_ha - base.eca_ha),
                             eca_minor_ha=float(r.eca_minor_ha),
                             eca_minor_baseline_ha=float(base.eca_minor_ha),
                             minor_gain_ha=float(r.eca_minor_ha - base.eca_minor_ha),
                             target_ha=CORRIDOR_TARGET_HA,
                             target_year=CORRIDOR_TARGET_YEAR))
    pd.DataFrame(prog).to_csv(INTERIM / "corridor_progress.csv", index=False)
    (INTERIM / "biome_meta.json").write_text(json.dumps(meta, indent=2),
                                             encoding="utf-8")
    print(f"\nwrote biome_year_metrics.csv ({len(df)} rows), "
          f"biome_loss_annual.csv ({len(loss_rows)}), "
          f"connectivity_sensitivity.csv ({len(sens)}), corridor_progress.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
