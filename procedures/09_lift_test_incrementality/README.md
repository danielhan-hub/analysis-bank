# Procedure: LIFT_TEST_INCREMENTALITY

## Overview
Calculates incremental sales lift and iROAS from controlled lift tests (test vs control groups), with optional breakouts by customer segment.

## Question Themes
- "Are my ads actually driving incremental sales?"
- "What's my iROAS?"
- "Is the lift statistically significant?"
- "Do my ads drive more lift among NTB vs existing customers?"
- "What's the incremental sales lift from my SP campaigns?"

## Methodology
1. Retrieve test/control group assignments and experiment metadata
2. Calculate total sales in test group vs control group during experiment window
3. Compute incremental sales = test_sales - control_sales (group-size adjusted)
4. Calculate iROAS = incremental_sales / total_ad_spend during experiment
5. Calculate confidence interval bounds using z-score and provided confidence level
6. Return overall lift metrics with statistical bounds (no segment breakouts)

## Data Requirements
- Experiment metadata (ID, start date, end date, tactic tested, brand, category)
- Test/Control group user assignments
- Sales by user by group during measurement window
- Ad spend during experiment period
- Ad exposure data (for ghost ads methodology)
- User segment classification (loyal, NTB, lapsed)

## Parameters
| Parameter | Type | Example | Description |
|---|---|---|---|
| experiment_id | VARCHAR | 'exp_12345' | Experiment identifier |
| brand_id | INTEGER | 67890 | Brand ID tested |
| test_start | DATE | '2025-09-01' | Experiment start date |
| test_end | DATE | '2025-09-30' | Experiment end date |
| confidence_level | DECIMAL(10, 2) | 0.95 | Statistical confidence level (default: 0.95 for 95%) |
| z_score | DECIMAL(10, 4) | 1.96 | Z-score for confidence interval (default: 1.96 for 95% CI) |
| country_id | INTEGER | 840 | Country ID (default: 840 for USA) |

## Expected Output
Single result table with overall lift metrics and 95% confidence intervals:

| Column | Description |
|---|---|
| test_group_size | Number of users in test group |
| control_group_size | Number of users in control group |
| test_sales | Total sales from test group |
| control_sales | Total sales from control group |
| incremental_sales | test_sales - control_sales (adjusted for group size differences) |
| lift_pct | Percentage lift from test vs control |
| ad_spend | Total ad spend during experiment period |
| iroas | Incremental Return on Ad Spend (incremental_sales / ad_spend) |
| ci_lower | Lower bound of 95% confidence interval for lift |
| ci_upper | Upper bound of 95% confidence interval for lift |

## Visual Types
- **Primary**: Bar chart — test vs control total sales with lift % callout
- **Secondary**: Summary card: Lift %, iROAS, confidence interval
- **Tertiary**: Segment breakout table or bar chart

## Slide References
- Excel rows 3-4, 6, 23-37: 18+ lift test case studies across categories (Wine, Alcohol, Petcare, Prepared Foods, Frozen Meals, Snacks, Cheese, Dairy, Beverages, Fresh Fruit)
- Excel row 2: "Loyalty Pilot Case Study" — Total Sales Lift, Loyal User Sales Lift, Loyal User Order Lift
- Note: Lift tests often use Instacart's BFA (Brand Fit Analysis) framework for automated experimentation

## Sub-Variants

### 9A: GYS (Grow Your Share) — Spend Increase A/B Test
- **Question**: "How much does increased SP investment grow my business?"
- **Modification**: Instead of on/off test, test group gets increased SP budget vs control at baseline budget. Measures incremental sales and iROAS from the *increase*.
- **Key Definitions**: Incremental Total Sales = difference in sales between test and control; Incremental ROAS = difference in sales / difference in spend
- **Typical Setup**: 4-week test in a quarter
- **Slide refs**: GYS Coffee Partner, GYS Cookies Partner

### 9B: BFA (Brand Fit Analysis) — Automated Lift Testing
- **Question**: "Can we run lift tests at scale across many brands?"
- **Modification**: Same underlying methodology but executed via automated BFA framework. Results are batch-generated.
- **Note**: BFA is an Instacart internal tool — procedure may need to interface with BFA outputs rather than raw data

## Hoped-For Outcome
The test shows a statistically significant positive lift, proving ads drive incremental sales above organic. Seller message: "Your SP campaigns drove X% incremental sales lift with an iROAS of Y — this proves your ads are working and justifies continued or increased investment."
