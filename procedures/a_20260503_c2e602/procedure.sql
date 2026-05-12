USE ROLE IC_ENG_ROLE;
USE WAREHOUSE DEVELOPER_XL_WH;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- PROCEDURE: MULTI_TACTIC_COMBO_NTB_CONVERSION
-- PURPOSE:   For a given brand/account, classify exposed users into a 3-bit
--            combo over {SP, Display, SUAS} and report per-combo:
--              - exposed_user_count
--              - ntb_converters
--              - ntb_conversion_rate
--              - attributed_ntb_sales_usd (linear NTB attribution)
--              - lift_vs_sp_only_baseline  (rate / SP-only rate)
--            Display rolls up Shoppable Display (SD), Shoppable Video (SV),
--            and Banner-family creatives (search_banner / sponsored_recipe /
--            occasion / uvc_banner) — the sub-product split happens at the
--            impression-row level via creative_type, not at campaign_id.
--
-- METHODOLOGY HONORED:
--   - SP and Display campaign_ids are re-resolved from rds.ads_production.campaigns_records
--     using the historical-window overlap pattern (no hardcoded campaign_id IN-lists).
--   - SUAS scope is anchored on stakeholder-supplied promotion_group_ids; the
--     resolved discount_policy_ids drive the impression filter (per the
--     dd_promotions.md gotcha that fact_event_savings_client_*.promotion_id
--     actually holds discount_policy_id).
--   - SP/Display impression scope uses account_uuid; NTB attribution scope uses
--     entity_brand_id + country_id (the NTB attribution table has no
--     account_uuid column per dd_shared.md).
--   - Combos with fewer than v_min_combo_users exposed users are suppressed.
--
-- SAMPLE CALL:
--   CALL SANDBOX_DB.DANIELHAN.a_20260503_c2e602(
--       45,                                        -- v_account_id
--       '717500bd-e82b-4438-a8a0-aa42dec1183b',    -- v_account_uuid
--       564770,                                    -- v_entity_brand_id
--       840,                                       -- v_country_id (US)
--       '2025-07-01'::DATE,                        -- v_window_start
--       '2026-05-03'::DATE,                        -- v_window_end
--       ARRAY_CONSTRUCT(                           -- v_suas_promotion_group_ids
--           '5ae514c3-332d-4668-b654-862d95cf755e',
--           '16998f0f-578d-411b-8021-eb278004f772',
--           '381681f9-6c43-4d5a-81d3-13495fec75de'
--       ),
--       100                                        -- v_min_combo_users (default 100)
--   );

CREATE OR REPLACE PROCEDURE SANDBOX_DB.DANIELHAN.a_20260503_c2e602(
    v_account_id                BIGINT,
    v_account_uuid              VARCHAR,
    v_entity_brand_id           NUMBER,
    v_country_id                NUMBER,
    v_window_start              DATE,
    v_window_end                DATE,
    v_suas_promotion_group_ids  ARRAY,
    v_min_combo_users           NUMBER DEFAULT 100
)
RETURNS TABLE (
    combo_label                 VARCHAR,
    exposed_user_count          NUMBER,
    ntb_converters              NUMBER,
    ntb_conversion_rate         FLOAT,
    attributed_ntb_sales_usd    FLOAT,
    lift_vs_sp_only_baseline    FLOAT
)
LANGUAGE SQL
AS
$$
DECLARE
    res RESULTSET;
BEGIN
    res := (
        WITH
        -- Re-resolve SP campaigns active any time during the window.
        -- Filters: workflow_state='active' + campaign_type='featured_product'
        -- scoped to the input account_id.
        sp_campaigns AS (
            SELECT
                id::NUMBER AS campaign_id
            FROM rds.ads_production.campaigns_records
            WHERE 1 = 1
              AND current_from <= :v_window_end
              AND COALESCE(current_to, CURRENT_DATE()) >= :v_window_start
              AND after:account_id      = :v_account_id
              AND after:workflow_state  = 'active'
              AND after:campaign_type   = 'featured_product'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY current_from DESC) = 1
        ),

        -- Re-resolve Display campaigns active any time during the window.
        -- Sub-product (SD / SV / Banner) split is NOT done here; it happens
        -- at the impression-row level via creative_type below.
        display_campaigns AS (
            SELECT
                id::NUMBER AS campaign_id
            FROM rds.ads_production.campaigns_records
            WHERE 1 = 1
              AND current_from <= :v_window_end
              AND COALESCE(current_to, CURRENT_DATE()) >= :v_window_start
              AND after:account_id    = :v_account_id
              AND after:enabled       = TRUE
              AND after:campaign_type = 'display'
            QUALIFY ROW_NUMBER() OVER (PARTITION BY id ORDER BY current_from DESC) = 1
        ),

        -- Resolve SUAS promotion_ids and discount_policy_ids from the input
        -- promotion_group_ids. Per the dd_promotions.md gotcha, the impression
        -- table column called `promotion_id` actually holds discount_policy_id,
        -- so we carry both and filter the impression table on discount_policy_id.
        suas_promotions AS (
            SELECT
                id                  AS promotion_id,
                discount_policy_id
            FROM instadata.rds_ads.nexus_coupons
            WHERE 1 = 1
              AND campaign_id IN (
                  SELECT VALUE::VARCHAR
                  FROM TABLE(FLATTEN(INPUT => :v_suas_promotion_group_ids))
              )
        ),

        -- TACTIC = SP: distinct users with a viewable SP impression on a
        -- resolved SP campaign during the window. event_date_time_utc is the
        -- partition column (per dd_sp.md) — pad UTC by one day to capture
        -- late-PT events that fall into the next UTC day.
        exp_sp AS (
            SELECT DISTINCT
                user_id::VARCHAR AS user_id
            FROM ADS.ADS_DWH.SP_VIEWABLE_IMPRESSIONS
            WHERE 1 = 1
              AND event_date_time_utc       BETWEEN :v_window_start AND DATEADD(day, 1, :v_window_end)
              AND event_date_time_pt::DATE  BETWEEN :v_window_start AND :v_window_end
              AND event_name    = 'store.viewport_viewable_featured_product'
              AND account_uuid  = :v_account_uuid
              AND campaign_id IN (SELECT campaign_id FROM sp_campaigns)
        ),

        -- TACTIC = SD (Shoppable Display): viewable Display auction impressions
        -- where row-level creative_type ILIKE 'promoted_aisle%'.
        exp_sd AS (
            SELECT DISTINCT
                user_id::VARCHAR AS user_id
            FROM ADS.ADS_DWH.DISPLAY_VIEWABLE_IMPRESSIONS
            WHERE 1 = 1
              AND event_date_time_utc       BETWEEN :v_window_start AND DATEADD(day, 1, :v_window_end)
              AND event_date_time_pt::DATE  BETWEEN :v_window_start AND :v_window_end
              AND auction_type  = 'FIRST_PRICE'
              AND event_name IN ('display.viewport_viewable_creative', 'caper.viewport_viewable_creative')
              AND account_uuid  = :v_account_uuid
              AND campaign_id IN (SELECT campaign_id FROM display_campaigns)
              AND creative_type ILIKE 'promoted_aisle%'
        ),

        -- TACTIC = Banner: viewable Display auction impressions for
        -- banner-family creatives (search_banner / sponsored_recipe(_video) /
        -- occasion / uvc_banner). Same source + base filters as exp_sd; differs
        -- only in creative_type filter.
        exp_banner AS (
            SELECT DISTINCT
                user_id::VARCHAR AS user_id
            FROM ADS.ADS_DWH.DISPLAY_VIEWABLE_IMPRESSIONS
            WHERE 1 = 1
              AND event_date_time_utc       BETWEEN :v_window_start AND DATEADD(day, 1, :v_window_end)
              AND event_date_time_pt::DATE  BETWEEN :v_window_start AND :v_window_end
              AND auction_type  = 'FIRST_PRICE'
              AND event_name IN ('display.viewport_viewable_creative', 'caper.viewport_viewable_creative')
              AND account_uuid  = :v_account_uuid
              AND campaign_id IN (SELECT campaign_id FROM display_campaigns)
              AND (
                      creative_type ILIKE 'search_banner%'
                   OR creative_type ILIKE 'sponsored_recipe%'
                   OR creative_type ILIKE 'occasion%'
                   OR creative_type ILIKE 'uvc_banner%'
                  )
        ),

        -- TACTIC = SV (Shoppable Video): DISPLAY_VIDEO_IMPRESSIONS is already
        -- SV-only, so no creative_type filter required.
        exp_sv AS (
            SELECT DISTINCT
                user_id::VARCHAR AS user_id
            FROM ADS.ADS_DWH.DISPLAY_VIDEO_IMPRESSIONS
            WHERE 1 = 1
              AND event_date_time_utc       BETWEEN :v_window_start AND DATEADD(day, 1, :v_window_end)
              AND event_date_time_pt::DATE  BETWEEN :v_window_start AND :v_window_end
              AND account_uuid  = :v_account_uuid
              AND campaign_id IN (SELECT campaign_id FROM display_campaigns)
        ),

        -- TACTIC = SUAS: distinct users who saw a SUAS promotion impression
        -- for any of the resolved discount_policy_ids.
        exp_suas AS (
            SELECT DISTINCT
                user_id::VARCHAR AS user_id
            FROM instadata.dwh.fact_event_savings_client_viewport_viewable_item
            WHERE 1 = 1
              AND impression_date_time_pt::DATE BETWEEN :v_window_start AND :v_window_end
              AND discount_category_id = 114
              AND promotion_id IN (SELECT discount_policy_id FROM suas_promotions)
        ),

        -- NTB conversion source: linear NTB attribution table, scoped by
        -- entity_brand_id + country_id (the NTB attribution table has no
        -- account_uuid column per dd_shared.md). Order date filtered to window.
        ntb_orders AS (
            SELECT
                user_id::VARCHAR AS user_id,
                order_id,
                attributed_sales_nanos_usd
            FROM ads.ads_dwh.new_to_brand_multi_touch_linear_ads_attributions
            WHERE 1 = 1
              AND entity_brand_id = :v_entity_brand_id
              AND country_id      = :v_country_id
              AND order_item_created_date_time_pt::DATE BETWEEN :v_window_start AND :v_window_end
        ),

        -- Per-user NTB roll-up: collapse NTB attribution rows to one row per
        -- user with a binary converter flag and total NTB-attributed sales (USD).
        ntb_by_user AS (
            SELECT
                user_id,
                1                                       AS is_ntb_converter,
                SUM(attributed_sales_nanos_usd) / 1e9   AS attributed_ntb_sales_usd
            FROM ntb_orders
            GROUP BY user_id
        ),

        -- Stack the 5 exposure CTEs and collapse to one row per user with a
        -- 3-bit flag. SD / SV / Banner are unified under 'Display'.
        all_exposures AS (
            SELECT user_id, 'SP'      AS tactic FROM exp_sp
            UNION ALL
            SELECT user_id, 'Display' AS tactic FROM exp_sd
            UNION ALL
            SELECT user_id, 'Display' AS tactic FROM exp_sv
            UNION ALL
            SELECT user_id, 'Display' AS tactic FROM exp_banner
            UNION ALL
            SELECT user_id, 'SUAS'    AS tactic FROM exp_suas
        ),
        user_flags AS (
            SELECT
                user_id,
                MAX(IFF(tactic = 'SP',      1, 0)) AS f_sp,
                MAX(IFF(tactic = 'Display', 1, 0)) AS f_display,
                MAX(IFF(tactic = 'SUAS',    1, 0)) AS f_suas
            FROM all_exposures
            GROUP BY user_id
        ),

        -- Build a human-readable combo_label by '+'-joining tactic names
        -- whose flag=1. Tactic order is fixed (SP, Display, SUAS) so the same
        -- combination always renders identically (e.g. 'SP+Display+SUAS').
        user_combo AS (
            SELECT
                uf.user_id,
                ARRAY_TO_STRING(
                    ARRAY_COMPACT(ARRAY_CONSTRUCT(
                        IFF(uf.f_sp      = 1, 'SP',      NULL),
                        IFF(uf.f_display = 1, 'Display', NULL),
                        IFF(uf.f_suas    = 1, 'SUAS',    NULL)
                    )),
                    '+'
                ) AS combo_label
            FROM user_flags uf
        ),

        -- Per-user join to NTB conversion. LEFT JOIN so non-converters carry
        -- zero, preserving the full exposed-user denominator for each combo.
        user_combo_ntb AS (
            SELECT
                uc.user_id,
                uc.combo_label,
                COALESCE(nbu.is_ntb_converter,         0) AS is_ntb_converter,
                COALESCE(nbu.attributed_ntb_sales_usd, 0) AS attributed_ntb_sales_usd
            FROM user_combo uc
            LEFT JOIN ntb_by_user nbu
                ON nbu.user_id = uc.user_id
        ),

        -- Combo-level aggregation with small-cell suppression
        -- (< v_min_combo_users exposed users).
        combo_metrics AS (
            SELECT
                combo_label,
                COUNT(DISTINCT user_id)                                AS exposed_user_count,
                SUM(is_ntb_converter)                                  AS ntb_converters,
                DIV0(SUM(is_ntb_converter), COUNT(DISTINCT user_id))   AS ntb_conversion_rate,
                SUM(attributed_ntb_sales_usd)                          AS attributed_ntb_sales_usd
            FROM user_combo_ntb
            GROUP BY combo_label
            HAVING COUNT(DISTINCT user_id) >= :v_min_combo_users
        ),

        -- SP-only baseline rate for the lift index. If 'SP' is suppressed or
        -- absent, the baseline is NULL and DIV0 returns 0 for lift.
        sp_only_rate AS (
            SELECT
                ntb_conversion_rate AS sp_only_ntb_conversion_rate
            FROM combo_metrics
            WHERE 1 = 1
              AND combo_label = 'SP'
        )

        SELECT
            cm.combo_label::VARCHAR                            AS combo_label,
            cm.exposed_user_count::NUMBER                      AS exposed_user_count,
            cm.ntb_converters::NUMBER                          AS ntb_converters,
            cm.ntb_conversion_rate::FLOAT                      AS ntb_conversion_rate,
            cm.attributed_ntb_sales_usd::FLOAT                 AS attributed_ntb_sales_usd,
            DIV0(
                cm.ntb_conversion_rate,
                (SELECT sp_only_ntb_conversion_rate FROM sp_only_rate)
            )::FLOAT                                           AS lift_vs_sp_only_baseline
        FROM combo_metrics cm
        ORDER BY cm.ntb_conversion_rate DESC
    );

    RETURN TABLE(res);
END;
$$;
