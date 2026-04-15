# Procedure: MULTI_TACTIC_SYNERGY

## Overview
Proves the incremental value of layering ad tactics (Display, Shoppable Display, Shoppable Video, SFB, Recipes) on top of Sponsored Product. Measures reach expansion, conversion lift, basket size increase, and NTB contribution by tactic combination.

> **See also: Tentpole Strategy (10)** — If the question is about multi-tactic performance *during a specific holiday or event*, use 10 instead. This procedure (03) is **tactic-centric** ("does adding Display to SP help?") while 10 is **event-centric** ("how should I invest for Thanksgiving?"). The underlying data overlaps, but the framing and output differ.

## Question Themes
- "How many more customers can we reach by adding Display to SP?"
- "Does layering tactics increase basket size?"
- "What % of Display-attributed sales come from NTB customers?"
- "Show me the synergy between SP and Shoppable Display"
- "What's the brand conversion rate when using full-funnel vs SP only?"
- "Did Recipes add any incremental reach?"

## Methodology
1. Identify users exposed to each ad tactic (SP and Display) for the target brand during the analysis period
2. Classify users into tactic combinations: SP Only, Display Only, or SP + Display
3. Match users to their attributed orders and calculate conversion metrics per combination
4. Enrich orders with NTB (new-to-brand) flag to measure acquisition contribution
5. Calculate reach penetration against total category buyers for the period
6. Index conversion rates relative to best-performing combination

## Data Requirements
- User-level ad exposure by tactic (SP, Display, Shoppable Display, Shoppable Video, SFB, Coupon, Recipes)
- User-level purchase data (brand, category, basket contents, order value, units)
- Attribution data (which tactic attributed to which sale, by model)
- Daily total category buyer counts
- NTB/NTC classification per user (based on lookback window)

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| brand_id | INTEGER | 45678 | Target brand ID |
| category_name | VARCHAR | 'SALTY SNACKS' | Category context |
| period_start | DATE | '2025-01-01' | Analysis period start |
| period_end | DATE | '2025-03-31' | Analysis period end |
| ntb_lookback_days | INTEGER | 365 | Days to define NTB |
| country_id | INTEGER | 840 | Country ID (default: 840 for USA) |

## Expected Output
Single result table with reach, conversion, and basket metrics by tactic combination:

| Column | Description |
|---|---|
| tactic_combination | e.g., 'SP Only', 'SP + Display', 'Display Only' |
| unique_users_reached | Count of unique users exposed to this combination |
| pct_increase_vs_sp_only | % more users vs SP-only baseline |
| pct_of_daily_category_buyers | % of category's daily active buyers reached |
| conversion_rate_index | Conversion rate indexed to best performer = 100 |
| avg_basket_value | Average order value for converting users |
| avg_units_per_order | Average units purchased per order |
| pct_ntb | % of converting users that are new-to-brand |

## Visual Types
- **Primary**: Stacked bar chart (ad spend by tactic) + line (% daily category buyers reached) — the "Utz" chart
- **Secondary**: Indexed bar chart showing conversion rate by tactic combination
- **Tertiary**: Bar chart comparing basket size by tactic exposure count
- **NTB breakout**: Stacked bar showing NTB/Existing breakout by tactic

## Slide References
- Deck 2, Slide 5: "Advertiser X increased total ad reach by 122% with Display & Video"
- Deck 2, Slide 7: "Utz reached up to 55% of daily Salty Snacks buyers with SP + Display"
- Deck 2, Slide 12: "Layered ad tactics lead to larger baskets for Ruiz" (SP+Display+Coupon had 3+ units/order)
- Deck 2, Slide 25: "Pepsico Extends Instacart Ad Reach with Recipes" (13% net new users)
- Deck 2, Slide 30: "25% of Black Rifle's ad attributed sales from NTB + first-time category buyers"
- Deck 1, Slide 31: "Co-Brand Display extended monthly ad reach beyond SP"
- Excel rows 80-86, 119-145: Multiple Display, Shoppable, Video, full-funnel case studies

## Sub-Variants

### 3A: SFB + SP for Innovation/New Product Launch
- **Question**: "How can I drive awareness for a new product?"
- **Modification**: Storefront Banner as primary upper-funnel tactic paired with SP, focused on a single new/innovation product
- **Additional Metrics**: Innovation product sales lift %, category share for new product specifically
- **Slide refs**: "SFB + SP for Innovation Product" deck (81% sales increase)

### 3B: Display Retargeting (Lapsed User Recapture)
- **Question**: "Can I use Display to win back lapsed buyers?"
- **Modification**: Display targeting lapsed brand buyers, tracking days-between-repeat metric
- **Focus**: Retention/win-back rather than acquisition
- **Additional Parameters**: `lapsed_user_definition_days`, `days_between_repeat_metric`
- **Slide refs**: Retargeting Display deck

### 3C: Flyouts (Brand Page Traffic)
- **Question**: "What does Flyout placement contribute?"
- **Modification**: Isolates Flyout as a tactic, measures brand page traffic and downstream conversion
- **Slide refs**: Flyouts case study deck

### 3D: Order Sources Analysis (Non-Search SP Attribution)
- **Question**: "Where are my SP impressions/sales coming from beyond search?"
- **Modification**: Breaks down SP attributed sales by source (search, buy-it-again, recommended, etc.)
- **Key Insight**: Non-search sources are crucial for SP performance
- **Slide refs**: "Order Sources for Packaged Lunchmeat" deck

### 3E: Launch Pilot / Optimized Display Performance
- **Question**: "Does optimizing Display creative/targeting improve results?"
- **Modification**: A/B test of display ad optimization (standard vs optimized)
- **Slide refs**: Launch Pilot & Optimized Performance decks

## Hoped-For Outcome
The data shows that adding upper-funnel tactics to SP dramatically increases unique user reach (often 50-120%+), lifts conversion rates, and grows basket sizes. The seller can position this as: "SP is your foundation, but Display/Video/Shoppable take you to the next level by reaching customers in aisles you can't win with SP alone."
