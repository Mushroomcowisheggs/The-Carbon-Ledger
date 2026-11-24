"""Central configuration for the environmental analysis.

Everything here is a documented assumption or a documented source. No value
is hard-coded from a paper.

Layout
  * analysis grid
  * carbon        : US EPA GHGRP (facility-year, unit-level, SBTi targets)
  * national CO2  : OWID / Global Carbon Budget, World Bank forest area
  * forests       : INPE PRODES (annual + accumulated), DETER alerts
  * fire          : INPE BDQueimadas active-fire foci
  * species       : GBIF occurrences, four taxa
  * statistical settings (bootstrap, trend tests, sensitivity)
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
INTERIM = ROOT / "data" / "interim"
OUT = ROOT / "data" / "out"
FIG = ROOT / "reports" / "figures"
DB = ROOT / "db" / "environmental.db"
MASK_DIR = RAW / "masks"
VENDOR = ROOT / "vendor"   # local wheels (pyxlsb) so the run needs no global install
for _p in (RAW, INTERIM, OUT, FIG, DB.parent, MASK_DIR):
    _p.mkdir(parents=True, exist_ok=True)
if VENDOR.exists():
    sys.path.insert(0, str(VENDOR))

USER_AGENT = "The-Carbon-Ledger/1.0 (research; contact: local)"

# ----------------------------------------------------------------------------
# Offline reproduction
# ----------------------------------------------------------------------------
# The repository bundles every upstream payload under data/raw and the derived
# tables under data/interim.  With those files present a full run needs no
# network at all.  CARBON_LEDGER_OFFLINE=1 (set by `run_all.py --offline`) makes
# any stray request fail loudly instead of silently hitting a public server.
import os as _os

OFFLINE = _os.environ.get("CARBON_LEDGER_OFFLINE", "").strip().lower() in (
    "1", "true", "yes")

if OFFLINE:
    import requests as _requests

    def _block_network(*_a, **_kw):
        raise RuntimeError(
            "offline mode: a stage tried to open a network connection; "
            "the bundled local data for it is missing")

    _requests.Session.request = _block_network

# Last complete calendar year in the sources when this pipeline was written
# (servers reported 2026-11).  Partial current-year data is excluded from annual
# series: a half-year 2026 would otherwise look like a collapse in every trend.
DATA_VINTAGE_YEAR = 2025

# The Windows console here defaults to GBK, which raises on 'Amazônia' and on
# any Portuguese municipality name.  Every stage imports config, so reconfiguring
# once here keeps the log readable instead of crashing a stage after its files
# are already written.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

# ----------------------------------------------------------------------------
# Analysis grid
# ----------------------------------------------------------------------------
# 0.02 deg ~ 2.2 km at the equator.  Chosen because ecological corridors are
# tens of km wide and because the source polygons (PRODES) must be rasterised
# through INPE's WMS, where a ~2 km pixel keeps the request count affordable.
GRID_RES = 0.02
LON_MIN, LON_MAX = -75.0, -33.0
LAT_MIN, LAT_MAX = -35.0, 6.0
NCOLS = int(round((LON_MAX - LON_MIN) / GRID_RES))
NROWS = int(round((LAT_MAX - LAT_MIN) / GRID_RES))
WMS_ALPHA_THRESHOLD = 128  # >=50% pixel coverage counts as "present"

# ----------------------------------------------------------------------------
# Carbon: US EPA Greenhouse Gas Reporting Program (GHGRP)
# ----------------------------------------------------------------------------
GHGRP_ZIP_URL = ("https://www.epa.gov/system/files/other-files/2024-10/"
                 "2023_data_summary_spreadsheets.zip")
# unit-level (subparts C, D, AA) workbook: facility x unit x year, plus fuel mix
GHGRP_UNIT_ZIP_URL = ("https://www.epa.gov/system/files/other-files/2024-10/"
                      "emissions_by_unit_and_fuel_type_c_d_aa.zip")
GHGRP_YEARS = list(range(2010, 2024))
# "Direct Point Emitters" = Scope 1 reporters (subparts C, D, AA, ...).
# GHGRP does not observe Scope 3 at facility level: we build an explicit
# sector-level proxy from EPA's supply-chain emission factors (see s01).
GHGRP_SHEET = "Direct Point Emitters"
GHGRP_HEADER_ROW = 2  # 0-based; two preamble rows above the header

# Net-zero pathway used to score "decarbonisation progress".
BASE_YEAR = 2019
NET_ZERO_YEAR = 2050
CUT_2030 = 0.50  # 50% below base year by 2030 (linearised between anchors)
# second, independent benchmark: absolute reduction vs 2005 (Kyoto-style)
KYOTO_BASE_YEAR = 2005

# SBTi target register
SBTI_FILES = {
    "sbti_targets.xlsx": "https://files.sciencebasedtargets.org/production/files/targets-excel.xlsx",
    "sbti_companies.xlsx": "https://files.sciencebasedtargets.org/production/files/companies-excel.xlsx",
}

# ----------------------------------------------------------------------------
# National / global CO2 context (CSVs)
# ----------------------------------------------------------------------------
OWID_CO2_URL = "https://raw.githubusercontent.com/owid/co2-data/master/owid-co2-data.csv"
WB_INDICATORS = {
    "forest_km2": "AG.LND.FRST.K2",
    "forest_pct": "AG.LND.FRST.ZS",
    "protected_pct": "ER.LND.PTLD.ZS",
    "renewable_elec_pct": "EG.ELC.RNEW.ZS",
}
WB_URL = ("https://api.worldbank.org/v2/country/{countries}/indicator/{ind}"
          "?format=json&per_page=1000")
WB_COUNTRIES = "BRA;WLD"

# ----------------------------------------------------------------------------
# Biodiversity / land cover: INPE TerraBrasilis PRODES (WFS/WMS)
# ----------------------------------------------------------------------------
TB = "http://terrabrasilis.dpi.inpe.br/geoserver"
# biome key -> (workspace, border_layer, baseline_deforestation_layer, nonforest_layer)
# `yearly` is the annual-increment layer; its full-year range differs per biome
# (Amazon 2008+, Cerrado/Atlantic Forest are biennial before 2019).
BIOMES = {
    "Amazonia": dict(
        workspace="prodes-amazon-nb",
        border="prodes-amazon-nb:amazon_biome_border",
        baseline="prodes-amazon-nb:accumulated_deforestation_2007_biome",
        baseline_year=2007,
        nonforest="prodes-amazon-nb:no_forest_biome",
        yearly="prodes-amazon-nb:yearly_deforestation_biome",
        # superseded at run time by the year inventory in prodes_annual_full.csv
        years=[2008, 2009, 2010, 2011, 2012, 2013, 2014, 2015, 2016, 2017,
               2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025],
        baseline_label="PRODES accumulated deforestation <= 2007",
    ),
    "Cerrado": dict(
        workspace="prodes-cerrado-nb",
        border="prodes-cerrado-nb:biome_border",
        baseline="prodes-cerrado-nb:accumulated_deforestation_2000",
        baseline_year=2000,
        nonforest=None,
        yearly="prodes-cerrado-nb:yearly_deforestation",
        # biennial until 2018, annual afterwards; 2001-2005 is not published
        years=[2006, 2008, 2010, 2012, 2014, 2016, 2018, 2019, 2020, 2021,
               2022, 2023, 2024, 2025],
        baseline_label="PRODES accumulated deforestation <= 2000",
    ),
    "MataAtlantica": dict(
        workspace="prodes-mata-atlantica-nb",
        border="prodes-mata-atlantica-nb:biome_border",
        baseline="prodes-mata-atlantica-nb:accumulated_deforestation_2000",
        baseline_year=2000,
        nonforest=None,
        yearly="prodes-mata-atlantica-nb:yearly_deforestation",
        years=[2006, 2008, 2010, 2012, 2014, 2016, 2018, 2019, 2020, 2021,
               2022, 2023, 2024, 2025],
        baseline_label="PRODES accumulated deforestation <= 2000",
    ),
}
# Mata Atlantica workspace naming differs between INPE nodes; resolved at runtime.
MA_WORKSPACES = ["prodes-mata-atlantica-nb", "prodes-mata-atlantica"]

# Year window probed for published PRODES increments (inclusive).
PRODES_YEAR_MIN, PRODES_YEAR_MAX = 2000, 2026
# A year with a handful of features is a stray record, not an increment (Mata
# Atlantica 2005 carries exactly one), so the mask stage needs this floor.
PRODES_MIN_FEATURES = 10
# The Mata Atlantica accumulated layer carries ~249 k polygons in one snapshot and
# GeoServer's gateway 504s on a deep WFS offset, so its exact area is summed by
# partitioning on `state` instead of paging the whole layer.
PRODES_BASELINE_BY_STATE = {"MataAtlantica"}

# DETER = near-real-time alert system, the monthly-frequency counterpart of the
# annual PRODES inventory.  Layer names are resolved from GetCapabilities at run
# time; these are the prefixes INPE uses per biome.
DETER_LAYER_HINTS = {
    "Amazonia": ["deter_amz", "deter-amz"],
    "Cerrado": ["deter_cerrado", "deter-cerrado"],
    "MataAtlantica": ["deter_mata", "deter-mata"],
}

# ----------------------------------------------------------------------------
# Fire: INPE BDQueimadas active-fire foci (monthly CSVs by state)
# ----------------------------------------------------------------------------
FIRE_YEARS = list(range(2003, 2025))
# states covering the three biomes (acre .. tocantins subset is resolved by the
# pipeline from the data itself; the list below is only used for request scope)
FIRE_STATES = ["AC", "AM", "AP", "BA", "DF", "ES", "GO", "MA", "MG", "MS",
               "MT", "PA", "PE", "PI", "PR", "RJ", "RO", "RR", "RS", "SC",
               "SE", "SP", "TO"]

# ----------------------------------------------------------------------------
# Species: GBIF occurrence records (API)
# ----------------------------------------------------------------------------
GBIF = "https://api.gbif.org/v1"
# Three groups, chosen so that a cross-taxon check is possible: one vagile
# vertebrate order, one sedentary vertebrate order and one plant family.
# A fourth (Anura, taxonKey 952) was tried: 305 k Brazilian frog records take
# ~1 000 paged requests at 3-12 s each, which did not finish within this run, so
# it is left out rather than half-fetched.  Adding it is one line here - the
# analysis loops over whatever groups have a cleaned file.
GBIF_TARGETS = {
    "primates": dict(taxonKey=798, label="Primates (order)"),
    "palms": dict(taxonKey=7681, label="Arecaceae (family)"),
    "bats": dict(taxonKey=734, label="Chiroptera (order)"),
}
GBIF_FIELDS = [
    "key", "taxonKey", "speciesKey", "acceptedScientificName", "species",
    "scientificName", "taxonRank", "kingdom", "phylum", "class", "order",
    "family", "genus", "decimalLatitude", "decimalLongitude", "year", "month",
    "eventDate", "basisOfRecord", "datasetKey", "institutionCode",
    "coordinateUncertaintyInMeters", "occurrenceStatus", "stateProvince",
    "countryCode", "elevation", "iucnRedListCategory", "establishmentMeans",
    "issue",
]
GBIF_MAX_OFFSET = 100000
GBIF_PAGE = 300

# ----------------------------------------------------------------------------
# Corridor target.  No registry fixes this figure, so it is set
# explicitly here and treated throughout as a stated assumption rather than an
# observed quantity.
# ----------------------------------------------------------------------------
CORRIDOR_TARGET_HA = 500_000
CORRIDOR_TARGET_YEAR = 2030
TARGET_START_YEAR = 2008  # first full year of the Hansen loss series used

# Functional-connectivity parameters
DISPERSAL_KM = {"primates": 5.0, "generic": 10.0}
CORE_THRESHOLD_CELLS = 5  # erosion radius (cells) defining interior/core habitat

# ----------------------------------------------------------------------------
# Statistical settings (all documented, all seeded)
# ----------------------------------------------------------------------------
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20260101
TREND_ALPHA = 0.05
MIN_YEARS_FOR_TREND = 8
# connectivity sensitivity: alternative dispersal distances and coarser grids
DISPERSAL_SENSITIVITY_KM = (2.0, 5.0, 10.0, 20.0)
COARSE_GRID_FACTORS = (1, 5)   # 0.02 deg and 0.10 deg (block majority)
CORE_THRESHOLD_SENSITIVITY = (3, 5, 8)
