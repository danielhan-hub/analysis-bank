# multi_tactic_combo_ntb_conversion

## Overview

Per-combo NTB (new-to-brand) conversion table for a given brand. For an
input account / brand / window, the procedure classifies every exposed
user into a 3-bit combination over **{SP, Display, SUAS}** and reports —
for each observed combo — exposed users, NTB converters, NTB conversion
rate, attributed NTB sales (USD), and lift vs the SP-only baseline.

**Display** rolls up Shoppable Display (SD), Shoppable Video (SV), and
Banner-family creatives (search_banner / sponsored_recipe / occasion /
uvc_banner). The sub-product split happens at the **impression-row
level via `creative_type`**, not by partitioning the resolved Display
campaign list — so a single Display campaign that runs both SD and SV
creatives will contribute to both buckets correctly.

**SUAS** is anchored on stakeholder-supplied promotion_group_ids; the
procedure resolves them to discount_policy_ids at query time and filters
the savings impression table on those (per the dd_promotions.md gotcha
that `fact_event_savings_client_*.promotion_id` actually holds the
discount_policy_id).

Combos with fewer than `v_min_combo_users` exposed users are suppressed
via `HAVING`. The default threshold is 100.

## Question Themes

This procedure answers questions like:
- Which tactic combinations drive the highest NTB conversion rate for
  this brand, over this window?
- How much lift does each multi-tactic combo deliver versus SP-only?
- Where does Display + SUAS land relative to single-tactic exposure?
- Which combos generate the most NTB-attributed sales in dollars?
- Is the SP+Display+SUAS triple-overlap actually pulling its weight, or
  is it concentrated in a handful of users?
- Is SUAS-only effectively a non-converter for this brand?

## Methodology

1. **Resolve SP campaigns** active any time during the window from
   `rds.ads_production.campaigns_records` —
   `workflow_state='active'` + `campaign_type='featured_product'` +
   `account_id = v_account_id`, deduped via QUALIFY on `current_from`.
2. **Resolve Display campaigns** the same way —
   `enabled=TRUE` + `campaign_type='display'` + `account_id`. No
   sub-product split here; that happens at the impression-row level.
3. **Resolve SUAS promotion_ids and discount_policy_ids** from the
   input promotion_group_ids via `instadata.rds_ads.nexus_coupons`.
4. **Build five exposure CTEs** (distinct `user_id` per CTE) over the
   window, scoped by `account_uuid`:
   - `exp_sp` — SP_VIEWABLE_IMPRESSIONS, event = featured_product viewport
   - `exp_sd` — DISPLAY_VIEWABLE_IMPRESSIONS, `creative_type ILIKE 'promoted_aisle%'`
   - `exp_banner` — DISPLAY_VIEWABLE_IMPRESSIONS, `creative_type` in banner family
   - `exp_sv` — DISPLAY_VIDEO_IMPRESSIONS (already SV-only)
   - `exp_suas` — `fact_event_savings_client_viewport_viewable_item`,
     `discount_category_id = 114`, filtered by resolved discount_policy_ids
5. **Pull NTB orders** from
   `ads.ads_dwh.new_to_brand_multi_touch_linear_ads_attributions`,
   scoped by `entity_brand_id + country_id` (the table has no
   account_uuid column per dd_shared.md). Roll up to one row per user
   with a binary converter flag and total NTB-attributed sales (USD).
6. **Stack and flag** — UNION ALL the five exposure CTEs, label each
   row with one of {SP, Display, SUAS} (SD/SV/Banner all map to
   Display), then collapse to one row per user with three bit flags.
7. **Build `combo_label`** by `+`-joining the active tactic names in
   fixed order (SP, Display, SUAS) so the same combination always
   renders identically (`SP+Display`, `SP+Display+SUAS`, etc.).
8. **LEFT JOIN to NTB roll-up** so non-converters carry zero —
   preserving the full exposed-user denominator for each combo.
9. **Aggregate** at combo grain with small-cell suppression
   (`HAVING COUNT(DISTINCT user_id) >= :v_min_combo_users`).
10. **Compute lift** for each combo as
    `combo_rate / sp_only_rate` via DIV0 (returns 0 if SP is suppressed
    or absent, so the column never NULLs).

## Data Requirements

| Source | Used for |
|---|---|
| `rds.ads_production.campaigns_records` | Re-resolve SP and Display campaign_ids active during the window |
| `instadata.rds_ads.nexus_coupons` | Resolve SUAS promotion_group_ids → discount_policy_ids |
| `ADS.ADS_DWH.SP_VIEWABLE_IMPRESSIONS` | SP exposure (distinct users) |
| `ADS.ADS_DWH.DISPLAY_VIEWABLE_IMPRESSIONS` | SD + Banner exposure (split by creative_type) |
| `ADS.ADS_DWH.DISPLAY_VIDEO_IMPRESSIONS` | SV exposure (already SV-only) |
| `instadata.dwh.fact_event_savings_client_viewport_viewable_item` | SUAS exposure (filtered by discount_policy_id) |
| `ads.ads_dwh.new_to_brand_multi_touch_linear_ads_attributions` | NTB orders (scoped by entity_brand_id + country_id) |

## Parameters

| Parameter | Type | Example | Description |
|---|---|---|---|
| `v_account_id` | BIGINT | `45` | The Ads account id; drives campaign resolution from `campaigns_records.after:account_id`. |
| `v_account_uuid` | VARCHAR | `'717500bd-e82b-4438-a8a0-aa42dec1183b'` | Used as the SP / Display impression-table scope. Must match the account_id. |
| `v_entity_brand_id` | NUMBER | `564770` | Brand id used for NTB attribution scope (the NTB table has no account_uuid). |
| `v_country_id` | NUMBER | `840` | NTB attribution country scope. US = 840, Canada = 124. |
| `v_window_start` | DATE | `'2025-07-01'::DATE` | Inclusive start of analysis window (PT). |
| `v_window_end` | DATE | `'2026-05-03'::DATE` | Inclusive end of analysis window (PT). UTC partition pad is handled internally via `DATEADD(day, 1, …)`. |
| `v_suas_promotion_group_ids` | ARRAY | `ARRAY_CONSTRUCT('5ae514c3-…','16998f0f-…','381681f9-…')` | SUAS promotion_group_ids (= `nexus_coupons.campaign_id`). Pass an empty array to disable the SUAS dimension entirely. |
| `v_min_combo_users` | NUMBER (default `100`) | `100` | Minimum exposed-user count required to keep a combo in the output (small-cell suppression). |

## Expected Output

| Column | Description |
|---|---|
| `combo_label` | `+`-joined tactic names in fixed order (e.g. `SP`, `SP+Display`, `SP+Display+SUAS`). Up to 7 distinct values. |
| `exposed_user_count` | Distinct users exposed to that exact combination during the window. |
| `ntb_converters` | Distinct users in the combo who had at least one NTB-attributed order during the window. |
| `ntb_conversion_rate` | `ntb_converters / exposed_user_count` (decimal, e.g. 0.0876 = 8.76%). |
| `attributed_ntb_sales_usd` | Sum of `attributed_sales_nanos_usd / 1e9` for NTB orders attributed to users in the combo. |
| `lift_vs_sp_only_baseline` | `ntb_conversion_rate / sp_only_rate`. SP-only itself = 1.0; > 1 means above SP-only baseline. Returns 0 if SP is suppressed. |

Rows are ordered by `ntb_conversion_rate DESC`.

## Visual Types

- **Primary:** Horizontal bar chart of top-N combos by NTB conversion
  rate, focal combo (highest rate) bolded. See `chart.py` →
  `render_chart(...)`.
- **Secondary:** Bar + line combo chart with NTB conversion rate (bars)
  and attributed NTB sales (line), or a Plotly table for exact-number
  comparison.

## Hoped-For Outcome

Stakeholders walk away knowing which **tactic mix** is moving the
NTB-conversion needle for this brand — not just whether SP or Display
"works" individually, but whether the multi-tactic overlaps (SP+SUAS,
SP+Display, the full triple) are pulling enough additional NTB
conversion to justify their cost. The lift index makes the comparison
unit-free (any combo ≥ 1.0 outperforms SP-only on a per-user basis),
which is the form most useful for media-mix conversations.

## Usage

```sql
CALL multi_tactic_combo_ntb_conversion(
    45,                                          -- v_account_id
    '717500bd-e82b-4438-a8a0-aa42dec1183b',      -- v_account_uuid
    564770,                                      -- v_entity_brand_id
    840,                                         -- v_country_id (US)
    '2025-07-01'::DATE,                          -- v_window_start
    '2026-05-03'::DATE,                          -- v_window_end
    ARRAY_CONSTRUCT(                             -- v_suas_promotion_group_ids
        '5ae514c3-332d-4668-b654-862d95cf755e',
        '16998f0f-578d-411b-8021-eb278004f772',
        '381681f9-6c43-4d5a-81d3-13495fec75de'
    ),
    100                                          -- v_min_combo_users
);
```

```python
# Equivalent call from Python via the chart wrapper
from chart import render_chart
fig = render_chart(
    v_account_id=45,
    v_account_uuid='717500bd-e82b-4438-a8a0-aa42dec1183b',
    v_entity_brand_id=564770,
    v_country_id=840,
    v_window_start='2025-07-01',
    v_window_end='2026-05-03',
    v_suas_promotion_group_ids=[
        '5ae514c3-332d-4668-b654-862d95cf755e',
        '16998f0f-578d-411b-8021-eb278004f772',
        '381681f9-6c43-4d5a-81d3-13495fec75de',
    ],
)
```
