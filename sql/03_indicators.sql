-- =====================================================================
-- Indicator queries.  Each block starts with `-- >>> name` and is run by
-- src/s07_build_db.py; the result of each block is written to
-- data/out/ind_<name>.csv and listed in data/out/indicator_index.csv.
-- =====================================================================

-- >>> carbon_tier_trend
-- National reported totals per carbon stratum, indexed to the 2019 base year.
WITH base AS (
    SELECT tier, SUM(scope1_co2e) AS base_t
    FROM fact_carbon_year WHERE year = 2019 GROUP BY tier
)
SELECT f.year,
       f.tier,
       ROUND(SUM(f.scope1_co2e) / 1e6, 1)                    AS mt_co2e,
       ROUND(b.base_t / 1e6, 1)                              AS base2019_mt,
       ROUND(100.0 * (SUM(f.scope1_co2e) / b.base_t - 1), 1) AS pct_vs_base2019,
       COUNT(*)                                              AS n_reporters
FROM fact_carbon_year f
JOIN base b ON b.tier = f.tier
GROUP BY f.year, f.tier
ORDER BY f.tier, f.year;

-- >>> carbon_pathway_gap
-- Actual direct-emitter emissions against a linearised net-zero pathway
-- (base year 2019 -> -50% by 2030 -> net zero 2050).
WITH base AS (
    SELECT SUM(scope1_co2e) AS base_t FROM fact_carbon_year
    WHERE year = 2019 AND tier = 'direct'
), act AS (
    SELECT year, SUM(scope1_co2e) AS act_t FROM fact_carbon_year
    WHERE tier = 'direct' GROUP BY year
)
SELECT a.year,
       ROUND(a.act_t / 1e6, 1)                     AS actual_mt,
       ROUND(b.base_t / 1e6, 1)                    AS base2019_mt,
       ROUND((CASE WHEN a.year <= 2030
                   THEN b.base_t * (1 - 0.5 * (a.year - 2019) / 11.0)
                   ELSE b.base_t * 0.5 * (1 - (a.year - 2030) / 20.0)
              END) / 1e6, 1)                       AS required_mt,
       ROUND((a.act_t - (CASE WHEN a.year <= 2030
                   THEN b.base_t * (1 - 0.5 * (a.year - 2019) / 11.0)
                   ELSE b.base_t * 0.5 * (1 - (a.year - 2030) / 20.0)
              END)) / 1e6, 1)                      AS gap_mt
FROM act a CROSS JOIN base b
ORDER BY a.year;

-- >>> carbon_benchmark
-- Change against the first reporting year (2010), i.e. an absolute benchmark
-- that does not depend on the choice of 2019.
WITH y AS (
    SELECT year, SUM(scope1_co2e) / 1e6 AS mt
    FROM fact_carbon_year WHERE tier = 'direct' GROUP BY year
), b AS (SELECT mt AS base FROM y WHERE year = (SELECT MIN(year) FROM y)),
l AS (SELECT year, mt FROM y ORDER BY year DESC LIMIT 1)
SELECT (SELECT base FROM b)                                   AS base_year_mt,
       l.year                                                 AS latest_year,
       ROUND(l.mt, 1)                                         AS latest_mt,
       ROUND(l.mt - (SELECT base FROM b), 1)                  AS delta_mt,
       ROUND(100.0 * (l.mt - (SELECT base FROM b))
             / (SELECT base FROM b), 1)                       AS pct_vs_first_year
FROM l;

-- >>> carbon_state_movers
-- Which geographies actually moved, 2019 -> 2023 (direct emitters only).
WITH pivot AS (
    SELECT r.state,
           SUM(CASE WHEN f.year = 2019 THEN f.scope1_co2e END) AS y2019,
           SUM(CASE WHEN f.year = 2023 THEN f.scope1_co2e END) AS y2023
    FROM fact_carbon_year f JOIN dim_reporter r USING (reporter_id)
    WHERE f.tier = 'direct' AND f.year IN (2019, 2023)
    GROUP BY r.state
)
SELECT state,
       ROUND(y2019 / 1e6, 2)                        AS mt_2019,
       ROUND(y2023 / 1e6, 2)                        AS mt_2023,
       ROUND((y2023 - y2019) / 1e6, 2)              AS delta_mt,
       ROUND(100.0 * (y2023 - y2019) / NULLIF(y2019, 0), 1) AS pct_change,
       RANK() OVER (ORDER BY (y2023 - y2019) DESC)  AS rank_worst,
       RANK() OVER (ORDER BY (y2023 - y2019) ASC)   AS rank_best
FROM pivot
WHERE y2019 IS NOT NULL AND y2023 IS NOT NULL
ORDER BY ABS(y2023 - y2019) DESC
LIMIT 15;

-- >>> carbon_facility_trajectory
-- Facility-level trajectory classification for reporters present in both 2015
-- and 2023 (direct emitters).
WITH pair AS (
    SELECT reporter_id,
           MAX(CASE WHEN year = 2015 THEN scope1_co2e END) AS e2015,
           MAX(CASE WHEN year = 2023 THEN scope1_co2e END) AS e2023
    FROM fact_carbon_year
    WHERE tier = 'direct' AND year IN (2015, 2023)
    GROUP BY reporter_id
), cls AS (
    SELECT *, (e2023 - e2015) AS delta,
           CASE WHEN e2015 < 10000 THEN 'excluded_small'
                WHEN (e2023 - e2015) / e2015 <= -0.25 THEN 'A_cut>=25%'
                WHEN (e2023 - e2015) / e2015 <  -0.05 THEN 'B_cut5-25%'
                WHEN (e2023 - e2015) / e2015 <=  0.05 THEN 'C_flat'
                WHEN (e2023 - e2015) / e2015 <=  0.25 THEN 'D_up5-25%'
                ELSE 'E_up>25%' END AS trajectory
    FROM pair WHERE e2015 IS NOT NULL AND e2023 IS NOT NULL AND e2015 > 0
)
SELECT trajectory,
       COUNT(*)                            AS n_facilities,
       ROUND(SUM(e2023) / 1e6, 1)          AS mt_2023,
       ROUND(SUM(e2015) / 1e6, 1)          AS mt_2015,
       ROUND(AVG(delta), 0)                AS mean_delta_t,
       ROUND(SUM(e2023 - e2015) / 1e6, 1)  AS net_delta_mt
FROM cls
GROUP BY trajectory
ORDER BY trajectory;

-- >>> carbon_facility_movers
-- The 15 facilities with the largest absolute change 2019 -> 2023.
WITH pair AS (
    SELECT f.reporter_id,
           MAX(CASE WHEN year = 2019 THEN scope1_co2e END) AS e2019,
           MAX(CASE WHEN year = 2023 THEN scope1_co2e END) AS e2023
    FROM fact_carbon_year f
    WHERE f.tier = 'direct' AND f.year IN (2019, 2023)
    GROUP BY f.reporter_id
)
SELECT r.facility_name, r.state, r.sectors,
       ROUND(p.e2019 / 1e6, 2) AS mt_2019,
       ROUND(p.e2023 / 1e6, 2) AS mt_2023,
       ROUND((p.e2023 - p.e2019) / 1e6, 2) AS delta_mt
FROM pair p JOIN dim_reporter r USING (reporter_id)
WHERE p.e2019 IS NOT NULL AND p.e2023 IS NOT NULL
ORDER BY ABS(p.e2023 - p.e2019) DESC
LIMIT 15;

-- >>> carbon_concentration
-- How concentrated each stratum is: top-20 share and the Herfindahl index.
WITH y AS (
    SELECT tier, reporter_id, scope1_co2e
    FROM fact_carbon_year WHERE year = 2023
), ranked AS (
    SELECT tier, reporter_id, scope1_co2e,
           ROW_NUMBER() OVER (PARTITION BY tier ORDER BY scope1_co2e DESC) AS rn,
           SUM(scope1_co2e) OVER (PARTITION BY tier)                       AS tier_total,
           SUM(scope1_co2e * scope1_co2e) OVER (PARTITION BY tier)         AS tier_sq
    FROM y
)
SELECT tier,
       COUNT(*)                                                   AS n_reporters,
       ROUND(MAX(tier_total) / 1e6, 1)                            AS mt_total,
       ROUND(SUM(CASE WHEN rn <= 20 THEN scope1_co2e END) / 1e6, 1) AS mt_top20,
       ROUND(100.0 * SUM(CASE WHEN rn <= 20 THEN scope1_co2e END)
             / MAX(tier_total), 1)                                AS pct_top20,
       ROUND(MAX(tier_sq) / (MAX(tier_total) * MAX(tier_total)) * 10000, 0) AS hhi
FROM ranked GROUP BY tier ORDER BY tier;

-- >>> carbon_sector_trend
-- Sector-level direct emissions, 2019 vs 2023 (first-listed sector).
WITH s AS (
    SELECT d.sector, f.year, SUM(f.scope1_co2e) AS mt
    FROM fact_carbon_year f
    JOIN dim_reporter_sector d
      ON d.reporter_id = f.reporter_id AND d.year = f.year
    WHERE f.tier = 'direct' AND f.year IN (2019, 2023)
    GROUP BY d.sector, f.year
), p AS (
    SELECT sector,
           SUM(CASE WHEN year = 2019 THEN mt END) AS y19,
           SUM(CASE WHEN year = 2023 THEN mt END) AS y23
    FROM s GROUP BY sector
)
SELECT sector,
       ROUND(y19 / 1e6, 2) AS mt_2019,
       ROUND(y23 / 1e6, 2) AS mt_2023,
       ROUND((y23 - y19) / 1e6, 2) AS delta_mt,
       ROUND(100.0 * (y23 - y19) / NULLIF(y19, 0), 1) AS pct_change
FROM p
WHERE y19 IS NOT NULL AND y23 IS NOT NULL
ORDER BY ABS(y23 - y19) DESC
LIMIT 15;

-- >>> carbon_gas_mix
-- Reported gas mix of direct emitters.
SELECT year,
       ROUND(SUM(co2) / 1e6, 1)                                AS co2_mt,
       ROUND(SUM(ch4) / 1e6, 1)                                AS ch4_mt,
       ROUND(SUM(n2o) / 1e6, 1)                                AS n2o_mt,
       ROUND(SUM(COALESCE(hfc, 0) + COALESCE(pfc, 0)
                 + COALESCE(sf6, 0) + COALESCE(nf3, 0)) / 1e6, 1) AS fgas_mt,
       ROUND(100.0 * SUM(ch4) / SUM(scope1_co2e), 2)           AS ch4_share_pct,
       ROUND(100.0 * SUM(COALESCE(hfc, 0) + COALESCE(pfc, 0)
                 + COALESCE(sf6, 0) + COALESCE(nf3, 0))
             / SUM(scope1_co2e), 2)                            AS fgas_share_pct
FROM fact_carbon_year
WHERE tier = 'direct'
GROUP BY year ORDER BY year;

-- >>> carbon_entry_exit
-- Reporter churn: how much of the year-on-year movement is the reporting frame
-- changing rather than the surviving facilities emitting differently.
-- Written with window functions: the correlated-subquery version scanned the
-- 12 k x 14 reporter-year set once per row and never finished.
WITH y AS (
    SELECT DISTINCT reporter_id, year FROM fact_carbon_year WHERE tier = 'direct'
), p AS (
    SELECT reporter_id, year,
           LAG(year)  OVER (PARTITION BY reporter_id ORDER BY year) AS prev,
           LEAD(year) OVER (PARTITION BY reporter_id ORDER BY year) AS nxt
    FROM y
)
SELECT year,
       COUNT(*)                                                   AS n_reporters,
       SUM(CASE WHEN prev IS NULL OR prev <> year - 1 THEN 1 ELSE 0 END) AS new_reporters,
       SUM(CASE WHEN year < (SELECT MAX(year) FROM y)
                 AND (nxt IS NULL OR nxt <> year + 1) THEN 1 ELSE 0 END)  AS reporters_that_stop
FROM p
GROUP BY year
ORDER BY year;

-- >>> carbon_unit_mechanism
-- Unit-level capacity and emissions by fuel group: does the drop come from
-- coal units closing, or from the same units running less?
SELECT year, fuel_group, n_units,
       ROUND(co2e_mt, 2)                AS co2e_mt,
       ROUND(capacity, 0)               AS capacity_mmbtu_hr,
       ROUND(co2e_per_capacity, 4)      AS co2e_per_mmbtu_capacity
FROM fact_unit_aggregate
WHERE year IN (2010, 2015, 2019, 2023)
ORDER BY year, co2e_mt DESC;

-- >>> carbon_lmdi
-- Additive decomposition of the 2019 -> 2023 change: activity + structure +
-- intensity.  The three effects add up to the observed delta for every row.
SELECT sector,
       ROUND(emissions_from_mt, 2) AS mt_2019,
       ROUND(emissions_to_mt, 2)   AS mt_2023,
       ROUND(delta_mt, 2)          AS delta_mt,
       ROUND(activity_mt, 2)       AS activity_effect_mt,
       ROUND(structure_mt, 2)      AS structure_effect_mt,
       ROUND(intensity_mt, 2)      AS intensity_effect_mt
FROM fact_lmdi
ORDER BY ABS(delta_mt) DESC;

-- >>> carbon_bootstrap
-- Facility-resampled confidence intervals for the headline carbon quantities.
SELECT quantity,
       ROUND(estimate, 3) AS estimate,
       ROUND(ci_lo, 3)    AS ci_lo,
       ROUND(ci_hi, 3)    AS ci_hi,
       n, note
FROM fact_bootstrap
ORDER BY quantity;

-- >>> biome_change
-- Forest area, fragmentation and connectivity change per biome, baseline vs the
-- latest published year.
SELECT b.biome,
       b.year                                                   AS base_year,
       l.year                                                   AS last_year,
       ROUND(b.forest_km2 / 10, 0)                              AS base_kha,
       ROUND(l.forest_km2 / 10, 0)                              AS last_kha,
       ROUND(100.0 * (l.forest_km2 - b.forest_km2) / b.forest_km2, 2) AS forest_pct_chg,
       b.n_patches                                              AS patches_base,
       l.n_patches                                              AS patches_last,
       ROUND(b.mean_patch_ha, 0)                                AS mean_patch_base,
       ROUND(l.mean_patch_ha, 0)                                AS mean_patch_last,
       ROUND(b.p50_patch_ha, 0)                                 AS p50_patch_base,
       ROUND(l.p50_patch_ha, 0)                                 AS p50_patch_last,
       ROUND(b.edge_density, 4)                                 AS edge_base,
       ROUND(l.edge_density, 4)                                 AS edge_last,
       ROUND(b.core_pct, 1)                                     AS core_pct_base,
       ROUND(l.core_pct, 1)                                     AS core_pct_last,
       ROUND(l.core_pct - b.core_pct, 1)                        AS core_pp_chg,
       ROUND(b.shape_index, 2)                                  AS shape_base,
       ROUND(l.shape_index, 2)                                  AS shape_last,
       ROUND(l.pct_area_small_patches, 1)                       AS pct_small_patches_last,
       ROUND(b.eca_ha / 1e6, 1)                                 AS eca_mha_base,
       ROUND(l.eca_ha / 1e6, 1)                                 AS eca_mha_last
FROM fact_biome_year b
JOIN (SELECT f.* FROM fact_biome_year f
      JOIN (SELECT biome, MAX(year) AS y FROM fact_biome_year
            WHERE tier = 'yearly' GROUP BY biome) m
        ON m.biome = f.biome AND m.y = f.year
      WHERE f.tier = 'yearly') l ON l.biome = b.biome
WHERE b.tier = 'baseline'
ORDER BY b.biome;

-- >>> corridor_status
-- Progress against the 500 000 ha connected-area target.  Two readings: total
-- connected forest (`eca`) and connected forest outside the dominant block
-- (`eca_minor`), which is the one that responds to corridors themselves.
SELECT biome, year AS latest_year,
       ROUND(eca_baseline_ha / 1e3, 0)            AS eca_base_kha,
       ROUND(eca_ha / 1e3, 0)                     AS eca_latest_kha,
       ROUND(eca_gain_ha / 1e3, 0)                AS net_gain_kha,
       ROUND(100.0 * eca_gain_ha / target_ha, 1)  AS pct_of_target_total,
       ROUND(eca_minor_ha / 1e3, 0)               AS fragmented_connected_kha,
       ROUND(minor_gain_ha / 1e3, 0)              AS corridor_gain_kha,
       target_ha                                  AS target_ha,
       ROUND(100.0 * minor_gain_ha / target_ha, 1) AS pct_of_target,
       ROUND((target_ha - minor_gain_ha) / 1e3, 0) AS remaining_kha,
       (target_year - year)                       AS years_left
FROM v_corridor_status WHERE rn = 1
ORDER BY biome;

-- >>> corridor_annual
-- Annual connected-area series per biome (the progress curve).
SELECT biome, year,
       ROUND(eca_ha / 1e6, 2)          AS eca_mha,
       ROUND(eca_minor_ha / 1e6, 3)    AS corridor_eca_mha,
       ROUND(minor_gain_ha / 1e3, 0)   AS corridor_gain_kha,
       ROUND(100.0 * minor_gain_ha / target_ha, 1) AS pct_of_target
FROM fact_corridor_progress
ORDER BY biome, year;

-- >>> fragmentation_yoy
-- Year-on-year fragmentation change, with a window function for the trend.
SELECT biome, year,
       n_patches,
       n_patches - LAG(n_patches) OVER (PARTITION BY biome ORDER BY year) AS d_patches,
       ROUND(edge_density, 4) AS edge_density,
       ROUND(100.0 * edge_density
             / LAG(edge_density) OVER (PARTITION BY biome ORDER BY year) - 100, 2)
             AS edge_pct_chg,
       ROUND(core_pct, 1) AS core_pct,
       ROUND(pct_area_small_patches, 1) AS pct_area_small_patches
FROM fact_biome_year
WHERE tier = 'yearly'
ORDER BY biome, year;

-- >>> forest_loss_annual
-- Annual forest loss and its geometry: how much of each year's clearing lands
-- inside remaining core habitat, and how much touches the previous period's
-- clearing (i.e. extends an existing front rather than opening a new one).
SELECT biome, year,
       ROUND(loss_km2, 1)                            AS loss_km2,
       ROUND(loss_inside_core_pct, 1)                AS inside_core_pct,
       ROUND(loss_touching_previous_pct, 1)          AS touching_previous_pct,
       ROUND(loss_per_100km2, 3)                     AS loss_per_100km2,
       ROUND(forest_km2 / 10, 0)                     AS forest_kha,
       d_patches
FROM fact_loss_annual
ORDER BY biome, year;

-- >>> calibration_summary
-- The WMS rasterisation bias and the thinning that corrects it, every year.
SELECT biome, year,
       ROUND(raster_km2, 0)                                AS raster_km2,
       ROUND(official_km2, 0)                              AS official_km2,
       ROUND(raster_km2 / NULLIF(official_km2, 0), 2)      AS inflation_ratio,
       ROUND(used_km2, 0)                                  AS used_km2,
       ROUND(shortfall_km2, 1)                             AS shortfall_km2
FROM fact_calibration
ORDER BY biome, year;

-- >>> trend_summary
-- Mann-Kendall p and Theil-Sen slope for every annual series the pipeline has.
SELECT series, entity, unit, n_years, first_year, last_year,
       ROUND(first_value, 2) AS first_value,
       ROUND(last_value, 2)  AS last_value,
       ROUND(slope_per_year, 4) AS slope_per_year,
       ROUND(slope_ci_lo, 4)    AS slope_ci_lo,
       ROUND(slope_ci_hi, 4)    AS slope_ci_hi,
       ROUND(pct_per_year, 3)   AS pct_per_year,
       ROUND(p_value, 4)        AS p_value,
       CASE WHEN p_value < 0.05 THEN 'significant'
            WHEN p_value < 0.10 THEN 'marginal' ELSE 'not significant' END AS verdict
FROM fact_trend
ORDER BY series, entity;

-- >>> trend_significant
-- Which series actually trend.
SELECT series,
       COUNT(*)                                        AS n_series,
       SUM(CASE WHEN p_value < 0.05 THEN 1 ELSE 0 END) AS n_significant_5pct,
       SUM(CASE WHEN p_value < 0.05 AND slope_per_year < 0 THEN 1 ELSE 0 END) AS n_declining,
       SUM(CASE WHEN p_value < 0.05 AND slope_per_year > 0 THEN 1 ELSE 0 END) AS n_increasing
FROM fact_trend
GROUP BY series
ORDER BY n_series DESC;

-- >>> sensitivity_dispersal
-- ECA outside the dominant block at four dispersal distances.  If a corridor
-- conclusion only holds at d = 5 km, it is not a conclusion.
SELECT biome, year, tier,
       ROUND(eca_minor_2km / 1e6, 3)  AS d2_mha,
       ROUND(eca_minor_5km / 1e6, 3)  AS d5_mha,
       ROUND(eca_minor_10km / 1e6, 3) AS d10_mha,
       ROUND(eca_minor_20km / 1e6, 3) AS d20_mha,
       ROUND(eca_minor_20km - eca_minor_2km, 3) AS spread_mha,
       ROUND(core_r3, 1) AS core_pct_r3,
       ROUND(core_r5, 1) AS core_pct_r5,
       ROUND(core_r8, 1) AS core_pct_r8
FROM v_sensitivity_wide
ORDER BY biome, year;

-- >>> sensitivity_summary
-- Spread of each metric across variants, in the latest year per biome.
WITH last AS (
    SELECT biome, MAX(year) AS y FROM fact_sensitivity
    WHERE tier = 'yearly' GROUP BY biome
)
SELECT s.biome, s.year, s.metric,
       COUNT(DISTINCT s.variant)                      AS n_variants,
       ROUND(MIN(s.value), 1)                         AS min_value,
       ROUND(MAX(s.value), 1)                         AS max_value,
       ROUND(MAX(s.value) - MIN(s.value), 1)          AS spread,
       ROUND(100.0 * (MAX(s.value) - MIN(s.value))
             / NULLIF(MAX(s.value), 0), 2)            AS spread_pct_of_max
FROM fact_sensitivity s
JOIN last ON last.biome = s.biome AND last.y = s.year
GROUP BY s.biome, s.year, s.metric
ORDER BY s.biome, s.metric;

-- >>> fire_biome_year
-- Active-fire detections by biome and year, normalised by remaining forest.
SELECT biome_norm                    AS biome, year, n_foci,
       ROUND(dry_season_share, 3)    AS aug_oct_share,
       ROUND(forest_km2 / 10, 0)     AS forest_kha,
       ROUND(foci_per_100km2_forest, 3) AS foci_per_100km2_forest
FROM fact_fire_biome_year
ORDER BY biome, year;

-- >>> fire_seasonality
-- Monthly climatology of detections, 2003-2024.
SELECT biome_norm AS biome, month, n_foci_2003_2025 AS n_foci,
       ROUND(100.0 * n_foci_2003_2025
             / SUM(n_foci_2003_2025) OVER (PARTITION BY biome_norm), 1) AS share_pct
FROM fact_fire_climatology
ORDER BY biome, month;

-- >>> fire_per_forest
-- Fire density relative to the forest that is left, by biome and year.
SELECT l.biome, l.year,
       l.n_foci,
       ROUND(l.foci_per_100km2_forest, 3) AS foci_per_100km2_forest,
       ROUND(l.loss_km2, 1)               AS loss_km2,
       ROUND(l.loss_inside_core_pct, 1)   AS loss_inside_core_pct
FROM v_loss_vs_fire l
WHERE l.n_foci IS NOT NULL
ORDER BY l.biome, l.year;

-- >>> fire_vs_loss
-- Do fire detections and deforestation move together?  Same year, and with fire
-- leading loss by one year (clearing follows burning in the frontier).
SELECT biome, n_years,
       ROUND(spearman_same_year, 3)        AS r_same_year,
       ROUND(p_same_year, 4)               AS p_same_year,
       ROUND(spearman_fire_leads_loss, 3)  AS r_fire_leads_1y,
       ROUND(p_fire_leads_loss, 4)         AS p_fire_leads_1y
FROM fact_fire_corr
ORDER BY biome;

-- >>> fire_top_states
-- Where the fire actually is.
SELECT state,
       SUM(n_foci) AS n_foci,
       ROUND(100.0 * SUM(n_foci)
             / (SELECT SUM(n_foci) FROM fact_fire_biome_month), 1) AS share_pct,
       COUNT(DISTINCT year) AS n_years
FROM fact_fire_biome_month
GROUP BY state
ORDER BY n_foci DESC
LIMIT 12;

-- >>> deter_annual
-- DETER clearing alerts by biome and year (monthly source, annual view).
SELECT biome, year,
       SUM(n_alerts)                          AS n_alerts,
       ROUND(SUM(alert_area_km2), 0)          AS alert_area_km2
FROM fact_deter_month
GROUP BY biome, year
ORDER BY biome, year;

-- >>> deter_monthly
-- Monthly alert counts: the only monthly series in the pipeline.
SELECT biome, year, month, n_alerts,
       ROUND(alert_area_km2, 1) AS alert_area_km2
FROM fact_deter_month
ORDER BY biome, year, month;

-- >>> deter_class_mix
-- What kind of disturbance the alerts describe (clearing vs degradation).
SELECT biome, classname,
       COUNT(*)                                         AS n_alerts,
       ROUND(SUM(COALESCE(areamunkm, 0)), 0)            AS area_mun_km2,
       ROUND(AVG(COALESCE(areamunkm, 0)), 3)            AS mean_alert_km2
FROM fact_deter_alert
GROUP BY biome, classname
ORDER BY n_alerts DESC
LIMIT 20;

-- >>> species_retention_by_class
-- Persistence of adequately-sampled species, by forest-loss class.
SELECT grp, biome, forest_change_class,
       n_cells,
       ROUND(mean_retention, 3) AS mean_retention,
       ROUND(mean_species, 1)   AS mean_species_tested,
       ROUND(mean_effort, 1)    AS mean_effort
FROM fact_species_retention
ORDER BY grp, biome,
         CASE forest_change_class
              WHEN '1_lost>5km2' THEN 1 WHEN '2_lost1-5km2' THEN 2
              WHEN '3_stable' THEN 3 ELSE 4 END;

-- >>> species_retention_pooled
-- Pooled retention by loss class and group, with the contrast vs stable cells.
WITH agg AS (
    SELECT grp, forest_change_class, cells, retention FROM v_species_group
)
SELECT grp, forest_change_class, cells, ROUND(retention, 3) AS retention,
       ROUND(100.0 * (retention - (SELECT retention FROM agg a2
                                   WHERE a2.grp = agg.grp
                                     AND a2.forest_change_class = '3_stable')), 1)
              AS pp_vs_stable
FROM agg
ORDER BY grp, forest_change_class;

-- >>> species_logit
-- Persistence ~ forest loss + sampling effort (+ remaining forest share),
-- with bootstrap confidence intervals; one model per group and one pooled.
SELECT model, term, n_cells,
       ROUND(coef, 4)  AS coef,
       ROUND(ci_lo, 4) AS ci_lo,
       ROUND(ci_hi, 4) AS ci_hi,
       CASE WHEN ci_lo > 0 OR ci_hi < 0 THEN 'excludes zero' ELSE 'includes zero' END
              AS verdict
FROM fact_species_logit
ORDER BY CASE WHEN model = 'pooled' THEN 0 ELSE 1 END, model, term;

-- >>> species_cross_taxon
-- Do independent taxa agree about which cells lost species?
SELECT grp_a, grp_b, n_cells, ROUND(spearman_r, 3) AS spearman_r
FROM fact_species_cross_taxon
ORDER BY spearman_r;

-- >>> species_iucn
-- Persistence and range change by IUCN category, so a signal concentrated in
-- threatened species can be distinguished from a sampling artefact.
SELECT grp, iucn, n_species,
       ROUND(mean_cell_change_pct, 2)   AS mean_cell_change_pct,
       ROUND(mean_centroid_shift_km, 1) AS mean_centroid_shift_km,
       ROUND(mean_cell_retention, 3)    AS mean_cell_retention
FROM fact_species_iucn
ORDER BY grp, CASE iucn WHEN 'CR' THEN 1 WHEN 'EN' THEN 2 WHEN 'VU' THEN 3
                        WHEN 'NT' THEN 4 WHEN 'LC' THEN 5 WHEN 'DD' THEN 6
                        ELSE 7 END;

-- >>> species_range_summary
-- Cell-level turnover summary per group and biome, including how far the cells
-- are from the nearest forest edge (a proxy for interior-habitat dependence).
SELECT grp, biome,
       COUNT(*)                             AS n_cells,
       ROUND(AVG(n_species_tested), 1)      AS mean_species_tested,
       ROUND(AVG(retention), 3)             AS mean_retention,
       ROUND(AVG(dist_to_forest_km), 1)     AS mean_dist_to_forest_km,
       ROUND(AVG(effort_p2), 1)             AS mean_effort_p2,
       ROUND(AVG(coverage_p2), 3)           AS mean_sample_coverage
FROM fact_species_turnover
GROUP BY grp, biome
ORDER BY grp, biome;

-- >>> national_co2_brazil
-- Brazil's national panel: fossil CO2 and land-use-change CO2, 1990-2025.
SELECT year,
       ROUND(co2, 1)                         AS fossil_co2_mt,
       ROUND(land_use_change_co2, 1)         AS land_use_co2_mt,
       ROUND(total_ghg, 1)                   AS total_ghg_mt,
       ROUND(co2_per_capita, 2)              AS fossil_t_per_capita,
       ROUND(cumulative_co2, 0)              AS cumulative_co2_mt
FROM fact_national_co2
WHERE country = 'Brazil' AND year BETWEEN 1990 AND 2025
ORDER BY year;

-- >>> national_co2_compare
-- The national frame the facility data sits in.
SELECT country, year,
       ROUND(co2, 0)            AS fossil_co2_mt,
       ROUND(co2_per_capita, 2) AS t_per_capita,
       ROUND(co2_per_gdp, 3)    AS kg_per_dollar
FROM fact_national_co2
WHERE country IN ('Brazil', 'United States', 'World')
  AND year IN (2000, 2010, 2019, 2023, 2024)
ORDER BY country, year;

-- >>> wb_indicators
-- Official World Bank series used to triangulate the PRODES raster.
SELECT indicator, country, iso3, year, ROUND(value, 3) AS value
FROM fact_wb_indicator
ORDER BY indicator, country, year;

-- >>> data_coverage
-- What the data actually covers, per source and year.
SELECT 'ghgrp_reporters' AS source, CAST(year AS TEXT) AS period, COUNT(*) AS n
FROM fact_carbon_year GROUP BY year
UNION ALL
SELECT 'ghgrp_unit_years', CAST(year AS TEXT), COUNT(*) FROM fact_unit_year GROUP BY year
UNION ALL
SELECT 'biome_year_rows', CAST(year AS TEXT), COUNT(*) FROM fact_biome_year GROUP BY year
UNION ALL
SELECT 'fire_foci', CAST(year AS TEXT), SUM(n_foci) FROM fact_fire_biome_month GROUP BY year
UNION ALL
SELECT 'deter_alerts', CAST(year AS TEXT), SUM(n_alerts) FROM fact_deter_month GROUP BY year
UNION ALL
SELECT 'national_co2_rows', CAST(year AS TEXT), COUNT(*) FROM fact_national_co2 GROUP BY year
ORDER BY source, period;

-- >>> source_catalogue
-- Every dataset in the pipeline with its grain, licence and retrieval date.
SELECT s.source, s.grain, s.licence, s.url, s.note, s.retrieved
FROM dim_source s
ORDER BY s.source;

-- >>> table_sizes
-- Row counts of every loaded table (also stored in dim_table_size).
SELECT table_name, rows
FROM dim_table_size
ORDER BY rows DESC;

-- >>> scope_boundary
-- Which scopes the target register actually constrains.  This is the
-- honest counterweight to the missing scope-2 / scope-3 tonnages.
SELECT scope,
       COUNT(*)                              AS n_targets,
       COUNT(DISTINCT company_name)          AS n_companies,
       ROUND(AVG(target_value), 1)           AS mean_reduction_pct,
       ROUND(AVG(target_year), 0)            AS mean_target_year
FROM dim_target
WHERE scope IS NOT NULL AND scope <> ''
GROUP BY scope
ORDER BY n_targets DESC;

-- >>> target_ambition_by_sector
-- Sector-level ambition, for the top sectors by target count.
SELECT sector,
       COUNT(*)                     AS n_targets,
       ROUND(AVG(target_value), 1)  AS mean_reduction_pct,
       ROUND(AVG(target_year), 0)   AS mean_target_year,
       SUM(CASE WHEN scope LIKE '%3%' THEN 1 ELSE 0 END) AS n_scope3_targets
FROM dim_target
WHERE sector IS NOT NULL AND sector <> ''
GROUP BY sector
ORDER BY n_targets DESC
LIMIT 12;

-- >>> triangulation
-- Same quantity, independent sources.  A source-specific artefact shows up as
-- a disagreement here.
SELECT pair, year,
       ROUND(value_a, 2) AS value_a,
       ROUND(value_b, 2) AS value_b,
       unit_a, unit_b
FROM fact_triangulation
ORDER BY pair, year;
