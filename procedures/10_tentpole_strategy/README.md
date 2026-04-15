# Procedure: TENTPOLE_STRATEGY

## Overview
Analyzes brand performance around tentpole/holiday events, comparing multi-tactic vs single-tactic strategies and measuring whether share gains persist after the event.

> **See also: Multi-Tactic Synergy (03)** — If the question is about the *general value of layering tactics* without a specific event, use 03 instead. This procedure (10) is **event-centric** ("how did we perform during Thanksgiving?") while 03 is **tactic-centric** ("does adding Display to SP help?"). Some slide references overlap between these two procedures.

## Question Themes
- "How should I invest during the upcoming holiday?"
- "What happened to brands that went full-funnel during Thanksgiving?"
- "Does my share gain from a tentpole persist afterward?"
- "Should I increase spend before or during the holiday?"
- "What's the value of layering SP + Display + SFB + Coupon for a tentpole?"

## Methodology
1. Define the tentpole event window and pre/post comparison periods based on parameters
2. Aggregate weekly brand performance metrics: category share, total ad spend by tactic, and paid impression share
3. Calculate new customer acquisition (NTB + NTC) weekly
4. Classify each week into pre-period, tentpole, or post-period based on defined dates
5. Return weekly-level view of the brand's performance arc through the tentpole event

## Data Requirements
- Daily/weekly category share by brand
- Daily/weekly ad spend by tactic (SP, Display, SFB, Coupon, Shoppable)
- Paid impression share by brand
- New customer counts (NTB, NTC) by period
- Tentpole event calendar with defined dates

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| brand_id | VARCHAR | '12345' | Target brand ID |
| category_name | VARCHAR | 'Beverages' | Category context |
| tentpole_name | VARCHAR | 'Labor Day 2025' | Event name (for labeling) |
| tentpole_start | DATE | '2025-08-20' | Event window start |
| tentpole_end | DATE | '2025-09-07' | Event window end |
| pre_period_days | INTEGER | 30 | Baseline before tentpole (days) |
| post_period_days | INTEGER | 30 | Follow-up after tentpole (days) |
| country_id | INTEGER | 840 | Country ID (default: 840 for USA) |

## Expected Output
Weekly-bucketed performance data across pre/tentpole/post periods:

| Column | Description |
|---|---|
| week_start | Start date of each weekly period |
| period_label | 'Pre', 'Tentpole', or 'Post' (indicating which phase the week falls into) |
| category_share | Brand's category share for the week |
| total_ad_spend | Total spend across all tactics for the week |
| sp_spend | Sponsored Product spend for the week |
| display_spend | Display advertising spend for the week |
| other_tactic_spend | Spend on other tactics (SFB, Video, etc.) for the week |
| paid_impression_share | Paid impression share during the week |
| new_customers | Count of new-to-brand and new-to-category customers for the week |

## Visual Types
- **Primary**: Area chart — category share over time (pre → tentpole → post) with ad spend by tactic as stacked bars below
- **Secondary**: Comparison table — multi-tactic vs single-tactic strategy outcomes
- **Tertiary**: New customer acquisition chart during tentpole vs baseline

## Slide References
- Excel rows 52-53: "Steady and Increased Investment Wins Holiday Drive Periods", "Gradual Spend Heavy-Up Strategy during Tentpole"
- Excel row 71: "Brand X's share reached 3-month high when maintaining SP presence"
- Excel row 75: "SP Contributes to Category Share Loss & Recovery Leading up to Key [Events]"
- Excel rows 118-136: Holiday/tentpole multi-tactic case studies (Halloween, Father's Day, Easter, Thanksgiving, Cinco de Mayo)
- Excel row 125: "Savings, SP, and Display leading into Cinco de Mayo led to sales and share increase"
- Excel row 127: "Full-funnel approach led to share increase during Thanksgiving season"

## Sub-Variants

### 10A: Gradual Spend Heavy-Up Strategy (Pacing)
- **Question**: "Should I spike spend on the tentpole day or ramp up gradually?"
- **Modification**: Compares brands that paced spend gradually into the tentpole vs brands that spiked last-minute. Proves steady ramp outperforms spike.
- **Additional Output**: Daily spend pacing curve, performance by pacing strategy
- **Slide refs**: "Gradual Spend Heavy-Up Strategy" deck

### 10B: YoY Steady Investment Comparison
- **Question**: "Did steady investment this year outperform last year?"
- **Modification**: Year-over-year comparison showing that higher and more consistent SP spend during Q4 holiday reaches more users and drives higher sales
- **Additional Parameters**: `comparison_year`, `yoy_metrics` (users reached, total sales)
- **Slide refs**: "Steady and Increased Investment Wins Holiday Drive Periods" deck

## Hoped-For Outcome
Data shows that brands investing in multi-tactic strategies during tentpoles capture significant share gains, acquire more new customers, and retain a portion of those gains post-event. Seller message: "Plan ahead with a layered strategy — brands that went full-funnel during [Holiday] saw X% share increase that persisted for weeks after."
