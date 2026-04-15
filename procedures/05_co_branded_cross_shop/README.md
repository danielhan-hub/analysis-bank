# Procedure: CO_BRANDED_CROSS_SHOP

## Overview
Measures the mutual benefit of co-branded campaigns between two partner brands. Tracks cross-shop basket rates, trial rates from each other's customer base, and new aisle visibility gained through the partnership.

## Question Themes
- "Did cross-shop increase during the Pepsi + DiGiorno campaign?"
- "How many new customers did each brand gain from the partnership?"
- "What new aisles did my brand gain visibility in through the co-branded campaign?"
- "Is there evidence that co-branded campaigns drive mutual trial?"

## Methodology
1. Define the co-branded campaign period and a comparable pre-period (calculated from pre_period_days)
2. At the basket level, calculate the % of orders containing both Brand A and Brand B for pre-period and campaign period
3. For each brand, identify loyal customers from the partner brand (customers who purchased partner brand during pre-period)
4. Measure trial rate: % of partner brand's loyal customers who purchased this brand for the first time during campaign
5. Identify categories where each brand appeared during campaign but not during pre-period (category expansion)
6. Count order volume in each new category for each brand

## Data Requirements
- Basket-level data (all items per order, by user)
- Brand identification for each item
- Campaign period flags
- User-level brand purchase history (for NTB/trial classification)
- Aisle/department/category classification for each product

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| brand_a_id | VARCHAR | 'PEPSI' | First partner brand ID |
| brand_b_id | VARCHAR | 'DIGIORNO' | Second partner brand ID |
| campaign_start | DATE | '2024-03-01' | Campaign period start |
| campaign_end | DATE | '2024-04-30' | Campaign period end |
| pre_period_days | INTEGER | 60 | Pre-campaign baseline period (days before campaign start) |
| ntb_lookback_days | INTEGER | 365 | Days to define NTB for trial measurement |
| country_id | INTEGER | 840 | Country ID (default: 840 for USA) |

## Expected Output
Unified result table with all three analyses (cross-shop summary, trial analysis, and aisle expansion). The `metric_type` field distinguishes between result sections.

| Column | Description |
|---|---|
| metric_type | 'CrossShop_Summary' for cross-shopping metrics, 'Trial_Analysis' for trial metrics, 'Aisle_Expansion' for category expansion |
| period | For CrossShop_Summary: 'Pre-Campaign' or 'Campaign'; NULL for other metric types |
| total_orders | For CrossShop_Summary: total orders in period; NULL for other metric types |
| orders_with_both | For CrossShop_Summary: orders with both brands; NULL for other metric types |
| cross_shop_pct | For CrossShop_Summary: % of orders with both brands; NULL for other metric types |
| cross_shop_pct_change | For CrossShop_Summary: % change from pre- to campaign period; NULL for other metric types |
| brand | For Trial_Analysis and Aisle_Expansion: brand ID; NULL for CrossShop_Summary |
| partner_loyal_customers | For Trial_Analysis: count of partner brand's loyal customers; NULL for other metric types |
| tried_this_brand_during_campaign | For Trial_Analysis: count who tried this brand for first time; NULL for other metric types |
| trial_rate | For Trial_Analysis: % who tried; NULL for other metric types |
| new_category | For Aisle_Expansion: new category entered during campaign; NULL for other metric types |
| orders_in_category | For Aisle_Expansion: order count in new category; NULL for other metric types |

## Visual Types
- **Primary**: Pre/Post bar chart showing cross-shop basket % growth (e.g., +25%)
- **Secondary**: Two-panel trial rate comparison for each brand
- **Tertiary**: Aisle/category expansion heatmap or list

## Slide References
- Deck 1, Slide 36: "Cross shop between Pepsi & DiGiorno grew +25% during Co-Branded campaigns"
- Deck 1, Slide 37: "Co-branded campaigns boost trial rate for both brands"
- Deck 1, Slide 38: "Co-branded campaign partnership helps Duraflame gain visibility in new aisles"
- Deck 2, Slide 31: "Co-branded partnership helps Oatly gain visibility in new aisles" (1.2% of attributed purchases included both brands)
- Excel rows 76-79: Cross-CPG collaboration case studies

## Hoped-For Outcome
The data shows that co-branded campaigns drive material increases in cross-shop rates (e.g., +25%) and generate new trial for both brands from each other's loyal customer bases. Additionally, brands gain visibility in aisles they wouldn't normally appear in. The seller can position this as: "Partnering with a complementary brand amplifies your reach and drives mutual trial — both brands win."
