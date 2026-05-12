# SP Cumulative ROAS by Clicker Cohort

`SANDBOX_DB.DANIELHAN.a_20260511_1c68bb`

## Overview

For an SP advertiser (`account_id`), define a clicker cohort over a
**cohort window**, then track that cohort's SP-attributed sales,
SP click spend, and cumulative ROAS across the full **chart window**
bucketed by month / quarter / week / etc.

Output is a tidy time-series table (one row per bucket) that the
companion `chart.py` renders as a combo chart: cumulative sales bars
(positive), cumulative spend bars (negative mirror), and a cumulative
ROAS line overlay with inline value labels.

The procedure resolves the account's SP campaign IDs at query time via
`rds.ads_production.campaigns_records` (overlap with the chart window,
`workflow_state='active'`) — there is no hardcoded campaign IN-list and
no campaign-name filter.

## When to use (chart-pattern reuse)

The chart contract is a **cumulative-ROAS combo time series** (left
axis = cumulative sales/spend in USD, right axis = cumulative ROAS line
with inline value labels). The output table schema is fixed at
`bucket × {bucket_sales, bucket_spend, cumulative_sales, cumulative_spend, cumulative_roas}`.
Reuse this procedure for any question whose answer takes that visual
form — even if the cohort source, time-axis cadence, or cohort-window
length differs from the canonical version. **Three swap points** keep
chart.py and the output schema intact:

1. **Cohort-defining CTE** — currently the `clickers` CTE: distinct
   users with a billable SP click on the resolved campaigns within the
   cohort window. The same downstream pipeline (sales-per-bucket +
   spend-per-bucket + cumulative window functions) accepts any
   user-grain cohort definition: NTB-via-SP cohort (drop in the
   `new_to_brand_multi_touch_click_prioritized_ads_attributions`
   table), promo-redeemers, all-clickers across the full chart window,
   first-touch SP buyers, etc. The output schema is unchanged.
2. **Bucket cadence (`v_bucket` parameter)** — currently `'quarter'`.
   Pass `'month'`, `'week'`, `'day'`, or any other Snowflake
   `DATE_TRUNC` `date_or_time_part`. The output schema (`bucket DATE`,
   plus the same five metric columns) is invariant; chart.py's
   `_bucket_label` helper renders the appropriate x-axis tick format.
3. **Cohort window vs chart window** — the cohort window
   (`v_cohort_start`, `v_cohort_end`) is a parameter-controlled subset
   of the chart window (`v_chart_start`, `v_chart_end`). Q3-only
   cohort tracked across Jul-Dec is one configuration; "first 30 days
   of clickers tracked across 6 months" or "full-window cohort tracked
   across the same window" are equally valid configurations. Same SQL,
   same chart, different rhetorical framing of "what cohort, observed
   over what period."

For multi-cohort comparisons (e.g., "Q3 clicker cohort vs Q4 clicker
cohort, both tracked across the rest of the year"), run the procedure
once per cohort and stitch the output frames together for a single
overlay chart — the cumulative-curve pattern absorbs an arbitrary
number of cohort lines.

Concrete reuse targets:

- *What's the cumulative ROAS over time for users who clicked an
  account's SP campaigns during the cohort window?* — canonical, no
  swaps.
- *Cumulative SP ROAS by month for a quarterly clicker cohort* — swap
  bucket cadence: `v_bucket='quarter'` → `v_bucket='month'`.
- *Cumulative SP ROAS for users acquired (NTB-via-SP) in a cohort
  window, tracked across the chart window* — swap cohort CTE: replace
  `clickers` (consolidated_conversions) with NTB-attribution-based
  cohort.
- *Bi-weekly cumulative SP sales, spend, and ROAS for a clicker cohort* —
  swap bucket cadence: `v_bucket='week'` (and adjust labels).
- *First-30-days clicker cohort, monthly cumulative ROAS over 6 months* —
  swap cohort window (cohort_end = chart_start + 30 days) and bucket
  cadence (`v_bucket='month'`).
- *NTB-acquired-via-SP cohort, monthly cumulative ROAS over 6 months* —
  swap cohort CTE → NTB cohort; swap bucket cadence → `'month'`.

## Methodology

1. **Resolve campaigns dynamically.** Pull `after:id` from
   `rds.ads_production.campaigns_records` for the account where
   `campaign_type = :v_campaign_type`, `workflow_state = 'active'`, and
   the activity window overlaps `[v_chart_start, v_chart_end]`.
   `LISTAGG` the IDs into a comma-separated string, then materialize as
   a `campaign_ids` CTE via `STRTOK_SPLIT_TO_TABLE`.
2. **Identify SP product universe.** From
   `agg_featured_product_daily`, `SELECT DISTINCT product_id` for the
   resolved campaigns over the chart window.
3. **Build clicker cohort.** From `consolidated_conversions`, take
   `DISTINCT user_id` for billable
   (`charged_nanos_usd > 0`) clicks
   (`event_name = 'click.click_featured_product'`) on the resolved
   campaigns within the cohort window only.
4. **Bucket sales.** From `agg_ma_order_item_daily_v2`, sum
   `final_charge_amt_usd` for cohort users buying campaign products,
   grouped by `DATE_TRUNC(:v_bucket, delivered_date_pt)`.
5. **Bucket spend.** From `consolidated_conversions`, sum
   `charged_nanos_usd * 1e-9` for the cohort's clicks on the campaigns
   over the chart window, grouped by `DATE_TRUNC(:v_bucket, event_date_pt)`.
6. **Cumulate.** Window-function running sums of sales and spend
   ordered by bucket; `cumulative_roas = DIV0(cum_sales, cum_spend)`.

## Data Requirements

| Table | Used For |
|---|---|
| `rds.ads_production.campaigns_records` | Dynamic campaign-ID resolution |
| `ads.ads_dwh.agg_featured_product_daily` | SP product universe per campaign |
| `ads.ads_dwh.consolidated_conversions` | Clicker cohort + click spend |
| `instadata.etl.agg_ma_order_item_daily_v2` | SP-attributed sales by cohort |

No `dim_warehouse` join is required (orders carry `country_id` natively
and we scope by `account_id` → `campaign_id`, not by country).

## Parameters

| Parameter | Type | Example | Description |
|---|---|---|---|
| `v_account_id` | `BIGINT` | `45` | Ads account ID (the SP advertiser). |
| `v_chart_start` | `DATE` | `'2025-07-01'` | First day of the chart/observation window. |
| `v_chart_end` | `DATE` | `'2025-12-31'` | Last day of the chart/observation window. |
| `v_cohort_start` | `DATE` | `'2025-07-01'` | First day of the cohort-definition window (subset of chart window). |
| `v_cohort_end` | `DATE` | `'2025-09-30'` | Last day of the cohort-definition window. |
| `v_bucket` | `VARCHAR` | `'quarter'` | `DATE_TRUNC` granularity: `'month'`, `'quarter'`, `'week'`, `'day'`, etc. Default `'quarter'`. |
| `v_campaign_type` | `VARCHAR` | `'featured_product'` | Campaign type filter on `campaigns_records`. Default `'featured_product'` (SP). |

## Expected Output

| Column | Description |
|---|---|
| `bucket` | First day of the bucket (DATE; `DATE_TRUNC(:v_bucket, ...)`). |
| `bucket_sales` | SP-attributed sales by the cohort in this bucket (USD, FLOAT). |
| `bucket_spend` | SP click spend by the cohort in this bucket (USD, FLOAT). |
| `cumulative_sales` | Running sum of `bucket_sales` ordered by `bucket`. |
| `cumulative_spend` | Running sum of `bucket_spend` ordered by `bucket`. |
| `cumulative_roas` | `cumulative_sales / cumulative_spend` (DIV0-safe). |

## Visual Types

- **Primary:** Combo chart — cumulative sales bars (positive, lime) +
  cumulative spend bars (negative-axis mirror, pomegranate) +
  cumulative ROAS line overlay (kale, inline value labels). Rendered
  by `chart.py::render_chart(...)`.
- **Secondary:** The same dataset can be rendered as a stacked-area
  cumulative chart, a per-bucket bar pair (sales vs spend without
  cumulation), or a single cumulative-ROAS line.

## Hoped-For Outcome

The cumulative ROAS curve crosses break-even (1.0) within the cohort's
observation window and continues climbing as the cohort's spend tapers
relative to ongoing sales — i.e., the SP investment "pays back" within
N buckets and continues delivering tail value. A flattening or
declining cumulative ROAS line late in the chart window signals
diminishing returns from the cohort.

## Sample Call

```sql
USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;
USE WAREHOUSE DEVELOPER_XL_WH;

CALL SANDBOX_DB.DANIELHAN.a_20260511_1c68bb(
    45,                          -- v_account_id (Vital Farms)
    '2025-07-01'::DATE,          -- v_chart_start
    '2025-12-31'::DATE,          -- v_chart_end
    '2025-07-01'::DATE,          -- v_cohort_start
    '2025-09-30'::DATE,          -- v_cohort_end (Q3 2025 clicker cohort)
    'quarter',                   -- v_bucket
    'featured_product'           -- v_campaign_type
);
```

Render the chart from Python:

```python
from chart import render_chart

render_chart(
    v_account_id=45,
    v_chart_start="2025-07-01",
    v_chart_end="2025-12-31",
    v_cohort_start="2025-07-01",
    v_cohort_end="2025-09-30",
    v_bucket="quarter",
    v_campaign_type="featured_product",
    output_path="my_chart.png",
)
```
