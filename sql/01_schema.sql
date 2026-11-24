-- =====================================================================
-- Core schema for the environmental analysis.
--
-- Only the tables whose shape is fixed by the pipeline are declared here;
-- the periodic facts that come straight out of a stage CSV (fire, DETER,
-- national CO2, units) are created by pandas at load time and indexed in
-- 02_views.sql, which runs after the load.  That keeps this file readable and
-- still lets the analytical views reference everything.
-- =====================================================================

DROP TABLE IF EXISTS dim_biome;
DROP TABLE IF EXISTS dim_reporter;
DROP TABLE IF EXISTS dim_reporter_sector;
DROP TABLE IF EXISTS dim_target;
DROP TABLE IF EXISTS dim_indicator;
DROP TABLE IF EXISTS dim_source;
DROP TABLE IF EXISTS dim_table_size;
DROP TABLE IF EXISTS fact_carbon_year;

CREATE TABLE dim_biome (
    biome          TEXT PRIMARY KEY,
    baseline_year  INTEGER,
    baseline_label TEXT,
    area_km2       REAL,
    n_years        INTEGER
);

CREATE TABLE dim_reporter (
    reporter_id   TEXT PRIMARY KEY,      -- facility_id | tier
    facility_id   TEXT NOT NULL,
    tier          TEXT NOT NULL,         -- direct | supply | fugitive | other
    facility_name TEXT,
    state         TEXT,
    county        TEXT,
    latitude      REAL,
    longitude     REAL,
    naics         TEXT,
    sectors       TEXT
);

-- The first-listed sector per reporter-year.  Kept as a separate dimension so
-- the LMDI decomposition can join without parsing the comma list in SQL.
CREATE TABLE dim_reporter_sector (
    reporter_id TEXT,
    year        INTEGER,
    sector      TEXT
);

CREATE TABLE fact_carbon_year (
    reporter_id            TEXT,     -- dim_reporter.reporter_id
    year                   INTEGER,
    tier                   TEXT,     -- denormalised: every indicator filters on it
    scope1_co2e            REAL,     -- reported direct emissions, t CO2e
    co2                    REAL,
    ch4                    REAL,
    n2o                    REAL,
    hfc                    REAL,
    pfc                    REAL,
    sf6                    REAL,
    nf3                    REAL,
    biogenic_co2           REAL,
    stationary_combustion  REAL,
    electricity_generation REAL
);

-- commitment side: Science Based Targets initiative target register
CREATE TABLE dim_target (
    company_name   TEXT,
    sector         TEXT,
    location       TEXT,
    region         TEXT,
    scope          TEXT,
    target_value   REAL,      -- percent reduction
    base_year      INTEGER,
    target_year    INTEGER,
    status         TEXT,
    classification TEXT,
    date_published TEXT
);

CREATE TABLE dim_indicator (
    indicator  TEXT PRIMARY KEY,
    label      TEXT,
    unit       TEXT,
    definition TEXT,
    source     TEXT,
    grain      TEXT
);

CREATE TABLE dim_source (
    source    TEXT PRIMARY KEY,
    url       TEXT,
    grain     TEXT,
    licence   TEXT,
    note      TEXT,
    retrieved TEXT
);

CREATE TABLE dim_table_size (
    table_name TEXT,
    rows       INTEGER
);
