# Procedure: GOING_DARK_IMPACT

## Overview
Analyzes the impact of pausing or significantly reducing Sponsored Product investment on brand performance over time. Tracks share erosion, user attrition (lapsed rates), and the cost/timeline of recovery.

> **How this differs from Early Bird SP (01)**: This procedure is **longitudinal** — it tracks *one brand over time* through a live → dark → recovery cycle to show the cost of pausing. Early Bird (01) is **cross-sectional** — it compares *many brands in a category* at a single transition point. Use this procedure when you need to tell a specific brand's "what happened when we paused" story. Use 01 when you want to show a category-wide investment pattern.

## Question Themes
- "My partner paused SP for 2 months — what happened to their share?"
- "How long does it take to recover category share after going dark?"
- "What's the lapsed user rate when SP is off vs on?"
- "Show me the impact of reduced SP spend on brand performance"
- "Is it more expensive to restart SP than to stay live?"

## Methodology
1. Accept explicit date ranges for pre-dark, dark, and recovery phases
2. Calculate weekly category share, attributed sales, and lapsed user rates for each phase
3. Track users who were active before the dark period to measure cohort retention
4. Calculate impression share trends across the live → dark → recovery cycle
5. Compare performance metrics across the three periods to quantify impact of pause

## Data Requirements
- Daily/weekly brand SP spend (to identify dark periods)
- Daily/weekly category share by brand
- Daily/weekly attributed sales by brand
- User-level purchase history (to calculate lapsed user rates — users who haven't purchased in N days)
- SP impression share / share of voice
- Placement-level data (for leaky bucket variant)

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| brand_id | INT | 12345 | Target brand ID |
| category_name | VARCHAR | 'FROZEN - PREPARED FOODS' | Category context |
| analysis_start_date | DATE | '2024-01-01' | Analysis window start |
| analysis_end_date | DATE | '2024-12-31' | Analysis window end |
| dark_period_start | DATE | '2024-04-01' | When SP was paused |
| dark_period_end | DATE | '2024-06-01' | When SP resumed |
| lapsed_threshold_days | INT | 30 | Days without purchase = lapsed |
| country_id | INT | 840 | Country ID (default: 840 for USA) |

## Expected Output
| Column | Description |
|---|---|
| period_label | 'Pre-Dark', 'Dark', 'Recovery' |
| week_start | Start of each weekly bucket |
| category_share | Brand's category share |
| attributed_sales | SP-attributed sales |
| total_sales | Total brand sales |
| sp_spend | SP spend |
| lapsed_user_rate | % of previous purchasers who haven't returned |
| impression_share | Paid impression share |

## Visual Types
- **Primary**: Line chart — category share over time with "Dark Period" shaded region, SP spend as secondary axis
- **Secondary**: Bar chart — lapsed user rate comparison (live vs dark vs recovery)
- **Variant**: Waterfall showing share loss during dark period and recovery progress

## Slide References
- Deck 1, Slide 40: "The Cost of Going Dark: How Pausing Sponsored Products Impacts Dial Performance"
- Deck 2, Slides 28-29: Share of basket analysis during live vs dark periods
- Excel rows 65-69: "Impact of going dark" case studies (Frozen, Beverages, general)
- Excel row 57: "Higher lapsed user rate, lower attributed sales when SP investment reduced"
- Excel row 58: "Reducing SP Budget causes share bleed"
- Excel row 74: "Leaky Bucket by Placement Type" variant

## Sub-Variants

### 2A: Recovery Phase Focus — Aggressive Bidding Required
- **Question**: "How aggressive do I need to be to recover share after going dark?"
- **Modification**: Deep dive on the recovery phase only — shows that default bid aggressiveness is key to speed of share recovery
- **Additional Parameters**: `recovery_bid_level` comparison, `placement_type` breakout (default vs non-default)
- **Key Insight**: Recovery is slow especially in default placements; requires aggressive bids
- **Slide refs**: "Recovering Share After Being Dark on SP" deck

## Hoped-For Outcome
The data clearly shows that going dark on SP leads to measurable category share decline and increased user lapse rates. The recovery period shows that regaining lost share takes longer and costs more than maintaining. The seller can tell the partner: "Pausing SP isn't saving you money — it's costing you customers and share that are expensive to win back."
