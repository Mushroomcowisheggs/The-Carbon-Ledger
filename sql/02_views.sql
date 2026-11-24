-- =====================================================================
-- Indexes and views, executed after the tables are loaded and before the
-- indicator queries.  Everything here assumes the loader ran; the runner
-- executes each statement separately and reports (rather than dies on) a
-- statement whose table is missing because a stage was skipped.
-- =====================================================================

CREATE INDEX IF NOT EXISTS ix_fact_carbon_year ON fact_carbon_year(year, tier);
CREATE INDEX IF NOT EXISTS ix_dim_reporter_state ON dim_reporter(state);
CREATE INDEX IF NOT EXISTS ix_dim_reporter_tier ON dim_reporter(tier);
CREATE INDEX IF NOT EXISTS ix_reporter_sector ON dim_reporter_sector(reporter_id, year);
CREATE INDEX IF NOT EXISTS ix_biome_year ON fact_biome_year(biome, year);
CREATE INDEX IF NOT EXISTS ix_loss_year ON fact_loss_annual(biome, year);
CREATE INDEX IF NOT EXISTS ix_sens_year ON fact_sensitivity(biome, year, variant);
CREATE INDEX IF NOT EXISTS ix_fire_bm ON fact_fire_biome_month(year, biome_name);
CREATE INDEX IF NOT EXISTS ix_fire_cell ON fact_fire_cell_year(year);
CREATE INDEX IF NOT EXISTS ix_deter_month ON fact_deter_month(biome, year);
CREATE INDEX IF NOT EXISTS ix_deter_alert ON fact_deter_alert(biome, year);
CREATE INDEX IF NOT EXISTS ix_national ON fact_national_co2(country, year);
CREATE INDEX IF NOT EXISTS ix_unit_year ON fact_unit_year(year, fuel_group);
CREATE INDEX IF NOT EXISTS ix_species_turn ON fact_species_turnover(grp, biome);
CREATE INDEX IF NOT EXISTS ix_species_cell ON fact_species_cell(grp, period);
CREATE INDEX IF NOT EXISTS ix_bootstrap ON fact_bootstrap(quantity);
CREATE INDEX IF NOT EXISTS ix_trend ON fact_trend(series, entity);

-- PRODES fire records carry the accented Portuguese biome name; the rest of the
-- pipeline uses the ASCII key.  One mapping table keeps every join honest.
DROP VIEW IF EXISTS v_biome_name_map;
CREATE VIEW v_biome_name_map AS
SELECT 'Amazônia' AS biome_name, 'Amazonia' AS biome
UNION ALL SELECT 'Cerrado', 'Cerrado'
UNION ALL SELECT 'Mata Atlântica', 'MataAtlantica'
UNION ALL SELECT 'Caatinga', 'Caatinga'
UNION ALL SELECT 'Pantanal', 'Pantanal'
UNION ALL SELECT 'Pampa', 'Pampa';

DROP VIEW IF EXISTS v_carbon_national;
CREATE VIEW v_carbon_national AS
SELECT year, tier,
       SUM(scope1_co2e) / 1e6 AS mt_co2e,
       COUNT(*)               AS n_reporters
FROM fact_carbon_year
GROUP BY year, tier;

DROP VIEW IF EXISTS v_biome_delta;
CREATE VIEW v_biome_delta AS
SELECT b.biome, b.year AS base_year,
       b.forest_km2 AS forest_base, b.eca_ha AS eca_base, b.core_pct AS core_base,
       l.year AS last_year, l.forest_km2 AS forest_last, l.eca_ha AS eca_last,
       l.core_pct AS core_last,
       (l.forest_km2 - b.forest_km2) AS d_forest_km2,
       (l.core_pct - b.core_pct)     AS d_core_pct
FROM fact_biome_year b
JOIN (SELECT f.* FROM fact_biome_year f
      JOIN (SELECT biome, MAX(year) AS y FROM fact_biome_year
            WHERE tier = 'yearly' GROUP BY biome) m
        ON m.biome = f.biome AND m.y = f.year
      WHERE f.tier = 'yearly') l ON l.biome = b.biome
WHERE b.tier = 'baseline';

DROP VIEW IF EXISTS v_corridor_status;
CREATE VIEW v_corridor_status AS
SELECT p.*,
       ROW_NUMBER() OVER (PARTITION BY p.biome ORDER BY p.year DESC) AS rn
FROM fact_corridor_progress p;

DROP VIEW IF EXISTS v_fire_year;
CREATE VIEW v_fire_year AS
SELECT m.biome, f.year, SUM(f.n_foci) AS n_foci
FROM fact_fire_biome_month f
JOIN v_biome_name_map m ON m.biome_name = f.biome_name
GROUP BY m.biome, f.year;

DROP VIEW IF EXISTS v_loss_vs_fire;
CREATE VIEW v_loss_vs_fire AS
SELECT l.biome, l.year, l.loss_km2, l.loss_inside_core_pct,
       f.n_foci,
       CASE WHEN l.forest_km2 > 0
            THEN 100.0 * f.n_foci / (l.forest_km2 / 100.0) END AS foci_per_100km2_forest
FROM fact_loss_annual l
LEFT JOIN v_fire_year f ON f.biome = l.biome AND f.year = l.year;

DROP VIEW IF EXISTS v_species_group;
CREATE VIEW v_species_group AS
SELECT grp, forest_change_class,
       SUM(n_cells) AS cells,
       SUM(mean_retention * n_cells) / SUM(n_cells) AS retention
FROM fact_species_retention
GROUP BY grp, forest_change_class;

DROP VIEW IF EXISTS v_sensitivity_wide;
CREATE VIEW v_sensitivity_wide AS
SELECT biome, year, tier,
       MAX(CASE WHEN variant = 'disp_2km'  AND metric = 'eca_minor_ha' THEN value END) AS eca_minor_2km,
       MAX(CASE WHEN variant = 'disp_5km'  AND metric = 'eca_minor_ha' THEN value END) AS eca_minor_5km,
       MAX(CASE WHEN variant = 'disp_10km' AND metric = 'eca_minor_ha' THEN value END) AS eca_minor_10km,
       MAX(CASE WHEN variant = 'disp_20km' AND metric = 'eca_minor_ha' THEN value END) AS eca_minor_20km,
       MAX(CASE WHEN variant = 'core_r3' AND metric = 'core_pct' THEN value END) AS core_r3,
       MAX(CASE WHEN variant = 'core_r5' AND metric = 'core_pct' THEN value END) AS core_r5,
       MAX(CASE WHEN variant = 'core_r8' AND metric = 'core_pct' THEN value END) AS core_r8
FROM fact_sensitivity
GROUP BY biome, year, tier;
