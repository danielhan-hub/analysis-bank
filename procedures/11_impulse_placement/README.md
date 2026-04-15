# Procedure: IMPULSE_PLACEMENT

## Overview
Analyzes the effectiveness of Impulse placements (checkout aisle equivalent) on driving trial (NTB, NTP, NTC), repeat purchase behavior, basket penetration, and trade-up.

## Question Themes
- "What % of impulse purchases are from NTB customers?"
- "Do impulse buyers come back and buy again?"
- "How does impulse compare to SP for trial generation?"
- "Is impulse effective for smaller/single-serve products?"
- "What's the repeat rate for impulse-acquired customers?"

## Methodology
1. Extract impulse placement events (REGEXP pattern match on placement_type) matched to multi-touch attributed orders
2. Classify impulse buyers by newness: NTB, New-to-Product (NTP), New-to-Category (NTC), Existing
3. Track post-impulse repeat brand purchases at 7, 14, and 30-day windows
4. Extract non-impulse (standard featured product) placements as a control group
5. Classify non-impulse buyers by same newness hierarchy
6. Track post-purchase repeat behavior for non-impulse buyers at same windows
7. Compute side-by-side KPIs: NTB trial rate, new customer rate, repeat rates at each window
8. Return metric-based output with impulse_ and non_impulse_ prefixes for direct comparison

## Data Requirements
- Impulse placement exposure and click/purchase data
- User-level brand/product purchase history (for NTB/NTP/NTC classification)
- Post-impulse purchase tracking
- Product size/pack data (for trade-up analysis)
- SP attribution data (for comparison)

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| brand_id | BIGINT | 67890 | Brand ID |
| category_name | VARCHAR | 'Beverages' | Category context |
| campaign_start | DATE | '2025-09-01' | Campaign period start |
| campaign_end | DATE | '2025-09-30' | Campaign period end |
| ntb_lookback_days | INT | 365 | NTB/NTP/NTC lookback window (days) |
| country_id | INT | 840 | Country ID (default: 840 for USA) |

## Expected Output
Metric-based key-value pairs comparing impulse vs non-impulse (featured product) placements:

| Column | Description |
|---|---|
| metric_name | Metric identifier (e.g., 'impulse_ntb_trial_rate', 'impulse_repeat_rate_7day', 'impulse_repeat_rate_14day', 'impulse_repeat_rate_30day', 'impulse_total_first_purchases', 'non_impulse_ntb_trial_rate', 'non_impulse_repeat_rate_7day', 'non_impulse_repeat_rate_14day', 'non_impulse_repeat_rate_30day', 'non_impulse_total_first_purchases') |
| metric_value | Numeric value for the metric (% for trial/repeat rates, count for purchase totals) |

Note: The procedure automatically compares impulse placements against non-impulse (standard featured product) placements in the same time period. All metrics are prefixed with 'impulse_' or 'non_impulse_' to distinguish the placement variant.

## Visual Types
- **Primary**: Side-by-side bar chart — Impulse vs Non-Impulse NTB trial rate
- **Secondary**: Line or bar chart — Repeat rates at 7/14/30 days, impulse vs non-impulse
- **Tertiary**: Summary table — All metrics side-by-side for direct comparison
- **Callout**: Highlight the trial rate difference (impulse typically higher)

## Slide References
- Excel rows 40-46: 7 Impulse placement case studies (Snacks, Energy Drinks, Sales Impact, general)
- Excel row 63: "Canada - Impulse" with NTB, 2/4/8 week repeat rates
- Excel row 72: "Repeat Behavior & case for smaller products"
- Metrics tracked: New to Brand, New to Product, New to Category, 30 Day Repeat Rate, Basket Penetration, Units per Order, Trade-Up

## Sub-Variants

### 11A: Impulse MoM Sales Impact (UPC-level)
- **Question**: "How much did Impulse activation boost my promoted UPCs' sales?"
- **Modification**: Month-over-month comparison at UPC level when Impulse bidding is activated within SP campaigns. Focus on total sales lift rather than NTB metrics.
- **Additional Output**: MoM % change in promoted UPC total sales, promoted UPC category share
- **Slide refs**: "Increase in Sales with Impulse activation" deck (Snacking Brand X)

## Hoped-For Outcome
Impulse placements show high NTB/NTP rates (often 20-40%), solid 30-day repeat behavior, and evidence of trade-up to larger sizes. Seller message: "Impulse is your best trial driver — X% of purchasers were brand new, and Y% came back within 30 days. It's especially effective for smaller pack sizes that lower the trial barrier."
