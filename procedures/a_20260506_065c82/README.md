# a_20260506_065c82

## Overview

Monthly cumulative SP ROAS for a **fixed cohort** of users who clicked on
a target set of Sponsored Product campaigns during a defined cohort
window. The procedure returns one row per calendar month across the full
chart window, with monthly and cumulative sales, spend, and ROAS — built
to feed a combo chart that shows how returns build over time as repeat
and latency-driven purchases arrive after the initial click.

The fixed-cohort framing isolates "what did the *<cohort window>*
clickers go on to do?" — most useful for arguing that SP returns
compound past the click month (delivery lag, repeat purchase, basket
expansion). Setting `v_cohort_month_end = v_chart_end` collapses it to
a vanilla all-clickers cumulative ROAS over the window.

## When to use (chart-pattern reuse)

The chart contract is a **bar+line cumulative-ROAS combo over a time
axis** for a fixed user cohort: positive bars = cumulative attributed
sales, negative bars = cumulative spend, line overlay = cumulative
ROAS. Reuse this procedure for any question whose answer takes that
visual form, even if the cohort source or time axis differs from the
canonical "SP clicker × calendar month" version. Two clean swap points:

1. **Cohort CTE** — the procedure's `clickers` CTE (users with billable
   clicks on the target campaigns in the cohort window) is the swap
   point. Replace it with an NTB-via-SP cohort, an NTB-via-any-ad
   cohort, a promo-redeemer cohort, or any other cohort definition
   whose first-touch event lands in the cohort window. The downstream
   SQL (sales aggregation, spend aggregation, cumulative running sums)
   doesn't change — it just sees a different `user_id` set.
2. **Time-axis expression** — the canonical version buckets by
   `DATE_TRUNC('month', delivered_date_pt)` (calendar month). Swap to
   `DATEDIFF('month', cohort_acquisition_date, delivered_date_pt)` to
   get **months-since-acquisition** instead. The output table schema
   (cohort × month-bucket × {cum_sales, cum_spend, cum_roas}) is fixed
   and `chart.py` renders both axes identically — only the x-axis
   label changes.

Concrete reuse targets:

- "If we look only at our January SP clickers, what's their cumulative
  ROAS by month through April?" — canonical, no swaps.
- "How much of a campaign's eventual ROAS lands after the click month?"
- "Does the cohort that clicked during the launch window keep coming back
  on subsequent months?"
- "What is the trajectory of cumulative SP ROAS for this brand across the
  current quarter?"
- "How does cumulative ROAS for [campaign set] shape up — running
  stronger or weaker than a 1.0x payback line?"
- "For NTB customers acquired via SP, how does cumulative attributed
  ROAS evolve as the cohort matures over months-since-acquisition?" —
  swap clicker CTE → NTB-via-SP CTE; swap calendar-month bucket →
  months-since-acquisition.
- "Cohort-maturity ROAS curve for an ad-acquired NTB cohort over 12+
  months since acquisition" — same two swaps.
- "Across a multi-campaign national portfolio, what's the combined
  cumulative ROAS picture?"

## Methodology

1. Parse the input `v_campaign_ids` comma-separated string into a
   single-column table of campaign IDs.
2. Pull the universe of products featured by any of those campaigns
   during the cohort window (`campaign_products`) — purchase
   attribution is restricted to this SKU set.
3. Build the **clicker cohort**: distinct users with at least one
   billable click (`event_name = 'click.click_featured_product'`,
   `charged_nanos_usd > 0`) on any target campaign between
   `v_chart_start` and `v_cohort_month_end`. UTC range is used for
   partition pruning; PT range enforces correctness.
4. Aggregate **monthly sales** for the cohort: join
   `agg_ma_order_item_daily_v2` to the cohort and to
   `campaign_products`, sum `final_charge_amt_usd` by delivered month
   over the chart window.
5. Aggregate **monthly spend**: sum `charged_nanos_usd * 1e-9` for the
   same cohort's billable clicks on the target campaigns, by PT month,
   across the full chart window.
6. Outer-join the two monthly series and emit cumulative running sums
   plus `DIV0(cumulative_sales, cumulative_spend)` as cumulative ROAS.

## Data Requirements

| Source | Why |
|---|---|
| `ads.ads_dwh.consolidated_conversions` | Cohort definition (Jan clickers) AND ongoing click spend tracking. |
| `ads.ads_dwh.agg_featured_product_daily` | Universe of products featured by the target campaigns (sales attribution scope). |
| `instadata.etl.agg_ma_order_item_daily_v2` | Delivered SP-attributed sales for the cohort on featured SKUs. |

## Parameters

| Parameter | Type | Example | Description |
|---|---|---|---|
| `v_campaign_ids` | STRING | `'266584,297686,311207'` | Comma-separated SP campaign IDs (no spaces). |
| `v_chart_start` | DATE | `'2026-01-01'` | First day of the chart window (also start of cohort window). |
| `v_chart_end` | DATE | `'2026-04-30'` | Last day of the chart window (sales + spend tracked through here). |
| `v_cohort_month_end` | DATE | `'2026-01-31'` | Last day of the cohort-definition window — only clicks at or before this date qualify a user for the cohort. Set `= v_chart_end` to collapse to all-clickers. |

## Expected Output

| Column | Description |
|---|---|
| `month` | First day of the calendar month (DATE). One row per month from `v_chart_start` through `v_chart_end` for which the cohort had any sales. |
| `monthly_sales` | SP-attributed sales by the cohort that delivered in that month (USD, FLOAT). |
| `monthly_spend` | Cohort's SP click spend on the target campaigns that month (USD, FLOAT). Zero for months with no follow-on clicks. |
| `cumulative_sales` | Running sum of `monthly_sales` ordered by month (USD, FLOAT). |
| `cumulative_spend` | Running sum of `monthly_spend` ordered by month (USD, FLOAT). |
| `cumulative_roas` | `cumulative_sales / cumulative_spend` (DIV0-protected, FLOAT). |

## Visual Types

- **Primary:** bar+line combo on twin axes — cumulative sales as positive
  bars (lime), cumulative spend as negative bars (pomegranate),
  cumulative ROAS as a line overlay (kale) with `$x.x` value labels at
  each point. See `chart.py` (`render_chart(...)`).
- **Secondary:** a plain monthly bar chart of `monthly_sales` /
  `monthly_spend` works when the audience cares about the period-by-period
  shape rather than the running total.

## Hoped-For Outcome

A clean, deck-ready combo chart that lets a stakeholder read off the
final cumulative ROAS at the right edge while seeing visually how the
ratio rose (or fell) month-over-month — most useful for arguing that
ROAS reported at the end of the cohort window understates true returns
because subsequent months add sales with relatively little additional
spend.

## Reuse

```python
from chart import render_chart

# Different brand's national SP set, last quarter
fig = render_chart(
    v_campaign_ids="412001,412002,412015",
    v_chart_start="2026-01-01",
    v_chart_end="2026-06-30",
    v_cohort_month_end="2026-02-28",   # 2-month cohort window
    output_path="my_chart.png",
)
```

The procedure is also callable directly:

```sql
CALL SANDBOX_DB.DANIELHAN.a_20260506_065c82(
    '412001,412002,412015',
    '2026-01-01'::DATE,
    '2026-06-30'::DATE,
    '2026-02-28'::DATE
);
```
