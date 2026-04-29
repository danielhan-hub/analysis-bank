# Procedure: EARLY_BIRD_SP_INVESTMENT

## Overview
Compares brand performance (CPC, sales, share) for brands that increased vs decreased Sponsored Product investment around a key seasonal transition date (e.g., January 1st). Proves that early/sustained investment drives efficiency gains and competitive advantage.

> **How this differs from Going Dark (02)**: This procedure is **cross-sectional** — it compares *many brands in a category* at one point in time to show that the "increasers" outperformed the "decreasers." Going Dark (02) is **longitudinal** — it tracks *one brand over time* through a live → dark → recovery cycle. Use this procedure when you want to show a category-wide pattern. Use 02 when you want to tell a specific brand's story.

## Question Themes
- "Should my client keep their SP campaigns live through the holidays/new year?"
- "What happens when brands go dark on SP at the start of Q1?"
- "Can you show me evidence that early SP investment drives category share?"
- "My partner wants to pause SP for January — what data can we show them?"

## Methodology
1. Define a key transition date (e.g., Jan 1) and pre/post windows (e.g., 30 days each)
2. For a given category, identify all advertisers with SP spend above a threshold
3. Classify advertisers as "Increased Spend" (spend_percent_diff > +threshold) or "Decreased Spend" (spend_percent_diff < -threshold)
4. For each group, calculate: CPC % change, Total Sales % change, Category Share change
5. Present side-by-side comparison of a representative "Increased" brand vs "Decreased" brand

## Data Requirements
- Advertiser-level daily/weekly SP spend
- Advertiser-level CPC (cost per click) by period
- Advertiser-level total sales by period
- Category-level total sales (for share calculation)
- Category share by advertiser by period

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| category_name | VARCHAR | 'GROCERY - BEVERAGES' | Target category |
| key_date | DATE | '2023-01-01' | The transition date |
| pre_period_days | INT | 30 | Baseline window before key_date |
| post_period_days | INT | 30 | Comparison window after key_date |
| min_spend_threshold | DECIMAL | 100000 | Min total ad spend to qualify |
| min_advertiser_count | INT | 5 | Min advertisers in category |
| spend_change_threshold | DECIMAL | 0.08 | % change to classify increase/decrease |

## Expected Output
| Column | Description |
|---|---|
| brand_name | Advertiser name |
| category | Category name |
| group_label | 'Increased Spend' or 'Decreased Spend' |
| spend_pre | Total SP spend in pre-period |
| spend_post | Total SP spend in post-period |
| spend_percent_diff | % change in spend |
| cpc_percent_diff | % change in CPC |
| sales_percent_diff | % change in total sales |
| share_pre | Category share in pre-period |
| share_post | Category share in post-period |

## Visual Types
- **Primary**: Side-by-side bar chart comparing "Increased Spend" brand vs "Decreased Spend" brand on CPC change, Sales change, Share change
- **Secondary**: Category-level summary table showing all qualifying brands

## Slide References
- **Deck 3 (Earlybird Case Studies)**: All 136 slides — this is THE procedure for that entire deck
- Each category has a "Gain a Competitive Advantage" slide (increased spend) and a "Going dark? Don't miss out!" slide (decreased spend)
- Categories: Beverages, Spirits, Wine, Beer, Frozen Foods, Cereal, Snacks, Cheese, Candy, Dairy, Meat, etc. (35+ categories)
- **Excel "Early Birds" sheet**: 47 rows with pre-computed metrics

## Sub-Variants (May Require Separate Parameter Sets)

### 1A: Optimized Bidding (OB) A/B Comparison
- **Question**: "Does Optimized Bidding outperform Manual Bidding?"
- **Modification**: Instead of pre/post period comparison, compare Manual vs OB campaigns running on same budget for same period
- **Additional Parameters**: `bidding_strategy_a` (e.g., 'MANUAL'), `bidding_strategy_b` (e.g., 'OPTIMIZED')
- **Output adds**: ROAS comparison, share gain comparison, efficiency metrics by strategy
- **Slide refs**: Frozen Meals OB study, Shelf Stable Meat OB study

### 1B: Broad Match Expansion A/B Test
- **Question**: "Does Broad Match drive more NTB sales than Exact Match?"
- **Modification**: Compare Broad Match vs Exact Match SP campaigns, focus on NTB sales expansion and scale
- **Additional Parameters**: `match_type_a` (e.g., 'EXACT'), `match_type_b` (e.g., 'BROAD')
- **Output adds**: NTB sales %, impressions scale, new keyword reach
- **Slide refs**: 4 Broad Match case studies (Prepared Foods, Desserts, Sugar & Sweeteners, multi-category CPG)

### 1C: SP Paid Impression Share → Category Share Correlation
- **Question**: "How does my SP visibility correlate with category share movement?"
- **Modification**: Time-series correlation between SP Paid Impression Share and Category Share during key seasons
- **Additional Parameters**: `season_type` (e.g., 'new_user_acquisition')
- **Slide refs**: "SP Contributing to Share Gain & Loss" deck

## Hoped-For Outcome
The data shows that increased-spend brands see negative CPC change (cheaper clicks) alongside positive sales growth, while decreased-spend brands see the opposite. This gives the seller ammunition to tell the partner: "Don't pause your SP campaigns — brands that stay live gain an efficiency advantage while growing sales."
