# The Carbon Ledger: Carbon, Forests, Fire, and Species

> Data sources: US EPA GHGRP (facility-level and unit-level), Brazil INPE (PRODES
> accumulated and annual increments, DETER alerts, BDQueimadas fire foci), GBIF
> occurrences, the SBTi target registry, OWID / Global Carbon Budget, World Bank.
> Every number in this report can be reproduced by re-running
> `python src/run_all.py`; the corresponding outputs are in `data/out/ind_*.csv`,
> `data/interim/*.csv`, and `db/environmental.db`. Self-checks are in
> `reports/validation.md`.

---

## 1. Main findings

1. **Emissions inside the US and the carbon it sells are diverging.** In 2023
   on-site direct emissions were 2 383 Mt CO₂e, 9.3% below 2019 and 25.5% below
   2010; over the same period the fossil-fuel supplier tier was 4 651 Mt, 5.4%
   above 2019. The two curves move in opposite directions.
2. **The decline comes from shutting units, not from cleaner units.** Coal-fired
   units fell 43% in eight years (1 118 → 641) and coal emissions fell 46%; gas
   emissions instead rose 18% (875 → 1 033 Mt). Emissions intensity per unit of
   capacity barely moved (coal 0.0208 → 0.0191 t CO₂e/mmBTU·h).
3. **The net-zero pathway is still short by 233 Mt** (2023); before 2018 actual
   emissions had stayed below the pathway.
4. **Forest area is falling in all three biomes**: Amazonia 2007–2025 cumulative
   −4.07% (−134 952 km²), Cerrado 2000–2025 −24.31% (−326 634 km²), Atlantic
   Forest −14.99% (−64 298 km²).
5. **Fragmentation advances in step with area loss.** Amazonia patch count
   +26.9%, edge density +25.8%, core-area share 55.3% → 48.3%; Cerrado core area
   37.8% → 6.0%; Atlantic Forest 14.7% → 0.76%.
6. **Connectivity metrics cannot be cited quantitatively.** For Amazonia, the
   connected area outside the main block is +221 kha at a dispersal distance of
   2 km and −28 kha at 5 km — the same biome, the same period, with the sign
   flipping under a different assumption.
7. **Fire and deforestation are out of sync at the annual scale.** Amazonia
   r = 0.32 (p = 0.22), Cerrado r = 0.19 (p = 0.46). The only significant result
   is the Atlantic Forest's "fire leads deforestation by one year" (r = 0.54,
   p = 0.037).
8. **The DETER monthly signal and the PRODES annual inventory diverge in 2024**:
   alerts doubled from 40 186 to 81 229 while annual loss fell from 7 809 to
   6 069 km².
9. **The relationship between species retention and forest loss does not hold
   at this data volume.** After controlling for sampling effort, the bootstrap
   intervals of the forest-change coefficient all cross zero; yet "which cells
   lose species" is consistent across the three taxa (Spearman 0.57–0.68).
10. **Brazil's national curve is driven by land**: in 2023 land-use CO₂ was
    1 467 Mt vs 486 Mt from fossil fuels; the US is the reverse (fossil 4 924 Mt).

## 2. Carbon: facility level and unit level

### 2.1 Three tiers, two opposing curves

| Tier | 2023 reported | Entities | vs 2019 |
|---|---|---|---|
| direct (on-site combustion and process) | 2 382.8 Mt CO₂e | 6 470 | −9.3% |
| supply (fossil-fuel suppliers) | 4 650.6 Mt CO₂e | 632 | **+5.4%** |
| fugitive (oil & gas, coal mines, landfills) | 91.4 Mt CO₂e | 445 | −27.9% |

The supplier tier records "the emissions that the fossil fuels these companies
sell will eventually produce", not what they burn themselves. It first exceeded
the 2019 baseline in 2022 and kept rising in 2023; over the same period direct
emissions fell from 2 628 to 2 383 Mt. **Reductions at regulated facilities have
not propagated upstream to the supply side** — the most direct observation of
Scope 3 accounting in US data.

The supplier tier jumps from 976 Mt to 3 465 Mt in 2011→2012 because of a
reporting-rule change (implementation of subparts LL/QQ); comparisons spanning
2012 are invalid and the series must be broken on the chart.

### 2.2 Pathway gap

A linear pathway with a 2019 baseline, halving by 2030, and net zero by 2050:

| Year | Actual Mt | Pathway Mt | Gap Mt |
|---|---|---|---|
| 2018 | 2 778.8 | 2 747.5 | +31.3 |
| 2019 | 2 628.0 | 2 628.0 | 0 |
| 2020 | 2 402.7 | 2 508.6 | −105.9 |
| 2021 | 2 523.5 | 2 389.1 | +134.4 |
| 2022 | 2 504.3 | 2 269.7 | +234.6 |
| 2023 | 2 382.8 | 2 150.2 | +232.6 |

From 2010 to 2017 actual emissions stayed below this pathway (507 Mt below in
2010), turned positive for the first time in 2018, dipped briefly in 2020 due to
the pandemic, then fell behind again. From 2022 to 2023 the gap narrowed by
2 Mt — essentially unchanged.

### 2.3 Who is cutting

**Sectors** (2019 vs 2023, using each sector's first industry label):

| Sector | 2019 Mt | 2023 Mt | Change |
|---|---|---|---|
| Power | 1 625.6 | 1 437.4 | **−188.2 (−11.6%)** |
| Chemicals | 295.7 | 277.9 | −17.8 |
| Metals | 95.5 | 84.1 | −11.3 |
| Oil & gas systems | 104.6 | 113.3 | **+8.8 (+8.4%)** |
| Waste | 99.7 | 92.2 | −7.5 |

Power contributed 77% of the net decline; oil & gas systems is the only sector
with a significant increase.

**States**: the largest absolute changes are IL (−24.7 Mt), IN (−20.6), KY
(−13.2), MO (−11.8), NC (−11.8), PA (−11.2), MI (−11.1), WV (−10.5) — states
with a high share of coal power. But the ranking is unstable: in 300 bootstrap
resamples IL stays first only 1.7% of the time. **"Which states are cutting" is
stable; "who cut the most" is not.**

**Facility trajectories** (5 410 facilities reporting in both 2015 and 2023):

| Trajectory | Facilities | 2015 Mt | 2023 Mt | Net change |
|---|---|---|---|---|
| Down ≥25% | 1 554 | 1 084.1 | 517.4 | −566.7 |
| Down 5–25% | 1 114 | 657.6 | 567.5 | −90.1 |
| Broadly flat | 713 | 322.1 | 322.3 | +0.1 |
| Up 5–25% | 870 | 355.0 | 400.6 | +45.5 |
| Up >25% | 1 159 | 223.4 | 402.5 | +179.1 |

Existing facilities (including 169 small facilities emitting <10 kt/yr) total
−424 Mt; the full scope (including entries and exits) is −556 Mt, and the
132 Mt difference is the contribution of reporting entities entering and leaving.
**A few facilities cutting heavily offset another batch rising heavily, which is
why the net effect looks mild** — the 1 554 facilities down ≥25% contribute
−567 Mt, while the 1 159 up >25% give back +179 Mt.

### 2.4 Mechanism: coal exits, gas fills the gap

| Fuel group | Units 2015→2023 | Emissions Mt 2015→2023 | Emissions per unit capacity (t CO₂e/mmBTU·h) |
|---|---|---|---|
| Coal | 1 118 → **641** (−43%) | 1 357.4 → **728.5** (−46%) | 0.0208 → 0.0191 |
| Natural gas | 14 348 → 12 993 (−9%) | 875.3 → **1 032.7** (+18%) | 0.0008 → 0.0009 |
| Petroleum | 4 084 → 3 645 | 87.8 → 54.6 | 0.0004 → 0.0003 |

Coal's emissions intensity is 26 times that of gas; over eight years this ratio
barely changed — what changed is the number of units. **Decarbonisation comes
from retirement, not efficiency.** Unit-level data is the only accounting level
that reveals this — facility-level aggregation blends "shutdown" and "load
reduction" into a single number.

The capacity field is only available from 2013 onward: in the source tables
114 unit-years in 2011–2012 exceed 100 000 mmBTU/hr (max 19.6e6, equivalent to
6 GW thermal for a single unit); these are truncated and counted during
aggregation.

### 2.5 Decomposition: intensity, structure, activity

LMDI (additive; the three terms sum exactly to the observed change):

| Term | Mt CO₂e | Share of change |
|---|---|---|
| Total change | −245.2 | 100% |
| Activity effect (number of reporting entities) | −42.2 | 17% |
| Structure effect (sector shares) | −17.5 | 7% |
| Intensity effect (emissions intensity per entity) | **−185.5** | **76%** |
| of which the power sector | −188.2 | activity −25.8, structure −29.7, intensity −132.7 |

**Three quarters of the decline comes from intensity.** The 2020 pandemic shock
shows up most clearly in the intensity effect but does not change its direction.

### 2.6 Concentration and the boundary of commitments

| Tier | Entities | Total Mt | Top 20 | Top 20 share | HHI |
|---|---|---|---|---|---|
| direct | 6 470 | 2 382.8 | 226.0 Mt | 9.5% | 14 |
| supply | 632 | 4 650.6 | 1 471.2 Mt | **31.6%** | 94 |
| fugitive | 445 | 91.4 | 25.7 Mt | 28.1% | 75 |

Supplier concentration is 6.7 times that of direct emissions (HHI 94 vs 14),
with the top 20 accounting for a third. **Scope 3 is more easily defined by a
small number of entities than Scope 1** — this is both why it can be regulated
and where its boundary is contested.

Distribution of commitments in the SBTi registry:

| Target coverage | Targets | Companies | Mean reduction | Mean target year |
|---|---|---|---|---|
| 1+2 | 13 229 | 11 522 | 49.3% | 2033 |
| Scope 3 | 9 315 | 5 299 | 52.7% | 2034 |
| 1+2+3 | 4 923 | 3 275 | 52.7% | **2043** |

Mean reductions are all around 50%, but the most comprehensive category finishes
on average 9–10 years later. **The more comprehensive the pledge, the later it
is delivered.**

### 2.7 Uncertainty

The balanced panel (6 056 entities reporting in both 2019 and 2023) changes by
**−220.3 Mt**, with a facility-level bootstrap 95% interval of
**[−278.0, −162.7]**; the full scope is −245.2 Mt. The HHI interval is
[12.5, 15.0] (direct emissions).

## 3. Forests: annual series

### 3.1 Trajectories of the three biomes

Grid 0.02° (~2.2 km); areas calibrated to exact official WFS areas (see 8.2).

| Biome | Base year | Base forest kha | 2025 kha | Change | Patch count | Mean patch ha | Edge density | Core-area share |
|---|---|---|---|---|---|---|---|---|
| Amazonia | 2007 | 332 093 | 318 593 | **−4.07%** | 3 708 → 4 704 | 89 561 → 67 728 | 0.179 → 0.226 | 55.3% → 48.3% |
| Cerrado | 2000 | 134 380 | 101 714 | **−24.31%** | 5 248 → 7 905 | 25 606 → 12 867 | 0.331 → 0.717 | 37.8% → 6.0% |
| Atlantic Forest | 2000 | 42 926 | 36 492 | **−14.99%** | 2 804 → 2 722 | 15 309 → 13 406 | 0.498 → 0.803 | 14.7% → 0.8% |

Three modes of degradation:

- **Amazonia**: area down 4.1%, patches +26.9%, edge density +25.8%, core area
  −7.0 pp. The area change looks mild only because the baseline is large
  (332 million hectares); the absolute loss is 13.49 million hectares, averaging
  7 497 km²/yr.
- **Cerrado**: area down 24.3%, core area from 37.8% to 6.0% (a relative −84%),
  edge density doubled. Agricultural expansion cuts continuous woodland into
  whole blocks and core habitat is close to disappearing.
- **Atlantic Forest**: area down 15.0%, yet patch count falls slightly
  (2 804 → 2 722), mean patch size shrinks, and core area falls from 14.7% to
  0.76%. **Small fragments disappear first, leaving larger but edge-dominated
  remnants** — loss "below the detection threshold" that a 2.2 km grid cannot see.

### 3.2 Trends

29 annual series, 19 significant at the 5% level (Mann-Kendall, with Theil-Sen
slope intervals):

| Series | Subject | Years | Slope %/yr | p |
|---|---|---|---|---|
| Forest area | Cerrado | 20 | **−0.86** | <0.001 |
| Forest area | Atlantic Forest | 18 | **−0.36** | <0.001 |
| Forest area | Amazonia | 19 | **−0.23** | <0.001 |
| Core-area share | Cerrado | 20 | **−4.31** | <0.001 |
| Core-area share | Atlantic Forest | 18 | −1.04 | <0.001 |
| Core-area share | Amazonia | 19 | −0.75 | <0.001 |
| Patch count | Amazonia | 19 | +1.25 | <0.001 |
| Edge density | Cerrado | 20 | +1.67 | <0.001 |
| Annual loss (from 2013) | Atlantic Forest | 12 | **−11.31** | <0.001 |
| Annual loss (from 2013) | Cerrado | 13 | −1.36 | 0.43 |
| Annual loss (from 2013) | Amazonia | 13 | +4.34 | 0.16 |

Before 2013, Cerrado and Atlantic Forest used biennial increments, so putting
them into an annual trend measures the release cadence. **Using report years only
(2013+): Atlantic Forest loss declines significantly (−11.3%/yr), Cerrado is not
significant (−1.4%, p = 0.43), and Amazonia has no trend (+4.3%, p = 0.16)**.
Amazonia's consecutive 2024–2025 decline is only two years long and is not
enough to constitute a trend.

### 3.3 Where loss occurs

| Biome | Share falling in core area | Share adjacent to the previous frontier | Annual loss per 100 km² remaining forest (median) |
|---|---|---|---|
| Amazonia | 3.8% | 15.2% | 0.13–0.39 (0.20) |
| Cerrado | 3.1% | 10.9% | 0.59–4.46 (1.00) |
| Atlantic Forest | 1.5% | 9.8% | 0.11–5.24 (0.36) |

Core areas are almost never cleared directly — **not because they are well
protected, but because they were already far from roads**. Frontier adjacency is
only 10–15%, which means most new deforestation is not immediately next to last
year's plots but opens up elsewhere along the road/plot network — the signature
of forest being "perforated" rather than "trimmed at the edges".

Cerrado's median loss rate per unit area is 5 times Amazonia's (1.00 vs 0.20),
peaking at 11 times — this explains why its core area falls fastest. The
Atlantic Forest's 5.24 occurs in 2004, which is the accumulated clearing of
2000–2004, not a single-year value.

### 3.4 Connectivity and targets

| Biome | Base connected area Mha | 2025 Mha | Net change kha | vs +500 kha target | Outside main block kha | Change kha |
|---|---|---|---|---|---|---|
| Amazonia | 332.1 | 318.6 | **−13 500** | −2 700% | 113 → 118 | +5 |
| Cerrado | 134.4 | 101.7 | **−32 666** | −6 533% | 0 → 0 | 0 |
| Atlantic Forest | 42.9 | 36.5 | **−6 435** | −1 287% | 359 → 371 | +12 |

By total connected area (distance threshold d = 10 km), all three biomes are
regressing; looking only at "connected area outside the main block" (closer to
the corridor concept), there are small increases of 1.1%, 0%, and 2.4%.
**Neither reading supports "the target is advancing"**: one is −2 700%, the
other +1.1%, both far from the 500 kha target.

### 3.5 Scale sensitivity: conclusions are stable only in direction

| Dispersal distance | Amazonia | Cerrado | Atlantic Forest |
|---|---|---|---|
| d = 2 km | **+221 kha** | +51 kha | **−122 kha** |
| d = 5 km | −28 kha | 0 | +15 kha |
| d = 10 km (main scope) | +5 kha | 0 | +12 kha |
| d = 20 km | 0 | 0 | −1 kha |

Amazonia is +221 kha at 2 km and −28 kha at 5 km. **This is not a data problem
but a property of the ECA metric**: moderate fragmentation *increases* the area
in which "at least two patches fall in the same connected cluster", so ECA-minor
rises at short distances; once distance is scaled up, the main block absorbs
everything and the metric tends to zero.

Core-area share is equally sensitive to the erosion radius (Amazonia: radius
3 cells 62.8%, 5 cells 48.3%, 8 cells 34.8%). These quantities are therefore
given only in tables; the main text uses direction and relative magnitude only.

## 4. Fire: 5.1 million foci (2003–2024)

### 4.1 Totals and distribution

| Biome | Fire foci | Share of national | 2024 | 2024 per 100 km² forest |
|---|---|---|---|---|
| Amazonia | 2 534 982 | 49.6% | 140 346 | 4.40 |
| Cerrado | 1 568 242 | 30.7% | 81 468 | **7.95** |
| Atlantic Forest | 421 572 | 8.3% | 21 328 | 5.84 |
| Caatinga | 407 189 | 8.0% | 20 235 | — |
| Pantanal | 152 452 | 3.0% | 14 498 | — |
| Pampa | 23 534 | 0.5% | 424 | — |

State distribution is highly concentrated: Pará 19.0%, Mato Grosso 16.9%,
Maranhão 10.7%, Rondônia 6.6%, Tocantins 6.0% — the top five states account
for 59%.

Normalised by remaining forest, **Cerrado has the highest fire density of the
three biomes** (7.95 per 100 km² forest), not Amazonia (4.40). Amazonia's large
absolute count simply reflects its larger forest area.

### 4.2 Seasonality

September accounts for 30.4% of Amazonia's annual total, with August–October
together at 69%; Cerrado and Pantanal also peak in September (August–October
70% and 71%), the Atlantic Forest 62%, while Caatinga and Pampa are lower
(52%, 43%). **The fire season is August–October, at the end of the Southern
Hemisphere dry season.**

### 4.3 Relationship to deforestation

| Biome | Same-year Spearman r | p | Fire leads by one year r | p |
|---|---|---|---|---|
| Amazonia | 0.32 | 0.22 | 0.22 | 0.41 |
| Cerrado | 0.19 | 0.46 | 0.30 | 0.25 |
| Atlantic Forest | 0.37 | 0.16 | **0.54** | **0.037** |

At the annual scale, fire and deforestation are largely out of sync.
**Fire is driven by drought and pasture management, deforestation by markets and
policy**; only the Atlantic Forest shows a lagged "burn first, clear later"
correlation, and since only one of three biomes is significant, under multiple
comparisons it should be treated as a hint rather than a conclusion.

**Scope limitation**: these 22 annual files are "one reference satellite per
year" (`Brasil_sat_ref`); the satellite changes year by year, so cross-year
horizontal shifts may be instrument artefacts rather than changes in fire
activity. Year-on-year comparisons look at direction only.

## 5. DETER: the only monthly signal (559 423 alerts)

### 5.1 Volume and composition

| Biome | Coverage start | 2023 | 2024 | 2025 |
|---|---|---|---|---|
| Amazonia | 2016-08 | 40 186 | **81 229** | 23 209 |
| Cerrado | 2018-05 | 16 773 | 17 158 | 18 537 |

Amazonia's alert peak is in September (monthly mean 9 000), while Cerrado's is
brought forward to May (2 161/month) — **clearing is scheduled a quarter earlier
than fire**, something only a monthly signal can show.

Class composition (Amazonia, 2016–2025):

| Class | Alerts | Area km² | Mean km² per alert |
|---|---|---|---|
| DESMATAMENTO_CR (clear-cut) | 260 707 | 56 955 | 0.22 |
| CICATRIZ_DE_QUEIMADA (burn scar) | 116 231 | **142 138** | 1.22 |
| DEGRADACAO (degradation) | 38 364 | 23 762 | 0.62 |
| MINERACAO (mining) | 8 073 | 768 | 0.10 |
| CS_DESORDENADO / CS_GEOMETRICO | 10 753 | 28 725 | 2.7 |

**By area, burn scars (142 thousand km²) are larger than clear-cuts
(57 thousand km²), yet account for only 44% of alerts.** "Alert count" and
"loss area" cannot be converted directly — the most common misuse of these two
series.

### 5.2 Divergence from the annual inventory

In 2024 Amazonia alerts doubled (40 186 → 81 229) while PRODES annual loss fell
(7 809 → 6 069 km²). **The two numbers are not contradictory**: DETER is
near-real-time detection, affected by cloud cover, resolution, and reporting
rules; PRODES is a reviewed annual inventory. DETER can be used as a monthly
signal, but not as annual loss.

## 6. Species: 3 taxa, 625 species

### 6.1 Coverage and sampling

| Taxon | Records (with scientific name) | Species | Testable species (≥8 records in each period) | Scoring units |
|---|---|---|---|---|
| Bats (Chiroptera) | 22 542 | 190 | 82 | 555 |
| Palms (Arecaceae) | 22 218 | 366 | 90 | 188 |
| Primates (Primates) | 24 295 | 140 | 59 | 121 |

After cleaning, 73 622 records, of which 4 567 lack a scientific name. Cleaning
rules: within Brazil, with coordinates, 1970–2026, remove (0,0), deduplicate by
"species × latitude/longitude × year" (about 47% of the raw records remain after
deduplication).

Sampling intensity varies enormously: primate cells in the Atlantic Forest
average 86.5 records with sample coverage 0.58; bat cells in Amazonia average
only 8.6 records with coverage 0.27. **Any comparison across biomes must first
address this difference.**

### 6.2 Retention rate

Retention rate = whether a testable species present in a given 0.1° cell in the
earlier period is still present in the same cell in the later period.

| Taxon | Lost >5 km² | Lost 1–5 km² | Stable | Gained | vs stable group |
|---|---|---|---|---|---|
| Bats | 0.126 | 0.137 | 0.093 | 0.111 | **+3.3 / +4.4 pp** |
| Palms | 0.137 | 0.105 | 0.171 | 0.169 | −3.4 / −6.6 pp |
| Primates | 0.400 | 0.357 | 0.429 | 0.470 | −2.9 / −7.2 pp |

**The sign is inconsistent across taxa**: negative for palms and primates (cells
that lost forest have lower retention), positive for bats. All bootstrap
intervals cross zero (bats +2.8 [−3.7, +10.1], palms −2.5 [−12.9, +8.0],
primates −2.3 [−29.6, +23.1]).

By biome, retention is mainly a function of sampling intensity, not of forest:
primates 0.675 in the Atlantic Forest, 0.583 in Cerrado, 0.250 in Amazonia, with
corresponding mean record counts of 86.5 / 37.1 / 17.1. **More records, higher
retention** — precisely why effort must be controlled for in the model.

### 6.3 After controlling for sampling effort

Weighted logistic regression of retention on forest change, two sampling efforts,
and remaining-forest share (weight = number of testable species in the cell),
250 bootstrap replicates:

| Model | Forest-change coefficient (per 10 km²) | 95% interval | Units |
|---|---|---|---|
| Pooled | +0.038 | [−0.112, +0.257] | 864 |
| Bats | −0.038 | [−0.226, +0.180] | 555 |
| Palms | +0.191 | [−0.131, +0.603] | 188 |
| Primates | +0.031 | [−0.250, +0.407] | 121 |

A positive coefficient means "more forest → higher retention", which matches
intuition, **but all intervals cross zero and the bat coefficient has the
opposite sign**. The 3–7 percentage-point gaps in the previous section no longer
hold once effort is controlled for.

### 6.4 Cross-taxon: the cell-level signal is consistent

| Taxon pair | Spearman r | Cells | p |
|---|---|---|---|
| Bats × palms | **0.571** | 26 | 0.002 |
| Bats × primates | **0.592** | 33 | <0.001 |
| Palms × primates | **0.683** | 11 | 0.020 |

**"Which cells lose species" agrees across the three taxa**, and this conclusion
does not depend on the forest mask. The cost is a small intersection (11–33
cells), so it serves only as evidence of consistency, not of magnitude.

### 6.5 Threat categories and the boundary of the conclusion

Among testable species: Critically Endangered 6, Endangered 11, Vulnerable 14,
Near Threatened 12, Least Concern 143, Data Deficient 5, Not Evaluated 40.
**Only 31 threatened species**, not enough to support a claim that "the signal
is concentrated in threatened taxa".

**The conclusion of this section is weak**: a causal link between species
turnover and forest change does not hold at this data volume; the only robust
findings are that "the cell-level species-loss signal is consistent across taxa"
and the methodological point that "sampling effort must enter the model".

## 7. National context and cross-validation

### 7.1 Brazil's two ledgers

| Year | Fossil CO₂ Mt | Land-use CO₂ Mt | Total Mt | Per capita t |
|---|---|---|---|---|
| 2000 | 340.2 | 1 899.3 | 2 823.8 | 1.96 |
| 2010 | 440.3 | 1 251.2 | 2 422.2 | 2.27 |
| 2019 | 476.7 | 1 308.5 | 2 468.7 | 2.30 |
| 2023 | 485.7 | **1 467.4** | 2 817.8 | 2.30 |
| 2024 | 490.7 | 1 334.0 | 2 804.4 | 2.32 |

Fossil emissions are rising (1990→2024 +124%); land-use emissions fell from
2 248 Mt in 2004 to 1 251 Mt in 2010, then recovered to 1 467 Mt in 2023.
**Brazil's curve is driven by land**: in 2023 the land-use term was 3.0 times
the fossil term, and 5.6 times in 2000; the land term's interannual swing can
reach 1 200 Mt (2003→2008), while the fossil term's swing is under 70 Mt.

### 7.2 Comparison with the US

| Indicator (2024) | Brazil | US | World |
|---|---|---|---|
| Fossil CO₂ Mt | 491 | 4 912 | 38 647 |
| Per capita t | 2.32 | 14.22 | 4.74 |
| kg per USD (2019) | 0.156 | 0.283 | 0.303 |

The US lever is in power plants (Section 2); Brazil's lever is in forests
(Section 3). Under the same CO₂ accounting, the two countries have completely
different marginal abatement cost structures.

### 7.3 The same thing, different sources

| Comparison | Result |
|---|---|
| GHGRP direct emissions / OWID US fossil CO₂ | 56% in 2010, **48% in 2023**: regulated large sources are falling faster than the national total |
| PRODES annual loss / OWID Brazil land-use CO₂ | Spearman **0.45 (p = 0.11)** across the 14 years with all three biomes; same direction but not significant |
| DETER alerts / PRODES annual loss | 2024 divergence (see 5.2) |
| PRODES per-year hits / whole-layer hits | 802 312 = 802 312, 2 388 715 = 2 388 715, 1 113 709 = 1 113 709, **difference 0** |

The last row is the hardest evidence: the sum of per-year CQL partitions equals
the whole-layer feature count exactly, showing the annual inventory has neither
omissions nor duplicates.

## 8. Uncertainty and scope

### 8.1 What is stable, what is not

| Conclusion | Robustness |
|---|---|
| Forest area falling in all three biomes | **Stable**: official-area calibration, trend p < 0.001 |
| Fragmentation worsening (patches, edges, core area) | **Stable**: three independent metrics agree in direction and are only magnitude-sensitive to erosion radius |
| US direct emissions falling, supplier tier rising | **Stable**: the two tiers are reported independently; supply has been above the 2019 baseline continuously since 2022 |
| Net-zero pathway gap ≈ 233 Mt | **Stable** (the pathway is an assumption; the gap is arithmetic) |
| "How much" connected area fell | **Unstable**: sign flips with dispersal distance |
| State rankings | **Unstable**: top-1 is stable in only 1.7% of bootstrap resamples |
| Species retention response to forest loss | **Does not hold**: intervals cross zero and the sign flips across taxa after controlling for effort |
| Fire–deforestation coupling | **Does not hold** (annual scale); only the Atlantic Forest lag term is a hint |

### 8.2 Area calibration

The median PRODES polygon is 0.08 km² and is inevitably rendered as a full pixel
on a 2.2 km grid; WMS-rendered area is inflated by 1.18 (Amazonia), 1.25
(Cerrado), and 1.31 (Atlantic Forest). The fix: stream WFS attributes to sum
exact official areas (without downloading geometry), then thin the raster to the
target area according to local deforestation density. After thinning, each
biome-year differs from the official figure by **< 2 km²**.

Annual increments use **nested calibration**: the current year's additions are
thinned to that year's official area and then added to the previous year's
accumulated mask. If the entire accumulated layer were re-thinned every year,
interannual differences would be drowned out by point-selection noise —
re-selecting points on a baseline layer of 1 million km² makes a single-year
increment of a few thousand km² impossible to read.

### 8.3 Data and scope

| Data | Grain | Coverage | Rows |
|---|---|---|---|
| EPA GHGRP annual summary | facility × year × tier | 2010–2023 | 122 260 |
| EPA GHGRP unit-level C/D/AA | facility × unit × year | 35 010 units | 268 778 |
| EPA GHGRP unit fuel | facility × unit × fuel × year | 7 categories / 62 fuels | 323 496 |
| INPE PRODES official areas | biome × year | 56 biome-years | 84 |
| INPE DETER alerts | alert | 2016-08–2025-12 | 559 423 |
| INPE BDQueimadas fire foci | fire focus | 2003–2024 | 5 107 973 |
| GBIF occurrences | record | Brazil, 3 taxa | 73 622 |
| OWID / Global Carbon Budget | country × year | 1850–2025 | 45 301 |
| World Bank | country × year | Brazil + world | 225 |
| SBTi target registry | target | — | 41 593 |

**Explicitly missing** (no estimated substitutes): facility-level Scope 2
(GHGRP does not publish purchased electricity), Scope 3 tonnages, the DETER
layer for the Atlantic Forest (not available on TerraBrasilis), and 30 m
resolution (a 15 GB data stack on a 0.1–2.3 MB/s connection is not feasible).

**Unified scope**: 0.02° grid, areas corrected by the cosine of latitude,
annual series truncated at 2025 (2026 is a partial year, explicitly excluded),
trends via Mann-Kendall + Theil-Sen, fixed bootstrap seed
(`BOOTSTRAP_SEED = 20260101`).

## 9. Outputs and reproduction

```
python src/run_all.py          # full run, ~2–3 h (network-bound)
python src/run_all.py --list   # stage names
```

| Output | Location | Size |
|---|---|---|
| Database | `db/environmental.db` | 35 tables, 8 views, 2 101 128 fact rows |
| Indicators | `data/out/ind_*.csv` | 46 |
| Figures | `reports/figures/fig01–24.png` | 24 |
| Intermediates | `data/interim/*.csv` | 689 MB |
| Self-checks | `reports/validation.md` | 32 checks, 0 failures |

Figure map: carbon fig01–08 (tiers, pathway, trajectories, states, map, sectors,
LMDI, unit mechanism); forests fig09–14 (biome maps, annual series,
connectivity, sensitivity, loss geometry, calibration); fire and DETER fig15–18;
species fig19–21; national ledgers and trend summary fig22–24.
