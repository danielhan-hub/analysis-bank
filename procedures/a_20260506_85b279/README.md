# a_20260506_85b279 — Brand Cohort Opportunity Analysis

## Overview

For a target brand within one or more categories, classify every repeat
category buyer in the period by their share of category orders that
contained the brand. Splits the buyer base into five cohorts —
**Brand Loyalist**, **Brand-Biased Switcher**, **Switcher, No Brand
Bias**, **Competitor-Biased Switcher**, and **Competitor Loyalist** —
and reports user counts, percent-of-category, and category sales per
cohort. Restricted to retailers (partners) where the brand had any
sales in the period, so non-availability stores don't dilute the
denominator.

The headline output is the size of the **Competitor Loyalist** cohort
— users who bought in the category multiple times but never picked
the brand. That pool is the addressable NTB opportunity, and its
attached category sales is its dollar size.

## When to use (chart-pattern reuse)

The chart contract is a **5-cohort 100% stacked column with the
focal cohort highlighted** plus a footer carrying `n` and the focal
cohort's attached category sales. The output table schema is fixed
at `cohort × {user_count, percent_category_users, sum_total_category_sales}`
across exactly 5 rows. Reuse this procedure for any question whose
answer takes that visual form OR any "size the addressable non-brand
category-buyer pool" question — the **Competitor Loyalist row IS
the addressable pool**, and the other 4 cohorts are the
brand-share-of-wallet context that contextualizes the headline
number. Two clean swap points keep chart.py and the output schema
intact:

1. **Category / brand scope** — `v_brand_ids`, `v_category_ids`,
   `v_country_id` are CSV-parameterized SET inputs. Swap the brand
   and category lists to size the addressable pool for any (brand,
   category-set) tuple. Multi-category rollups (e.g., "Vitamins" =
   VMS super-category expanded into its constituent category IDs)
   are a parameter swap, not a SQL change.
2. **Highlight cohort + headline framing** — the canonical headline
   highlights Competitor Loyalist as the addressable NTB pool.
   The same chart can highlight Brand Loyalist (depth-of-base
   story) or any switcher cohort (addressable depth-of-purchase
   story) with no SQL change — only `chart.py`'s
   `focal_cohort` parameter moves.

For super-category vs category comparison views (e.g., addressable
pool at the broad VMS level vs. just the focal sub-categories),
run the procedure once per scope and stitch the headline cohort
rows into a side-by-side bar — the output schema absorbs N runs
because each row already carries `sum_total_category_sales`.

Concrete reuse targets:

- *Where does the brand's NTB opportunity live within a category — what's
  the cohort breakdown of brand-share-of-wallet among repeat shoppers?* —
  canonical, no swaps.
- *What share of repeat category buyers are competitor loyalists vs.
  brand loyalists vs. switchers?* — canonical 5-cohort breakdown.
- *How big is the addressable NTB pool in users and in category dollars?* —
  read off the Competitor Loyalist row.
- *How large is the category buyer pool on Instacart that has NOT yet
  purchased our brand, and what's its category spend?* — same Competitor
  Loyalist row; the procedure IS the pool-sizing answer even when the
  question is framed as a single-number snapshot rather than a
  5-cohort breakdown.
- *Sizing the upside of continued investment — how many distinct category
  buyers exist in our categories who haven't bought our brand, and how
  much do they spend?* — Competitor Loyalist row, optionally with
  category vs. super-category run-pair stitching for context.
- *For a brand active in multiple categories (eggs vs. butter), how does
  the cohort mix differ category by category?* — run-per-category swap +
  segment-stitch.
- *US vs. Canada — does the brand have a stickier base in one country?* —
  `v_country_id` swap, run-pair stitching.

## Methodology

1. Parse the comma-separated `v_brand_ids` and `v_category_ids` inputs
   into row sets.
2. Identify **partners (retailers)** where the brand had any sales in
   the requested categories during the window — the "available stores"
   universe. Country is scoped via `v2.country_id` directly (the
   underlying table carries it natively; joining `dim_warehouse` on
   `partner_id` would silently multiply rows).
3. For each user shopping the requested categories at those partners
   in the window, count their total category orders, brand-bearing
   category orders, and total category sales.
4. Compute each user's `brand_orders / total_orders` share.
5. Filter to repeat buyers (`total_orders >= v_min_category_orders`,
   default 2) and bucket each user into one of five cohorts based on
   their share:
   - 0% → Competitor Loyalist
   - (0%, 40%] → Competitor-Biased Switcher
   - (40%, 60%) → Switcher, No Brand Bias
   - [60%, 100%) → Brand-Biased Switcher
   - 100% → Brand Loyalist
6. Aggregate to cohort-level user counts, percent-of-total, and
   summed category sales.

## Data Requirements

- `instadata.etl.agg_ma_order_item_daily_v2` — order item facts with
  `delivered_entity_brand_id`, `delivered_entity_category_id`,
  `partner_id`, `user_id`, `order_id`, `final_charge_amt_usd`,
  `delivered_date_pt`, `country_id`. The 3Y+ table is more than wide
  enough for any single-quarter or trailing-12 cohort window.
- Brand IDs sourced from `instadata.etl.ads_taxonomy_products_extd`
  (`entity_brand_id` / `entity_brand_name`).
- Category IDs sourced from the same taxonomy table or by inspecting
  v2's `delivered_entity_category_id` for the brand of interest.

## Parameters

| Parameter | Type | Example | Description |
|---|---|---|---|
| `v_brand_ids` | VARCHAR | `'564770'` | Comma-separated entity_brand_id list (one or many). |
| `v_category_ids` | VARCHAR | `'598,869'` | Comma-separated delivered_entity_category_id list. |
| `v_start_date` | DATE | `'2026-01-01'` | Window start (inclusive). |
| `v_end_date` | DATE | `'2026-03-31'` | Window end (inclusive). |
| `v_country_id` | NUMBER | `840` | 840 = US (default), 124 = CA. |
| `v_min_category_orders` | NUMBER | `2` | Minimum category orders for inclusion (default 2 = "repeat buyer"). |

## Expected Output

| Column | Description |
|---|---|
| `cohort` | Cohort label (Competitor Loyalist, Competitor-Biased Switcher, Switcher No Brand Bias, Brand-Biased Switcher, Brand Loyalist). |
| `user_count` | Distinct user count in the cohort. |
| `total_user_count` | Distinct user count across all cohorts (same value on every row — useful for downstream %). |
| `percent_category_users` | `user_count / total_user_count` (0.0–1.0 share). |
| `sum_total_category_sales` | Sum of category sales attributed to users in the cohort, in USD. |

Result set is exactly 5 rows when all cohorts are populated; missing
cohorts simply don't appear (non-issue — chart code reindexes against
the full cohort list).

## Visual Types

- **Primary:** Single 100% stacked column (5 cohort segments) with
  inline % labels and the focal cohort highlighted in IC_GREEN. The
  footer carries `n` (total users) and the focal cohort's category
  sales in dollars — i.e., the size of the NTB opportunity.
- **Secondary (suggested):** A horizontal bar of cohort-level
  `sum_total_category_sales` if the dollar comparison across cohorts
  matters more than the user-count split.

## Hoped-For Outcome

A one-glance read on whether the brand's growth ceiling within a
category is a NTB acquisition story (large Competitor Loyalist pool)
or a depth-of-purchase story (large switcher pools). Quantifies the
addressable NTB pool both in users and in dollars, making it
straightforward to size acquisition campaigns and forecast
incremental sales potential.

## Sample Call

```sql
CALL SANDBOX_DB.DANIELHAN.brand_cohort_opportunity_analysis(
    '564770',          -- v_brand_ids        (Vital Farms)
    '598,869',         -- v_category_ids     (Chicken Eggs + Egg Substitutes)
    '2026-01-01'::DATE,
    '2026-03-31'::DATE,
    840,               -- v_country_id       (US)
    2                  -- v_min_category_orders
);
```

Then `python chart.py` (smoke test) or `from chart import render_chart`
and call with your own params to render the stacked-bar PNG.
