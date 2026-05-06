# promo_cohort_brand_repeat_by_segment

## Overview

For users who **first redeemed a target promotion** within a defined cohort
window (e.g., SUAS, Free Gift, BOGO), track cumulative **brand repeat
behavior** over a configurable forward observation window — bucketed at a
configurable cadence (default: 12 weeks observed in 2-week buckets, yielding
weeks 2/4/6/8/10/12).

The cohort is split at query time into three mutually exclusive segments
based on the redeemer's NTX flags on the cohort-defining order's brand
items:

| Segment | NTB? | NTC? | Meaning |
|---|---|---|---|
| `NTC` | TRUE | TRUE | Truly net-new buyer — never bought ANY brand in the category in the prior 365d. |
| `Prev. Competitor Only` | TRUE | FALSE | New to the brand but had bought competing brands in the same category. |
| `Existing Users` | FALSE | — | Had bought the brand in the prior 365d (or had no NTX row at all — conservative default). |

`NTB = NTC ∪ Prev. Competitor Only` and is derived at viz time by summing
the two NTB sub-segments — not emitted as its own row.

For each (segment, bucket) the procedure emits:

- `brand_repeat_rate_pct` — share of cohort users in the segment who placed
  ≥1 brand order in days `0..bucket_end_day`.
- `brand_repeat_sales_usd` — cumulative brand `final_charge_amt_usd`
  attributable to those users in days `0..bucket_end_day`.

"Repeat" = ANY brand order (promo or not) **except** the cohort-defining
redemption order itself (anti-join on `order_id`, not on `delivered_date_pt`
— a same-day separate brand order still counts as a repeat).

## Question Themes

- "Of users we acquired with the [SUAS / Free Gift / BOGO] push, how
  quickly do they come back to the brand on their own?"
- "How does the 12-week brand-repeat trajectory differ for net-new
  customers (NTC) vs. customers we stole from a competitor vs. existing
  customers we re-engaged?"
- "How much downstream brand revenue do we book in the 84 days following
  a promo redemption, and how is it distributed across the three
  acquisition segments?"
- "At what week does the NTB cohort's repeat curve plateau vs. continue
  climbing?"
- "Is the existing-users repeat rate (the 'cannibalization' read) close
  to baseline, or did the promo materially shift their cadence?"
- "Which segment delivers the most repeat sales per redeemer over the
  observation window?"

## Methodology

1. Parse `v_promo_campaign_ids` (CSV of `nexus_coupons.campaign_id`
   UUIDs) into a single-column table.
2. Resolve those campaigns to `discount_policy_ids` via
   `instadata.rds_ads.nexus_coupons` (no hardcoded discount_policy_id
   list — campaigns are the stakeholder-named anchor).
3. Build the **cohort**: each user's first VALID promo-redemption order
   in `[v_cohort_window_start, v_cohort_window_end]` from
   `ads.ads_dwh.fact_spend_promotion_redemption`. Tiebreaker = smallest
   `order_id` within the same delivered date.
4. Pull NTX flags for **brand items on each user's cohort order** by
   joining `ads.ads_dwh.unified_order_item_ntx` to
   `instadata.etl.agg_ma_order_item_daily_v2` (filtered to
   `delivered_entity_brand_id = v_entity_brand_id` and
   `country_id = v_country_id` via `dim_warehouse`). Aggregate to user
   grain via `MAX(IFF(...))` so any TRUE on any brand item flips the
   user. LEFT JOIN ensures no-NTX-row users default to `f_ntb=0,
   f_ntc=0` (Existing Users).
5. Label each cohort user's segment per the table above.
6. Pull **forward-window brand purchases** for each cohort user from
   `agg_ma_order_item_daily_v2`: any brand order on
   `[cohort_date, cohort_date + v_forward_window_days]`, **anti-joined
   on `order_id`** (not on date) so same-day separate brand orders
   count.
7. Bucket each forward purchase by
   `LEAST(GREATEST(CEIL(days_since / v_bucket_size_days), 1), N)`
   where `N = v_forward_window_days / v_bucket_size_days`.
8. Build the (`segment` × `bucket`) grid (segments hardcoded so all 3
   appear even if one is empty); LEFT JOIN bucketed purchases with
   `bucket_id <= grid.bucket_id` to produce **cumulative** counts and
   sums per (segment, bucket).
9. Final SELECT joins to per-segment cohort sizes and computes
   `brand_repeat_rate_pct = DIV0(repeaters, cohort_size)`.

## Data Requirements

| Source | Why |
|---|---|
| `instadata.rds_ads.nexus_coupons` | Resolves `campaign_id` UUIDs → `discount_policy_id`. |
| `ads.ads_dwh.fact_spend_promotion_redemption` | Cohort-defining promo redemption events. |
| `ads.ads_dwh.unified_order_item_ntx` | NTB / NTC flags per user-order-item. |
| `instadata.etl.agg_ma_order_item_daily_v2` | Brand-item filter for NTX scoping AND forward-window brand purchase tracking. |
| `instadata.dwh.dim_warehouse` | Country scope (US = 840, CA = 124) via `partner_id → country_id`. |

## Parameters

| Parameter | Type | Example | Description |
|---|---|---|---|
| `v_promo_campaign_ids` | STRING | `'5ae514c3-…,16998f0f-…,381681f9-…'` | Comma-separated `nexus_coupons.campaign_id` UUIDs (no spaces). All campaigns are unioned. |
| `v_entity_brand_id` | BIGINT | `564770` | The brand to track (Vital Farms = 564770). Must match `agg_ma_order_item_daily_v2.delivered_entity_brand_id`. |
| `v_cohort_window_start` | DATE | `'2025-08-05'` | First day a redemption can land for the cohort. |
| `v_cohort_window_end` | DATE | `'2025-10-11'` | Last day a redemption can land for the cohort. |
| `v_forward_window_days` | INTEGER (DEFAULT 84) | `84` | How many days to observe each user after their cohort date. Should be a multiple of `v_bucket_size_days`. Caller must ensure `v_cohort_window_end + v_forward_window_days <= today`, else late-cohort users have truncated observation. |
| `v_bucket_size_days` | INTEGER (DEFAULT 14) | `14` | Bucket width in days. 14 = 2-week buckets. |
| `v_country_id` | BIGINT (DEFAULT 840) | `840` | Country scope: US = 840, Canada = 124. |

## Expected Output

3 × N rows where N = `v_forward_window_days / v_bucket_size_days`. With
defaults (84d window, 14d buckets) → 18 rows.

| Column | Description |
|---|---|
| `segment` | One of `'NTC'`, `'Prev. Competitor Only'`, `'Existing Users'`. |
| `weeks_since_conversion` | Bucket end as a fraction-of-a-week (FLOAT). With 14d buckets: `2.0, 4.0, 6.0, 8.0, 10.0, 12.0`. |
| `cohort_size` | Number of cohort users in the segment (constant within a segment). |
| `n_repeaters_through_bucket` | Distinct users in the segment with ≥1 qualifying brand order in days `0..bucket_end_day`. |
| `brand_repeat_rate_pct` | `DIV0(n_repeaters_through_bucket, cohort_size)` — fraction in `[0, 1]`. Multiply by 100 for chart display. |
| `brand_repeat_sales_usd` | Cumulative brand `final_charge_amt_usd` from those users' qualifying orders. |

## Visual Types

- **Primary:** bar+line combo on twin axes — cumulative brand repeat
  rate (%) as bars on the left axis, cumulative repeat sales ($) as a
  line overlay on the right axis. Rendered twice in `chart.py`:
  - `chart_1.png` — **Overall** (all 3 segments summed).
  - `chart_2.png` — **NTB only** (NTC + Prev. Competitor Only summed).
- **Secondary:** small-multiples — one bar+line panel per segment
  side-by-side — when the audience needs to compare segment trajectories
  directly. Or a stacked-bar of cumulative `brand_repeat_sales_usd` by
  segment over weeks if the dollar contribution by segment is the story.

## Hoped-For Outcome

A clean 12-week (or other window) view that lets a stakeholder read off
how quickly a promo-acquired cohort starts contributing organic brand
revenue — and whether NTC and Prev. Competitor Only users behave
materially differently from the existing user base who happened to grab
the offer.

## Reuse

```python
from chart import render_chart

# Different brand, different promo type, 12-week observation, 4-week buckets
fig = render_chart(
    v_promo_campaign_ids="aaaa-...,bbbb-...",
    v_entity_brand_id=412005,
    v_cohort_window_start="2025-09-01",
    v_cohort_window_end="2025-10-31",
    v_forward_window_days=84,
    v_bucket_size_days=28,
    output_path_overall="overall.png",
    output_path_ntb="ntb.png",
)
```

The procedure is also callable directly:

```sql
CALL SANDBOX_DB.DANIELHAN.promo_cohort_brand_repeat_by_segment(
    '5ae514c3-332d-4668-b654-862d95cf755e,16998f0f-578d-411b-8021-eb278004f772,381681f9-6c43-4d5a-81d3-13495fec75de',
    564770,
    '2025-08-05'::DATE,
    '2025-10-11'::DATE,
    84,
    14,
    840
);
```

## Bundle Contents

- `procedure.sql` — the parameterized stored procedure.
- `chart.py` — `render_chart(...)` callable that pulls fresh data via
  `iq.query("CALL SANDBOX_DB.DANIELHAN...")` and writes
  `chart_1.png` (Overall) + `chart_2.png` (NTB).
- `chart_1.png`, `chart_2.png` — source-case rendered charts (Vital
  Farms SUAS, 2025-08-05..2025-10-11 cohort).
- `suas_12w_brand_repeat.csv` — source-case CSV the original notebook
  rendered from. Reuse callers don't need this; it's preserved as
  evidence.
