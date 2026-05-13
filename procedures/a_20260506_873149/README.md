# Ad-Driven Cohort Brand Repeat by Segment (Weekly or Monthly)

## Overview
For users whose **first ad-driven brand purchase** falls in a configurable
cohort window — where "ad-driven" is the union of (a) SP/Display click-priori-
tized attribution and (b) SUAS (Spend & Save) redemptions against caller-
supplied campaign UUIDs — measure cumulative brand repeat trajectory in the
forward observation window (input in days), bucketed at **WEEKLY** or
**MONTHLY** cadence (`v_bucket_unit`). Cohort users are split by their NTX
flags on the cohort-defining order's brand items into mutually exclusive
segments: **NTC** (new-to-brand AND new-to-category), **Prev. Competitor Only**
(new-to-brand AND NOT new-to-category), and optionally **Existing Users**
(neither — included by default, suppressible via flag for an "NTB-only" view).

For every (segment, bucket) the procedure emits cumulative repeat **rate** and
cumulative repeat **sales** through the end of that bucket. The bucket end is
reported as an integer count in the chosen unit (`bucket_end = 1, 2, …, N` of
weeks or months) so the chart x-axis stays clean — no fractional weeks like
"2.86". NTB combined (NTC + Prev. Competitor Only) is derived at viz time by
summation.

## When to use (chart-pattern reuse)

The chart contract is a **bar+line cumulative cohort retention curve
on twin axes** — bars = cumulative repeat rate, line = cumulative
repeat sales — with one series per segment and a configurable bucket
unit (week or month) over a configurable forward window. Reuse this
procedure for any post-acquisition cohort retention question whose
answer takes that visual form. Three swap points keep `chart.py` and
the output schema intact:

1. **Cohort-defining CTE** — the canonical version unions ad-driven
   first orders (SP/Display attribution + SUAS redemption) in a single
   cohort window. Swap to a different first-touch event (NTB-via-SP
   only, organic NTB, promo redeemer) by replacing the cohort universe
   CTE; the rest of the SQL doesn't change.
2. **Segment field** — the canonical version splits on NTX flags (NTC
   vs Prev. Competitor Only vs Existing Users). The same field can
   carry **acquisition-quarter** or **acquisition-month** instead, so a
   single chart compares Q1/Q2/Q3/Q4 cohorts on calendar time, or
   monthly cohorts on days-since-acquisition. The output schema
   (segment × bucket × {cum_repeat_rate, cum_repeat_sales}) is
   unchanged.
3. **Bucket unit + forward window** — `v_bucket_unit` is `'WEEK'`
   (7-day buckets) or `'MONTH'` (30-day buckets); `v_forward_window_days`
   sets the horizon and **must** be a positive multiple of the unit
   (otherwise the procedure raises `PARTIAL_BUCKET_WINDOW` rather than
   silently dropping a partial trailing bucket). Defaults: `'WEEK'`
   over 84 days (12 weekly buckets). Common combos: weekly over 28/56/84
   days; monthly over 60/90/120/180 days. The output table holds
   `forward_window_days / unit_size_days` rows per segment.

For multi-cohort comparisons (Q3 push vs surrounding quarters,
month-cohort × N-day-window grid), run the procedure once per cohort
slice and stitch the segment outputs into a single chart — the output
schema absorbs an arbitrary number of segment lines.

Concrete reuse targets:

- *Of the people we acquired through any ad product (SP, Display, or SUAS)
  during a high-volume push, how many of them came back to the brand within
  12 weeks?* — canonical, no swaps (`v_bucket_unit='WEEK'`,
  `v_forward_window_days=84`).
- *…within 4 months?* — same procedure with `v_bucket_unit='MONTH'`,
  `v_forward_window_days=120`.
- *Does NTC repeat behavior differ from "I used to buy your competitor"
  repeat behavior in the post-acquisition ramp?*
- *What share of post-conversion brand sales is attributable to NTB
  acquisitions vs. existing buyers reactivating during a campaign?*
- *Is there a payback shape — when does cumulative repeat sales surpass
  acquisition cost or break a target threshold?*
- *How does the trajectory change when we restrict the cohort source to
  SUAS-only or to SP+Display-only (compare-by-source)?*
- *How sensitive is the segmentation to NTX lookback (182-day vs 365-day)?*
- *Did NTB customers acquired during a Q3 push period continue to drive
  brand sales in the months after the push, compared to surrounding
  quarters?* — segment field swap (NTX → acquisition_quarter) +
  run-per-quarter stitching, typically `v_bucket_unit='MONTH'`.
- *Per-cohort NTB repeat-purchase rate by acquisition cohort month over a
  6-month forward window with right-censoring for incomplete cohorts* —
  `v_bucket_unit='MONTH'`, `v_forward_window_days=180` + segment swap
  (NTX → cohort_month) + null-out incomplete buckets.

## Methodology
1. Resolve `v_bucket_unit` → internal `bucket_size_days` (7 or 30) and
   validate that `v_forward_window_days` is a positive multiple of it
   (raise `PARTIAL_BUCKET_WINDOW` otherwise).
2. Resolve SUAS `discount_policy_id`s inline from `nexus_coupons` for the
   caller's CSV list of campaign UUIDs (skipped if SUAS leg disabled).
3. Build the candidate cohort universe as the **UNION** of:
   - SP/Display orders attributed via
     `multi_touch_click_prioritized_ads_attributions` joined to
     `agg_ma_order_item_daily_v2` on `(order_id, order_item_id)` and
     filtered to the target brand and country (gated by
     `v_include_sp_display_attribution`); and
   - SUAS-redemption orders from `fact_spend_promotion_redemption` with
     `overall_status = 'VALID'` and matching policy IDs (gated by
     `v_include_suas_redemption`).
4. Pick each user's **first** candidate order in the cohort window
   (`QUALIFY ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY
   delivered_date_pt, order_id) = 1`) — this is the cohort-defining order.
5. Look up NTX flags from `unified_order_item_ntx`, restricted to brand
   items via `agg_ma_order_item_daily_v2.delivered_entity_brand_id` and
   country via `dim_warehouse.country_id`. The NTX lookback (182 vs 365)
   is selected by `v_ntx_lookback_days`. LEFT JOIN ensures users without
   matching NTX rows fall through as Existing Users (conservative).
6. Label each cohort user as `NTC`, `Prev. Competitor Only`, or
   `Existing Users`. Drop Existing Users when
   `v_include_existing_users = FALSE`.
7. For every cohort user, pull all subsequent brand orders in the forward
   window (`days_since BETWEEN 0 AND v_forward_window_days`) — anti-join on
   `cohort_order_id` (a same-day separate brand order still counts as a
   repeat). Sum `final_charge_amt_usd` per repeat order.
8. Bucket each repeat into bucket id `1..N` via
   `CEIL(days_since / bucket_size_days)` where `N = forward_window_days /
   bucket_size_days` (validated to be exact in step 1).
9. Cross-join a hardcoded segment list (so segments with zero cohort users
   still render) with a generated bucket grid `1..N`. For each
   (segment, bucket N), left-join repeat purchases with `bucket_id <= N`
   and aggregate `COUNT(DISTINCT user_id)` and `SUM(sales)` — this gives
   the **cumulative** shape.
10. Divide `n_repeaters_through_bucket` by per-segment cohort size for the
    rate; emit one row per (segment, bucket) with `bucket_unit` echoed and
    `bucket_end` reported in whole units (1.0, 2.0, …, N.0).

## Data Requirements
| Source | Used for |
|---|---|
| `instadata.rds_ads.nexus_coupons` | Resolve campaign UUIDs → `discount_policy_id` for SUAS leg |
| `ads.ads_dwh.multi_touch_click_prioritized_ads_attributions` | SP/Display attribution rows (cohort source A) |
| `ads.ads_dwh.fact_spend_promotion_redemption` | SUAS redemptions (cohort source B) |
| `instadata.etl.agg_ma_order_item_daily_v2` | Brand/country filter, cohort-order delivery dates, forward-window brand orders & sales |
| `instadata.dwh.dim_warehouse` | Country scope (`country_id`) via `partner_id` |
| `ads.ads_dwh.unified_order_item_ntx` | NTB / NTC flags for the cohort-defining order |

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| `v_promo_campaign_ids` | STRING | `'5ae514c3-...,16998f0f-...,381681f9-...'` | CSV of `nexus_coupons.campaign_id` UUIDs (no spaces). Pass `''` if SUAS leg disabled. |
| `v_entity_brand_id` | BIGINT | `564770` | Target brand (`agg_ma_order_item_daily_v2.delivered_entity_brand_id`). |
| `v_cohort_window_start` | DATE | `'2025-10-01'` | First eligible cohort delivered date. |
| `v_cohort_window_end` | DATE | `'2025-12-31'` | Last eligible cohort delivered date. |
| `v_forward_window_days` | INTEGER | `84` | Forward observation window in days. Must be a positive multiple of the bucket unit's day-size (7 for `'WEEK'`, 30 for `'MONTH'`). Default `84`. |
| `v_bucket_unit` | STRING | `'WEEK'` | `'WEEK'` (7-day buckets) or `'MONTH'` (30-day buckets), case-insensitive. Default `'WEEK'`. |
| `v_country_id` | BIGINT | `840` | `dim_warehouse.country_id`. US = `840`, CA = `124`. Default `840`. |
| `v_ntx_lookback_days` | INTEGER | `182` | NTX lookback. `182` reads `new_to_brand_182_day` / `new_to_category_182_day`; `365` reads the 365-day flags. Default `182`. |
| `v_include_existing_users` | BOOLEAN | `FALSE` | If `FALSE`, drop the Existing Users segment (NTB-only output). Default `TRUE`. |
| `v_include_sp_display_attribution` | BOOLEAN | `TRUE` | If `FALSE`, exclude SP/Display attribution from the cohort. Default `TRUE`. |
| `v_include_suas_redemption` | BOOLEAN | `TRUE` | If `FALSE`, exclude SUAS redemption from the cohort. Default `TRUE`. |

## Expected Output
| Column | Description |
|---|---|
| `segment` | `NTC`, `Prev. Competitor Only`, or `Existing Users`. |
| `bucket_unit` | `'WEEK'` or `'MONTH'` — echo of the resolved `v_bucket_unit`, used by the chart to label the x-axis. |
| `bucket_end` | Bucket end in the chosen unit (FLOAT, integer-valued: 1.0, 2.0, …, N.0). With WEEK + 84-day window: 1, 2, …, 12. With MONTH + 120-day window: 1, 2, 3, 4. |
| `cohort_size` | Total cohort users in this segment (constant per segment across buckets). |
| `n_repeaters_through_bucket` | Distinct cohort users in this segment with ≥1 brand repeat order in days `0..bucket_end_day`. |
| `brand_repeat_rate_pct` | `n_repeaters_through_bucket / cohort_size` as a decimal in `[0, 1]`. |
| `brand_repeat_sales_usd` | Sum of brand-order `final_charge_amt_usd` from those repeat orders, days `0..bucket_end_day`. |

Row count = `n_segments × (v_forward_window_days / unit_size_days)`.
With WEEK + 84-day window and `v_include_existing_users = TRUE` →
36 rows (3 × 12). With WEEK + 84-day window and `v_include_existing_users
= FALSE` → 24 rows (2 × 12). With MONTH + 120-day window and Existing
Users included → 12 rows (3 × 4).

## Visual Types
- **Primary:** Bar + line combo on twin axes — bars (left) = cumulative
  brand repeat **rate (%)**, line with markers (right) = cumulative brand
  repeat **sales ($)**, x-axis = `bucket_end` labelled in the unit
  carried by `bucket_unit` ("Weeks Since Conversion" or "Months Since
  Conversion"). Render NTB combined (NTC + Prev. Competitor Only summed)
  as the headline view; the source case omits Existing Users entirely.
- **Secondary:** Same combo rendered separately per segment for "is the
  ramp shape different across NTC vs Prev. Competitor Only?" Or a small-
  multiples grid (one panel per segment).

## Hoped-For Outcome
Acquisition spike → robust 12-week brand repeat ramp, with NTB segments
(NTC + Prev. Competitor Only combined) reaching a healthy cumulative repeat
rate (target 25-35%+ in CPG categories like dairy/eggs) and meaningful
cumulative brand repeat sales by week 12. Comparable shape between NTC and
Prev. Competitor Only validates the "we're getting both genuinely net-new
buyers AND switchers" story; a divergent ramp signals the campaign is
weighted toward one acquisition mode and informs creative/targeting next
quarter. Provides the post-acquisition leg of any payback / LTV-vs-CAC
narrative.

## Sample Call
```sql
CALL SANDBOX_DB.DANIELHAN.a_20260506_873149(
    '5ae514c3-332d-4668-b654-862d95cf755e,16998f0f-578d-411b-8021-eb278004f772,381681f9-6c43-4d5a-81d3-13495fec75de',
    564770,            -- Vital Farms
    '2025-10-01'::DATE,
    '2025-12-31'::DATE,
    84,                -- 12-week observation horizon (days)
    'WEEK',            -- weekly buckets
    840,               -- US
    182,               -- 26-week NTX
    FALSE,             -- NTB-only output
    TRUE,              -- include SP/Display attribution
    TRUE               -- include SUAS redemption
);
```
