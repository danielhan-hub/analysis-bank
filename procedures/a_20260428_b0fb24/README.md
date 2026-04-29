# Procedure: BASKET_AFFINITY

## Overview
Analyzes what other categories/products the brand's most loyal customers purchase, identifying over-indexing affinities and untapped category buyer pools who haven't tried the brand.

## Question Themes
- "What adjacent categories should we target with Display?"
- "How many category buyers haven't tried my brand?"
- "What do our most loyal customers buy besides our products?"
- "Where are the biggest untapped opportunities for brand growth?"

## Methodology
1. Define "loyal" customers for the brand (e.g., 3+ purchases in period)
2. For loyal customers, calculate purchase frequency by category across all departments
3. Compare against all category buyers (benchmark) to compute affinity index
4. Rank categories by over-index (affinity index > 1.0)
5. Separately: count total repeating category buyers who have never purchased the brand → untapped opportunity
6. Filter to users at stores where brand is available (distribution-adjusted)

## Data Requirements
- User-level purchase history across all categories/departments
- Brand purchase history (to define loyalty and NTB)
- Store-level brand availability/distribution data
- Category taxonomy (department > category > subcategory)

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| brand_id | INTEGER | 12345 | Target brand ID |
| category_name | VARCHAR | 'DAIRY - BUTTER' | Brand's home category |
| period_start | DATE | '2024-07-01' | Analysis period start |
| period_end | DATE | '2024-09-30' | Analysis period end |
| loyalty_threshold | INTEGER | 3 | Minimum purchases to define "loyal" |
| affinity_min_index | FLOAT | 1.2 | Minimum affinity index to report (1.2 = 20% above benchmark) |
| top_n_categories | INTEGER | 20 | Top N categories to return by affinity |
| country_id | INTEGER | 840 | Country ID (default: 840 for USA) |

## Expected Output
Unified result table with both affinity analysis and penetration gap analysis. The `result_type` field distinguishes between analysis types.

| Column | Description |
|---|---|
| result_type | 'affinity_index' for affinity analysis rows, 'penetration_gap' for opportunity rows |
| adjacent_category | Category name |
| loyal_purchase_rate | % of loyal customers who bought in this category (for affinity_index rows) |
| benchmark_purchase_rate | % of all category buyers who bought in this category (for affinity_index rows) |
| affinity_index | Loyal rate / Benchmark rate (for affinity_index rows) |
| total_repeating_category_buyers | Total users who purchased in this category (for penetration_gap rows) |
| have_tried_brand | Count who purchased the brand (for penetration_gap rows) |
| have_not_tried_brand | Count who have not purchased the brand (for penetration_gap rows) |
| untapped_pct | % untapped (have_not_tried_brand / total_repeating_category_buyers) (for penetration_gap rows) |

## Visual Types
- **Primary**: Horizontal bar chart — categories ranked by affinity index, colored by opportunity size
- **Secondary**: Funnel chart — total category buyers → brand-aware → brand purchasers (showing gap)
- **Callout**: List of specific over-indexing categories with targeting recommendations

## Slide References
- Deck 2, Slide 13: "What other categories do loyal Challenge Dairy customers purchase?" — crusty bread, lactose-reduced milk, ice cream, prepared meals over-index
- Deck 2, Slide 14: "Majority of Q3 repeating category buyers have never tried K&F" — massive untapped opportunity
- Excel rows 87-89: Cross-category targeting and adjacent category case studies

## Hoped-For Outcome
The analysis reveals that loyal customers have strong affinities in adjacent categories (great Display targeting opportunities) and that a massive pool of category buyers has never tried the brand. Seller message: "There's a huge untapped opportunity — X% of category buyers haven't tried your brand. Use Display to reach them in the aisles where your loyalists already shop."
