# Measurement Science Analysis Planner — Solution Manual Index

> **For**: Claude Agent (Analysis Planner)
> **Purpose**: Given a question from a seller or partner, route to the right stored procedure, understand what data/visuals it produces, and anticipate the business outcome.
> **Last Updated**: 2026-04-08
> **Source Material**: 178 case studies (Excel), 213 slides across 3 Google Slides decks, 267 individual case study presentations (strategically sampled ~60)

---

## How to Use This Index

When a question arrives, follow this reasoning chain:

```
1. QUESTION → Which procedure(s) answer this? (see Routing Table + Keyword Router below)
2. PROCEDURE → Read the procedure README. Does the question fit a sub-variant?
   (Sub-variants have modified parameters or output — check the "Sub-Variants" section in the README)
3. DATA/ANALYSIS → What data does it need? Are there prerequisites? (see Data Requirements + Prerequisites)
4. VISUAL → What chart/table does it produce? (see Visual Type column or README)
5. INSIGHT → What does this enlighten for the stakeholder? (see Insight column)
6. ACTION → What should the advertiser/seller do? (see Recommended Action column)
```

---

## Routing Table: Question → Procedure

| # | Procedure ID | Procedure Name | Question Themes | Data/Analysis Type | Visual Type | Insight for Stakeholder | Recommended Action | Procedure Path |
|---|---|---|---|---|---|---|---|---|
| 1 | `EARLY_BIRD_SP_INVESTMENT` | Early Bird SP Investment Comparison | "Should I keep SP live during Jan?", "What happens if I pause at New Year?", "Show me that early investment pays off" | Pre/Post period comparison of SP spend, CPC, sales, share across advertisers in a category | Bar chart: pre vs post metrics for increased-spend vs decreased-spend brands | Brands that invest early get cheaper CPCs AND higher sales/share; those that go dark lose ground | Maintain or increase SP investment through seasonal transitions — don't pause | `procedures/01_early_bird_sp_investment/` |
| 2 | `GOING_DARK_IMPACT` | Going Dark Impact Analysis | "My partner paused SP — what happened?", "Cost of going dark?", "How long to recover share?" | Time-series of share/sales/lapsed rates across live → dark → recovery periods | Line chart: share trajectory overlaid with SP spend; bar chart: lapsed user rate comparison | Pausing SP causes immediate share bleed and user attrition; recovery is slow and expensive | Stay always-on with SP; the cost of re-entry exceeds savings from pausing | `procedures/02_going_dark_impact/` |
| 3 | `MULTI_TACTIC_SYNERGY` | Multi-Tactic Reach & Synergy | "How many more customers with Display?", "Does layering tactics increase baskets?", "Show synergy between SP + Display" | User-level reach overlap analysis, conversion rate indexing by tactic combination, basket size by exposure count | Stacked bar (spend by tactic) + line (% buyers reached); indexed conversion bar chart; basket size comparison | Adding upper-funnel tactics to SP expands reach 50-120%+, improves conversion, and grows baskets | Adopt full-funnel approach; layer Display/Shoppable/Video with SP | `procedures/03_multi_tactic_synergy/` |
| 4 | `NTB_COHORT_LTV` | NTB Cohort Repeat & LTV | "Do ad-acquired NTB customers come back?", "What's the LTV?", "How does ROAS improve over time?" | Cohort analysis: track NTB users acquired via ads over 6-12 months, cumulative sales and evolving ROAS | Waterfall/cumulative line: cohort sales over time; ratio chart: sales/spend improving over quarters | Initial ROAS understates true value — repeat purchases can 2-3x the return over 6-12 months | View SP as customer acquisition investment; the LTV justifies initial ROAS | `procedures/04_ntb_cohort_ltv/` |
| 5 | `CO_BRANDED_CROSS_SHOP` | Co-Branded Cross-Shop Analysis | "Did cross-shop increase during campaign?", "How many new customers from partnership?", "New aisle visibility?" | Basket co-occurrence analysis, trial rate comparison, aisle reach expansion | Pre/Post bar chart: cross-shop basket %; pie: NTB from partner brand's customers | Co-branded campaigns drive +25% cross-shop growth and mutual trial from each other's loyal base | Partner with complementary brands for co-branded campaigns | `procedures/05_co_branded_cross_shop/` |
| 6 | `PROMO_EFFECTIVENESS` | SUAS / Coupon / Free Gift Effectiveness | "Do SUAS redeemers buy more?", "Repeat rate after coupon?", "Basket size for promo vs non-promo?" | Redemption basket analysis, post-purchase repeat tracking, top product combos | Side-by-side bar: redemption vs non-redemption baskets; decay curve: repeat rate over time | Redeemers have 5x larger baskets, ~50% return within 90 days, high cross-brand shopping | Use SUAS/Coupons for trial, basket building, and creating repeat customers | `procedures/06_promo_effectiveness/` |
| 7 | `BASKET_AFFINITY` | Basket Affinity & Category Opportunity | "What else do our loyalists buy?", "How many category buyers haven't tried us?", "Where should we target Display?" | Affinity index: brand loyalists vs all category buyers; penetration gap analysis | Indexed horizontal bar: over-indexing categories; funnel: total category buyers → tried brand | Loyalists over-index in specific adjacent categories; large untapped pool of category buyers never tried the brand | Use Display/Shoppable to target in over-indexing categories; highlight acquisition opportunity | `procedures/07_basket_affinity/` |
| 8 | `SHOPPABLE_DISPLAY_REACH` | Shoppable Display Incremental Reach | "Sales from terms SP can't reach?", "% of users new to our ads?", "Which terms does Shoppable cover?" | Search-term level attribution comparison (SP vs Shoppable); user overlap/gap analysis | Table: top search terms with Shoppable sales not covered by SP; Venn diagram: user overlap | Shoppable Display fills coverage gaps on competitive/cross-category terms SP can't win | Layer Shoppable Display with SP to fill coverage gaps | `procedures/08_shoppable_display_reach/` |
| 9 | `LIFT_TEST_INCREMENTALITY` | Lift Test Incrementality | "Are my ads actually incremental?", "What's my iROAS?", "Is the lift significant?" | Test/Control comparison, incremental sales calculation, statistical significance testing | Bar chart: test vs control sales; summary card: lift %, iROAS, confidence interval | Ads drive X% incremental sales above organic; iROAS of Y proves positive return | Continue or increase investment — ads are proven incremental | `procedures/09_lift_test_incrementality/` |
| 10 | `TENTPOLE_STRATEGY` | Tentpole Investment Strategy | "How should I invest for the holiday?", "What happened to full-funnel brands during Thanksgiving?", "Does share gain persist?" | Time-series around tentpole: share, spend, PIS, new customers; multi-tactic vs single comparison | Area chart: share trajectory through tentpole with spend overlay; comparison table: multi vs single tactic | Multi-tactic activation during tentpoles amplifies share gains; gains can persist post-event | Plan ahead with layered tactics for key shopping moments | `procedures/10_tentpole_strategy/` |
| 13 | `IMPULSE_PLACEMENT` | Impulse Placement Analysis | "% NTB from impulse?", "Do impulse buyers repeat?", "Impulse vs SP for trial?" | Impulse exposure → purchase → repeat analysis; NTB/NTP/NTC classification | Pie: NTB/NTP/NTC breakout; bar: repeat rate at 7/14/30 days; comparison: impulse vs SP trial rates | Impulse drives high NTB/NTP rates and strong 30-day repeat behavior | Activate impulse placement for trial-oriented goals | `procedures/13_impulse_placement/` |

---

## Quick Keyword Router

Use this when the question doesn't clearly map to one procedure:

| Keyword(s) in Question | Primary Procedure | Secondary Procedure |
|---|---|---|
| "go dark", "pause", "stop", "turn off" | `02_going_dark_impact` | `01_early_bird_sp_investment` |
| "new year", "january", "early bird", "seasonal" | `01_early_bird_sp_investment` | `10_tentpole_strategy` |
| "reach", "awareness", "exposure", "funnel" | `03_multi_tactic_synergy` | `08_shoppable_display_reach` |
| "NTB", "new to brand", "acquisition", "LTV", "lifetime" | `04_ntb_cohort_ltv` | `13_impulse_placement` |
| "repeat", "retention", "come back", "cohort" | `04_ntb_cohort_ltv` | `06_promo_effectiveness` |
| "co-brand", "cross-shop", "partnership", "collaboration" | `05_co_branded_cross_shop` | — |
| "coupon", "SUAS", "stock up", "free gift", "promotion" | `06_promo_effectiveness` | — |
| "basket", "affinity", "what else", "adjacent", "cross-category" | `07_basket_affinity` | `05_co_branded_cross_shop` |
| "shoppable", "search terms", "coverage", "incremental reach" | `08_shoppable_display_reach` | `03_multi_tactic_synergy` |
| "lift test", "incrementality", "iROAS", "causal" | `09_lift_test_incrementality` | — |
| "tentpole", "holiday", "thanksgiving", "cinco de mayo", "easter" | `10_tentpole_strategy` | `01_early_bird_sp_investment` |
| "impulse", "checkout", "trade up" | `13_impulse_placement` | — |
| "trial", "drive trial", "trial generation" | `13_impulse_placement` | `06_promo_effectiveness` |
| "ROAS", "return on ad spend" | `09_lift_test_incrementality` | — |
| "sales", "grow sales", "total sales", "drive sales" | `01_early_bird_sp_investment` | `03_multi_tactic_synergy` |
| "conversion", "conversion rate", "convert" | `03_multi_tactic_synergy` | `08_shoppable_display_reach` |
| "Display", "video", "storefront", "SFB" | `03_multi_tactic_synergy` | `08_shoppable_display_reach` |
| "share", "category share", "SOV" | `01_early_bird_sp_investment` | `02_going_dark_impact` |
| "CPC", "efficiency", "bidding" | `01_early_bird_sp_investment` | — |
| "basket size", "units per order" | `06_promo_effectiveness` | `03_multi_tactic_synergy` |
| "recipe" | `03_multi_tactic_synergy` | — |
| "optimized bidding", "OB", "manual bid" | `01_early_bird_sp_investment` | — |
| "broad match", "exact match", "match type" | `01_early_bird_sp_investment` | `08_shoppable_display_reach` |
| "recovery", "re-entry", "aggressive bid" | `02_going_dark_impact` | `01_early_bird_sp_investment` |
| "storefront banner", "brand page" | `03_multi_tactic_synergy` | `10_tentpole_strategy` |
| "flyout", "brand page traffic" | `03_multi_tactic_synergy` | — |
| "retarget", "win back", "recapture" | `03_multi_tactic_synergy` | `02_going_dark_impact` |
| "lapsed user rate", "user attrition" | `02_going_dark_impact` | `04_ntb_cohort_ltv` |
| "order sources", "non-search", "buy it again" | `03_multi_tactic_synergy` | — |
| "innovation", "new product", "launch" | `03_multi_tactic_synergy` | `13_impulse_placement` |
| "better together", "SUAS + coupon", "stacking promos" | `06_promo_effectiveness` | — |
| "GYS", "grow your share", "spend increase test" | `09_lift_test_incrementality` | `01_early_bird_sp_investment` |
| "pacing", "heavy-up", "ramp", "gradual" | `10_tentpole_strategy` | — |
| "Canada", "international" | `03_multi_tactic_synergy` | — |

---

## Disambiguation Rules

When a question matches multiple procedures, use these rules to pick the primary:

1. **Time-horizon test**: Is the question about a *specific event/period* (→ tentpole, lift test) or an *ongoing pattern* (→ early bird, going dark)?
2. **Cause vs effect**: Is the question about *why something happened* (→ going dark, early bird) or *what to do next* (→ multi-tactic, tentpole)?
3. **Tactic specificity**: If the question names a specific tactic (Display, Impulse, Coupon), route to the tactic-specific procedure first, then consider the broader pattern procedure as secondary.
4. **"Paused during holiday"**: Going dark (02) is primary if the focus is on the *impact of pausing*. Tentpole (10) is primary if the focus is on *how to invest during the event*.
5. **"Lapsed users"**: Going dark (02) if asking about lapsed rates as a *consequence of reduced spend*. Multi-tactic (03, sub-variant 3B) if asking about *retargeting lapsed users with Display*. NTB Cohort (04) if asking about *retention curves over time*.
6. **When in doubt**: Run both procedures and let the results speak — present the more compelling story.

---

## Procedure README Files

Each procedure folder contains:
- `README.md` — Full description: question themes, methodology, data requirements, parameters, expected output, visual types, slide references, sub-variants (where applicable)
- `procedure.sql` — Snowflake stored procedure (written using Daniel's context engineering framework: write-sql/review-sql Ralph Loop, data dictionary, Snowflake conventions)
- `sample_output.csv` — (Optional) Example of what the procedure generates
- `slide_references/` — Links or descriptions of the original slides this procedure was reverse-engineered from

**Note on parameter types**: README parameters use pseudo-types (`LIST`, `BOOLEAN`) for clarity. The actual SQL procedures adapt these for Snowflake — e.g., `LIST` becomes comma-separated `VARCHAR`, `BOOLEAN` becomes `VARCHAR` with `'Y'`/`'N'` or is handled via conditional logic.

---

## Procedure Status

| # | Procedure | SQL Status | Notes |
|---|---|---|---|
| 01 | Early Bird SP Investment | Written, self-reviewed | Ready for Snowflake execution test |
| 02 | Going Dark Impact | Written, self-reviewed | Ready for Snowflake execution test |
| 03 | Multi-Tactic Synergy | Written, self-reviewed | Ready for Snowflake execution test |
| 04 | NTB Cohort LTV | Written, self-reviewed | Ready for Snowflake execution test |
| 05 | Co-Branded Cross-Shop | Written, self-reviewed | Ready for Snowflake execution test |
| 06 | Promo Effectiveness | Written, self-reviewed | Ready for Snowflake execution test |
| 07 | Basket Affinity | Written, self-reviewed | Ready for Snowflake execution test |
| 08 | Shoppable Display Reach | Written, self-reviewed | Ready for Snowflake execution test |
| 09 | Lift Test Incrementality | Written, self-reviewed | Ready for Snowflake execution test |
| 10 | Tentpole Strategy | Written, self-reviewed | Ready for Snowflake execution test |
| 13 | Impulse Placement | Written, self-reviewed | Ready for Snowflake execution test |

**All procedures were written against the Instacart Snowflake data dictionary** (`context_engineering/docs/data_dict/`) using the write-sql/review-sql Ralph Loop adapted for Cowork mode. SQL has NOT been execution-tested in Snowflake — run via Snow CLI or Snowsight before production use.
