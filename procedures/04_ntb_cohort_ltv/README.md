# Procedure: NTB_COHORT_LTV

## Overview
Tracks a cohort of New-to-Brand customers acquired via ads over a follow-up window (6-12 months) to demonstrate long-term value. Shows cumulative repeat sales, improving ROAS over time, and retention curves.

## Question Themes
- "Do NTB customers acquired via SP come back and buy again?"
- "What's the 6-month LTV of ad-acquired customers?"
- "How does ROAS improve over time when you factor in repeat purchases?"
- "Prove that SP investment pays off beyond the initial attributed sale"
- "What's the repeat rate for customers acquired during our Q1 campaign?"

## Methodology
1. Define an acquisition cohort: NTB users who first purchased the brand during the specified period via Sponsored Product
2. Track all subsequent brand purchases by this cohort over the follow-up window
3. Bucket purchases into monthly time intervals (Month 1 through Month 12+)
4. Calculate cumulative sales and retention rate for each time bucket
5. Compute cumulative ROAS progression (total cohort sales / SP spend in acquisition period)
6. Compare initial-period ROAS vs extended ROAS to show long-term value multiplier

## Data Requirements
- User-level first brand purchase date (NTB moment)
- Ad attribution at user level (which tactic drove the first purchase)
- All subsequent brand purchases by cohort users
- SP spend during acquisition period
- Tactic-level attribution data

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| brand_id | VARCHAR | '55551' | Target brand ID |
| category_name | VARCHAR | 'PET FOOD' | Category context |
| acquisition_period_start | DATE | '2024-01-01' | Cohort acquisition window start |
| acquisition_period_end | DATE | '2024-03-31' | Cohort acquisition window end |
| follow_up_window_days | INTEGER | 180 | How long to track post-acquisition (days) |
| ntb_lookback_days | INTEGER | 365 | Days to define NTB |
| country_id | INTEGER | 840 | Country ID (default: 840 for USA) |

## Expected Output
Unified result table with both cohort summary and over-time progression. The `output_type` field distinguishes between summary-level and time-bucket-level rows.

| Column | Description |
|---|---|
| output_type | 'COHORT_SUMMARY' for summary row, 'COHORT_OVER_TIME' for monthly breakdown rows |
| cohort_label | e.g., 'Q1 2024 NTB via SP - PET FOOD' |
| cohort_size | Number of NTB users acquired |
| acquisition_period_sales | Total sales during acquisition period |
| acquisition_period_spend | SP spend during acquisition period |
| initial_roas | Sales / Spend in acquisition period |
| time_bucket | NULL for COHORT_SUMMARY; e.g., 'Month 1', 'Month 2', ... 'Beyond 12 months' for COHORT_OVER_TIME |
| cumulative_sales | NULL for COHORT_SUMMARY; running total of all brand sales from cohort for each time bucket |
| cumulative_roas | NULL for COHORT_SUMMARY; cumulative sales / original acquisition spend for each time bucket |
| pct_cohort_retained | NULL for COHORT_SUMMARY; % of original cohort who purchased in this bucket |
| incremental_sales_this_period | NULL for COHORT_SUMMARY; new sales in this period from the cohort |

## Visual Types
- **Primary**: Cumulative line chart — cohort sales growing over time with ROAS callouts
- **Secondary**: Bar chart showing initial ROAS vs 3-month vs 6-month ROAS
- **Tertiary**: Retention curve: % of cohort still purchasing at each time bucket
- **Reference**: Speaker notes from Deck 1 Slide 35 contain actual SQL params: `period_start_dt = '2021-10-01'; period_end_dt = '2021-12-31'; post_purchase_window_days = 180`

## Slide References
- Deck 1, Slide 34: Poise user acquisition — initial ROI 6.6x → long-run 13.1x
- Deck 1, Slide 35: Contains SQL parameter references (period_start_dt, period_end_dt, post_purchase_window_days)
- Deck 2, Slide 9: "NTB customers acquired via ads in Q1 continued to drive repeat brand sales into Q2" ($1.06MM → $1.31MM, +23%)
- Deck 2, Slide 11: "Saucy Spoon's Free Gift promotion → $18K in repeat brand sales over 14 weeks"
- Deck 2, Slide 16: "Cold Cuts SP acquisition → +17x return on repeat customers over 6 months"
- Deck 2, Slide 17: "Freshpet SP cohort: sales/spend ratio increases from $9.3 to $12.6 over 12 months"
- Excel rows 49-50: "Repeat Rate" and "Long Term Value" case studies
- Excel row 104: "Coupon Users Continue to Purchase Brand" (long term value)

## Hoped-For Outcome
The data shows that NTB customers don't just buy once — they continue purchasing the brand for months. The cumulative ROAS can double or triple the initial figure. The seller can say: "Your initial ROAS of 3x is actually understating the value — when you factor in repeat purchases, those customers delivered 9x+ over 6 months."
