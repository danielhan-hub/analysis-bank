# Procedure: SHOPPABLE_DISPLAY_REACH

## Overview
Proves that Shoppable Display drives attributed sales on search terms and in aisles that Sponsored Product cannot reach. Measures the incremental user exposure and sales from Shoppable Display beyond SP's footprint.

## Question Themes
- "How much in sales did Shoppable Display drive on terms we can't win with SP?"
- "What % of Shoppable-reached users were completely new to our ads?"
- "Which search terms does Shoppable Display cover that SP doesn't?"
- "Is Shoppable Display just cannibalizing SP, or truly incremental?"

## Methodology
1. Aggregate featured product (shoppable) performance by search term to identify channel coverage and sales
2. Aggregate display performance by search term to identify where shoppable coverage is missing
3. Compare channel coverage using a full outer join to identify display-only terms (gap terms)
4. Track user-level exposure to both display and shoppable product campaigns
5. Classify users by exposure pattern (display-only, SP-only, both)
6. Measure new-to-brand conversion rates by user exposure group
7. Calculate summary KPIs quantifying reach gaps and expansion opportunity

## Data Requirements
- Search term level attribution data by tactic (SP, Shoppable Display)
- User-level exposure data by tactic with timestamps
- Attributed sales by search term, tactic, and user
- NTB classification at user level

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| brand_id | INTEGER | 12345 | Target brand ID |
| category_name | VARCHAR | 'ALCOHOL - TOTAL WINE' | Brand's category context |
| period_start | DATE | '2024-12-01' | Analysis period start |
| period_end | DATE | '2024-12-31' | Analysis period end |
| country_id | INTEGER | 840 | Country ID (default: 840 for USA) |

## Expected Output
The procedure returns temporary view names that contain the analysis results. Query these views to access the three analysis tables:

**RESULT_SEARCH_TERM_GAP** — Search term-level gap analysis
| Column | Description |
|---|---|
| search_term | The keyword |
| sp_attributed_sales_usd | Sales attributed to SP |
| display_attributed_sales_usd | Sales attributed to Display |
| is_gap_term | 1 if display-only (SP has $0), 0 otherwise |
| term_coverage | 'Display Only', 'SP Only', or 'Both' |
| sales_rank | Rank by display attributed sales |

**RESULT_USER_EXPOSURE_OVERLAP** — User-level audience stratification by channel exposure
| Column | Description |
|---|---|
| exposure_group | 'Display Only', 'SP Only', or 'Both' |
| unique_users | Count of unique users |
| ntb_users | Count of new-to-brand users |
| non_ntb_users | Count of existing customers |
| pct_ntb | % of users that are NTB |

**RESULT_SUMMARY_METRICS** — Aggregate KPIs quantifying reach gaps
| Column | Description |
|---|---|
| brand_id | Brand identifier |
| category_name | Category context |
| period_start_date | Analysis start date |
| period_end_date | Analysis end date |
| total_display_gap_sales | Total display sales on display-only terms |
| display_only_term_sales | Display sales on terms with zero SP coverage |
| pct_display_users_not_exposed_to_sp | % of display users not exposed to SP |
| display_only_users | Count of users exposed only to display |
| total_display_users | Total display users |
| analysis_status | Status indicator (e.g., 'Analysis Complete' or 'Insufficient Data') |

## Visual Types
- **Primary**: Table/heatmap of top search terms with Shoppable sales not covered by SP
- **Secondary**: Venn diagram showing user overlap between SP and Shoppable Display
- **Tertiary**: Bar chart comparing NTB rates across tactics

## Slide References
- Deck 2, Slide 15: "Shoppable Display drives NTB sales on search terms not reached by SP" — $23K on unreachable terms
- Deck 2, Slide 20: "Homebake Shoppable Ads drove awareness outside the Aisle" — $146K from users not exposed to SP in L30 days
- Excel rows 87-97: Shoppable Display case studies on reach, NTB, cross-category keywords

## Hoped-For Outcome
The data shows significant attributed sales on search terms where SP has zero coverage, proving Shoppable Display is truly incremental (not cannibalizing). Seller message: "Shoppable Display drove $X in sales on terms your SP campaigns can't reach. It's not a replacement — it fills gaps and brings in new customers from different aisles."
