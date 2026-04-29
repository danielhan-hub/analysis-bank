# Procedure: PROMO_EFFECTIVENESS

## Overview
Measures the effectiveness of promotional tactics (Stock Up & Save, Coupons, Free Gifts) on basket size, cross-brand shopping, repeat purchase behavior, and NTB acquisition.

## Question Themes
- "Do SUAS redeemers buy more and come back?"
- "What's the repeat rate after a coupon redemption?"
- "How does basket size compare for promo redeemers vs non-redeemers?"
- "What are the top product combinations in SUAS baskets?"
- "What % of coupon redeemers are NTB?"

## Methodology
1. Identify all promotion redemptions of the specified type (SUAS/COUPON/FREE_GIFT) for the campaign during the promo period
2. Aggregate basket-level metrics for redeemed orders: average value, units, distinct brands, distinct UPCs
3. Calculate repeat purchase behavior in 30-day, 60-day, and 90-day windows post-promo
4. Measure new-to-brand acquisition: % of redeemers who had no prior brand purchases in the previous 365 days
5. Compare metrics across repeat time windows to show decay/retention over time

## Data Requirements
- Promotion/coupon redemption event data (user, date, promo type, products redeemed)
- Full basket data for redemption orders vs control orders
- Post-redemption purchase history by user
- Brand/UPC/category classification
- NTB flag (did user purchase brand before?)

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| promo_type | VARCHAR | 'SUAS' | Promotion type: 'SUAS', 'COUPON', or 'FREE_GIFT' |
| campaign_id | INTEGER | 98765 | Campaign ID to analyze |
| promo_period_start | DATE | '2025-10-01' | Promo period start |
| promo_period_end | DATE | '2025-10-31' | Promo period end |
| post_purchase_window_days | INTEGER | 90 | Follow-up window for repeat analysis (default: 90) |
| country_id | INTEGER | 840 | Country ID (default: 840 for USA) |

## Expected Output
Metric-based result table organized by metric group and time windows:

| Column | Description |
|---|---|
| metric_group | 'Basket Comparison', 'Repeat Purchase', or 'NTB Analysis' |
| metric_name | Name of the specific metric (e.g., 'Avg Basket Value', 'Repeat Rate Pct', 'NTB Pct of Redeemers') |
| group_label | Context label (e.g., 'Redeemed', 'Non-Redemption', '30 Days', '60 Days', '90 Days', 'New to Brand') |
| metric_value | Numeric value (dollar amount, percentage, or count) |
| metric_count | Associated count (total baskets, total redeemers, etc.) |

Basket Comparison metrics include: Avg Basket Value, Avg Units, Avg Brands per Basket, Avg UPCs per Basket
Repeat Purchase metrics: Total Redeemers and Repeat Rate Pct at 30, 60, and 90-day windows
NTB Analysis metrics: NTB Pct of Redeemers

## Visual Types
- **Primary**: Side-by-side bar chart — redemption vs non-redemption basket metrics
- **Secondary**: Decay curve line chart — repeat rate at 30/60/90 days
- **Tertiary**: Ranked list of top 5-10 product combinations
- **NTB callout**: Pie or stat card showing NTB % of redeemers

## Slide References
- Deck 1, Slide 33: "Top 5 Stock Up & Save Product Combinations"
- Deck 2, Slide 8: "Almost half of SUAS redeemers returned within 90 days" (El Monterey)
- Deck 2, Slides 21-22: "SUAS: 5x higher sales, 4x higher units, 1.5x higher brand cross shop; 48% had 2+ brands, 72% had 2+ UPCs"
- Deck 2, Slide 11: "Free Gift → $18K in repeat brand sales over 14 weeks"
- Excel rows 18, 104-117: SUAS, Coupon, Free Gift case studies

## Sub-Variants

### 6A: Basket-level + Item-level Promo Synergy ("Better Together")
- **Question**: "Should I run SUAS and Coupons at the same time?"
- **Modification**: Compare performance when SUAS (basket-level) and Coupons (item-level) run concurrently vs individually
- **Key Insight**: Synergistic effect — higher combined sales than sum of individual promo types
- **Additional Output**: Interaction effect metric (combined lift vs expected additive lift)
- **Slide refs**: "Basket-level + Item-level Better Together" deck

### Market Application: Free Gift Gifting Events
- Same base procedure applied to Free Gift promotions tied to seasonal gifting occasions
- No parameter changes needed — just set `promo_type = 'FREE_GIFT'`
- **Slide refs**: Multiple Free Gift/Gifting decks

## Hoped-For Outcome
Redemption baskets show dramatically higher basket sizes (5x sales) and cross-brand shopping. Nearly half of redeemers come back within 90 days. Seller message: "SUAS/Coupons don't just drive one-time sales — they create bigger baskets and loyal repeat customers."
