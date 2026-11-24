"""Fetch the land-cover / deforestation layers used for the connectivity analysis.

Source: INPE TerraBrasilis PRODES (WFS + WMS).  Three biomes are covered:
Amazonia, Cerrado and Mata Atlantica.

Why a WMS rasterisation instead of the vector download: the PRODES increment
layers hold 0.8 M (Amazon) to 2.4 M (Cerrado) polygons, which is several GB of
GeoJSON that this link cannot carry.  Asking GeoServer to draw the layer onto a
grid-aligned canvas gives the same picture for a few hundred kB per year; the
cost is that features smaller than one pixel are drawn at one pixel, so areas
are biased *upwards* for sparse years.  This bias is measured and reported
(see s06), and the biome boundaries themselves are rasterised locally from the
WFS geometry, so the biome masks carry no such bias.
"""
from __future__ import annotations

import io
import json
import os
import sys
import time

import numpy as np
import pandas as pd
import requests
import shapely
from PIL import Image, ImageDraw
from shapely.geometry import shape

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (BIOMES, DATA_VINTAGE_YEAR, GRID_RES, INTERIM, LAT_MAX,  # noqa: E402
                    LAT_MIN, LON_MAX, LON_MIN, NCOLS, NROWS,
                    PRODES_MIN_FEATURES, RAW, TB, USER_AGENT,
                    WMS_ALPHA_THRESHOLD)

PRODES = RAW / "prodes"
PRODES.mkdir(parents=True, exist_ok=True)
MASK = RAW / "masks"
MASK.mkdir(parents=True, exist_ok=True)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


# ---------------------------------------------------------------------------
# grid helpers
# ---------------------------------------------------------------------------
def grid_axes():
    lon_c = LON_MIN + (np.arange(NCOLS) + 0.5) * GRID_RES
    lat_c = LAT_MAX - (np.arange(NROWS) + 0.5) * GRID_RES
    return lon_c, lat_c


def bbox_to_window(west: float, south: float, east: float, north: float):
    """Snap a lon/lat bbox outwards to whole grid cells."""
    j0 = max(0, int(np.floor((west - LON_MIN) / GRID_RES)))
    j1 = min(NCOLS, int(np.ceil((east - LON_MIN) / GRID_RES)))
    i0 = max(0, int(np.floor((LAT_MAX - north) / GRID_RES)))
    i1 = min(NROWS, int(np.ceil((LAT_MAX - south) / GRID_RES)))
    return i0, i1, j0, j1


def window_bbox(i0: int, i1: int, j0: int, j1: int):
    west = LON_MIN + j0 * GRID_RES
    east = LON_MIN + j1 * GRID_RES
    north = LAT_MAX - i0 * GRID_RES
    south = LAT_MAX - i1 * GRID_RES
    return west, south, east, north


# ---------------------------------------------------------------------------
# WFS: biome border polygons
# ---------------------------------------------------------------------------
def fetch_border(layer: str, tag: str) -> dict:
    out = PRODES / f"border_{tag}.geojson"
    if out.exists() and out.stat().st_size > 1000:
        return json.loads(out.read_text(encoding="utf-8"))
    ns = layer.split(":")[0]
    r = SESSION.get(f"{TB}/{ns}/ows", params={
        "service": "WFS", "version": "1.0.0", "request": "GetFeature",
        "typeName": layer, "outputFormat": "json"}, timeout=600)
    r.raise_for_status()
    out.write_text(r.text, encoding="utf-8")
    print(f"  border {tag}: {len(r.content)/1e6:.2f} MB")
    return r.json()


def _union(geojson: dict):
    """Geometry of the biome border.  The ring(s) are used ring-by-ring for
    rasterisation, so we never need a repaired/validated union here."""
    parts = [shape(f["geometry"]) for f in geojson["features"]]
    return shapely.union_all(parts) if len(parts) > 1 else parts[0]


def _rings(geom):
    """Yield (exterior, [interiors]) for every polygon in the geometry."""
    if geom.geom_type == "Polygon":
        yield geom.exterior, list(geom.interiors)
    elif geom.geom_type == "MultiPolygon":
        for g in geom.geoms:
            yield g.exterior, list(g.interiors)


def rasterize_geojson(geojson: dict, i0, i1, j0, j1) -> np.ndarray:
    """Rasterise WFS geometry onto the grid window with a scanline fill.

    PIL's polygon fill is C-speed and does not care about ring validity, which
    matters because the PRODES biome outlines contain self-intersections that
    make GEOS-based rasterisation impractically slow.  Holes are punched by
    painting the interior rings back to zero.
    """
    west, north = LON_MIN + j0 * GRID_RES, LAT_MAX - i0 * GRID_RES
    w, h = j1 - j0, i1 - i0
    img = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(img)

    def to_px(ring):
        return [((x - west) / GRID_RES, (north - y) / GRID_RES)
                for x, y in ring.coords]

    geom = _union(geojson)
    for ext, holes in _rings(geom):
        draw.polygon(to_px(ext), fill=1)
        for hole in holes:
            draw.polygon(to_px(hole), fill=0)
    return np.array(img, dtype=bool)


# ---------------------------------------------------------------------------
# WMS: PRODES polygon layers
# ---------------------------------------------------------------------------
def _render_piece(ns: str, layers: str, i0, i1, j0, j1, cql: str | None,
                  timeout: int) -> np.ndarray:
    west, south, east, north = window_bbox(i0, i1, j0, j1)
    params = {
        "service": "WMS", "version": "1.1.1", "request": "GetMap",
        "layers": layers, "styles": "", "srs": "EPSG:4326",
        "bbox": f"{west},{south},{east},{north}",
        "width": j1 - j0, "height": i1 - i0,
        "format": "image/png", "transparent": "true",
    }
    if cql:
        params["CQL_FILTER"] = cql
    r = SESSION.get(f"{TB}/{ns}/wms", params=params, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:100]}")
    alpha = np.array(Image.open(io.BytesIO(r.content)).convert("RGBA"))[..., 3]
    return alpha >= WMS_ALPHA_THRESHOLD


def render_mask(ns: str, layers: str, i0, i1, j0, j1, cql: str | None,
                dest, timeout: int = 420) -> np.ndarray:
    if dest.exists():
        return np.load(dest)
    # INPE's gateway returns 504 on large renders under load, so fall back to
    # rendering the window in narrower strips and stitching them together.
    splits = [(j0, j1)]
    if j1 - j0 > 700:
        mid = j0 + (j1 - j0) // 2
        splits = [(j0, mid), (mid, j1)]
    last = None
    for attempt in range(6):
        try:
            t0 = time.time()
            piece = np.zeros((i1 - i0, j1 - j0), dtype=bool)
            for (a, b) in splits:
                piece[:, a - j0:b - j0] = _render_piece(ns, layers, i0, i1, a, b,
                                                        cql, timeout)
            full = np.zeros((NROWS, NCOLS), dtype=bool)
            full[i0:i1, j0:j1] = piece
            np.save(dest, full)
            print(f"  {dest.stem:38s} {'(tiled)' if len(splits)>1 else '       '} "
                  f"{piece.sum():7d} px  {time.time()-t0:5.1f}s", flush=True)
            return full
        except Exception as exc:  # noqa: BLE001
            last = exc
            wait = 10 * (attempt + 1)
            print(f"    retry {attempt+1} {dest.stem}: {type(exc).__name__} "
                  f"{str(exc)[:60]} -> {wait}s", flush=True)
            time.sleep(wait)
    raise SystemExit(f"render failed: {dest.stem}: {last}")


# ---------------------------------------------------------------------------
def published_years(tag: str, cfg: dict) -> list[int]:
    """Years this biome actually publishes, from the exact WFS attribute sweep.

    s02b sweeps every year and records the feature count per year, so the mask
    stage can request exactly the published years instead of a fixed five
    snapshots (Amazon 18 years, Cerrado 20, Mata Atlantica 18).  A year with
    fewer than PRODES_MIN_FEATURES polygons is a stray record - Mata Atlantica
    2005 carries exactly one - and is dropped.
    """
    p = INTERIM / "prodes_annual_full.csv"
    if not p.exists():
        print(f"  [warn] no prodes_annual_full.csv; falling back to config years")
        return list(cfg["years"])
    df = pd.read_csv(p)
    g = df[(df.biome == tag) & (df.n_features >= PRODES_MIN_FEATURES)]
    years = sorted(int(y) for y in g.year
                   if cfg["baseline_year"] <= int(y) <= DATA_VINTAGE_YEAR)
    return years or list(cfg["years"])


def main() -> int:
    manifest = {}
    wanted = sys.argv[1:] or list(BIOMES)
    for tag, cfg in BIOMES.items():
        if tag not in wanted:
            continue
        ns = cfg["workspace"]
        print(f"\n=== {tag} ({ns}) ===", flush=True)
        gj = fetch_border(cfg["border"], tag)
        geom = _union(gj)
        west, south, east, north = geom.bounds
        i0, i1, j0, j1 = bbox_to_window(west, south, east, north)
        print(f"  bbox {west:.2f},{south:.2f},{east:.2f},{north:.2f} "
              f"-> window {i1-i0}x{j1-j0}", flush=True)

        border = rasterize_geojson(gj, i0, i1, j0, j1)
        fb = MASK / f"border_{tag}.npy"
        full = np.zeros((NROWS, NCOLS), dtype=bool)
        full[i0:i1, j0:j1] = border
        np.save(fb, full)
        print(f"  border cells: {full.sum():,}", flush=True)

        years = published_years(tag, cfg)
        entries = {"window": [i0, i1, j0, j1], "border": str(fb.name),
                   "baseline_year": cfg["baseline_year"], "years": years,
                   "baseline_label": cfg["baseline_label"], "masks": {}}
        print(f"  published years ({len(years)}): {years[:4]}...{years[-2:]}",
              flush=True)

        if cfg["nonforest"]:
            f = MASK / f"nonforest_{tag}.npy"
            render_mask(ns, cfg["nonforest"], i0, i1, j0, j1, None, f)
            entries["masks"]["nonforest"] = f.name

        f = MASK / f"baseline_{tag}.npy"
        render_mask(ns, cfg["baseline"], i0, i1, j0, j1, None, f)
        entries["masks"]["baseline"] = f.name

        for yr in years:
            # cumulative through `yr`: one render carries every increment so far,
            # which is both cheaper and less inflated than summing yearly renders
            f = MASK / f"cum{yr}_{tag}.npy"
            render_mask(ns, cfg["yearly"], i0, i1, j0, j1, f"year<={yr}", f)
            entries["masks"][f"cum{yr}"] = f.name

        manifest[tag] = entries

    (PRODES / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                          encoding="utf-8")
    print("\nmanifest written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
