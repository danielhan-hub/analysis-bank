USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;
USE WAREHOUSE DEVELOPER_XL_WH;

----------------------------------------------------------------------
-- a_20260506_85b279 — a_20260506_85b279
--
-- Among repeat category buyers in the period, classify each user by
-- their share of category orders that contained the input brand:
--   • 0%        → Competitor Loyalist          (NTB opportunity pool)
--   • <=40%     → Competitor-Biased Switcher
--   • <60%      → Switcher, No Brand Bias
--   • <100%     → Brand-Biased Switcher
--   • 100%      → Brand Loyalist
-- Restricted to partners (retailers) where the brand had any sales in
-- the period, so non-availability stores don't dilute the denominator.
--
-- Source: instadata.etl.agg_ma_order_item_daily_v2 (3Y+, fast, entity
--   hierarchy pre-joined; carries `country_id` natively — NO need to
--   join `dim_warehouse` for country scoping). The original analysis
--   joined `dim_warehouse` on `partner_id` raw, which silently
--   multiplied fact rows (many warehouses per partner_id). This
--   procedure scopes country directly off v2.country_id; aggregates
--   are now correct.
--
-- INPUTS:
--   v_brand_ids            VARCHAR  — comma-separated entity_brand_id list
--                                     (look up via instadata.etl.ads_taxonomy_products_extd)
--   v_category_ids         VARCHAR  — comma-separated delivered_entity_category_id list
--   v_start_date           DATE     — analysis window start (inclusive)
--   v_end_date             DATE     — analysis window end   (inclusive)
--   v_country_id           NUMBER   — 840 (US, default) or 124 (CA)
--   v_min_category_orders  NUMBER   — minimum category orders for inclusion
--                                     (default 2 = "repeat buyer")
--
-- SAMPLE CALL:
-- CALL SANDBOX_DB.DANIELHAN.a_20260506_85b279(
--     '564770', '598,869', '2026-01-01'::DATE, '2026-03-31'::DATE, 840, 2);
----------------------------------------------------------------------

CREATE OR REPLACE PROCEDURE SANDBOX_DB.DANIELHAN.a_20260506_85b279(
    v_brand_ids            VARCHAR,
    v_category_ids         VARCHAR,
    v_start_date           DATE,
    v_end_date             DATE,
    v_country_id           NUMBER  DEFAULT 840,
    v_min_category_orders  NUMBER  DEFAULT 2
)
RETURNS TABLE(
    cohort                   VARCHAR,
    user_count               NUMBER,
    total_user_count         NUMBER,
    percent_category_users   FLOAT,
    sum_total_category_sales FLOAT
)
LANGUAGE SQL
AS $$
DECLARE
    res RESULTSET DEFAULT (
        WITH
        ----------------------------------------------------------------
        -- 0) Parse comma-separated input strings into row sets.
        ----------------------------------------------------------------
        brand_id_list AS (
            SELECT TRIM(value::VARCHAR) AS entity_brand_id
            FROM TABLE(FLATTEN(INPUT => SPLIT(:v_brand_ids, ',')))
        ),
        category_id_list AS (
            SELECT TRIM(value::VARCHAR) AS entity_category_id
            FROM TABLE(FLATTEN(INPUT => SPLIT(:v_category_ids, ',')))
        ),

        ----------------------------------------------------------------
        -- 1) Partners where the brand had any sales in the input
        --    category(ies) during the period — proxy for "available
        --    stores". v2.country_id used directly (no dim_warehouse
        --    join — that would multiply rows).
        ----------------------------------------------------------------
        partners_available AS (
            SELECT
                v2.partner_id,
                SUM(v2.final_charge_amt_usd) AS sum_brand_sales
            FROM instadata.etl.agg_ma_order_item_daily_v2 v2
            WHERE 1 = 1
              AND v2.delivered_date_pt BETWEEN :v_start_date AND :v_end_date
              AND v2.country_id = :v_country_id
              AND v2.delivered_entity_brand_id::VARCHAR
                  IN (SELECT entity_brand_id FROM brand_id_list)
              AND v2.delivered_entity_category_id::VARCHAR
                  IN (SELECT entity_category_id FROM category_id_list)
            GROUP BY v2.partner_id
            HAVING SUM(v2.final_charge_amt_usd) > 0
        ),

        ----------------------------------------------------------------
        -- 2) For each user, count category orders in the period and
        --    how many contained the input brand.
        ----------------------------------------------------------------
        category_orders AS (
            SELECT
                v2.user_id,
                COUNT(DISTINCT CASE
                    WHEN v2.delivered_entity_brand_id::VARCHAR
                         IN (SELECT entity_brand_id FROM brand_id_list)
                    THEN v2.order_id END
                )                                              AS brand_orders,
                COUNT(DISTINCT v2.order_id)                    AS total_orders,
                SUM(v2.final_charge_amt_usd)                   AS total_category_sales,
                DIV0(brand_orders, total_orders)               AS perc_orders
            FROM instadata.etl.agg_ma_order_item_daily_v2 v2
            WHERE 1 = 1
              AND v2.partner_id IN (SELECT partner_id FROM partners_available)
              AND v2.country_id = :v_country_id
              AND v2.delivered_date_pt BETWEEN :v_start_date AND :v_end_date
              AND v2.delivered_entity_category_id::VARCHAR
                  IN (SELECT entity_category_id FROM category_id_list)
            GROUP BY v2.user_id
        )

        ----------------------------------------------------------------
        -- 3) Cohort classification (gated on >= v_min_category_orders).
        ----------------------------------------------------------------
        SELECT
            CASE
                WHEN perc_orders = 0    THEN 'Competitor Loyalist'
                WHEN perc_orders <= 0.4 THEN 'Competitor-Biased Switcher'
                WHEN perc_orders < 0.6  THEN 'Switcher, No Brand Bias'
                WHEN perc_orders < 1    THEN 'Brand-Biased Switcher'
                WHEN perc_orders = 1    THEN 'Brand Loyalist'
            END                                                  AS cohort,
            COUNT(DISTINCT user_id)                              AS user_count,
            SUM(COUNT(DISTINCT user_id)) OVER ()                 AS total_user_count,
            DIV0(COUNT(DISTINCT user_id),
                 SUM(COUNT(DISTINCT user_id)) OVER ())::FLOAT    AS percent_category_users,
            SUM(total_category_sales)::FLOAT                     AS sum_total_category_sales
        FROM category_orders
        WHERE total_orders >= :v_min_category_orders
        GROUP BY 1
        ORDER BY 1
    );
BEGIN
    RETURN TABLE(res);
END;
$$;

-- SAMPLE CALL:
-- CALL SANDBOX_DB.DANIELHAN.a_20260506_85b279(
--     '564770', '598,869', '2026-01-01'::DATE, '2026-03-31'::DATE, 840, 2);
