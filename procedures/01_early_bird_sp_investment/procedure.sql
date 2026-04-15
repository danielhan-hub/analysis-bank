USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- PROCEDURE: EARLY_BIRD_SP_INVESTMENT
-- PURPOSE: Analyze brand performance across categories comparing pre/post-investment periods, measuring spend change impact on sales, CPC, and market share.
-- SAMPLE CALL: CALL EARLY_BIRD_SP_INVESTMENT('SOFT DRINKS', '2025-12-01', 30, 30, 100.00, 0.10);

CREATE OR REPLACE PROCEDURE EARLY_BIRD_SP_INVESTMENT(
    category_name VARCHAR,
    key_date DATE,
    pre_period_days INTEGER,
    post_period_days INTEGER,
    min_spend_threshold DECIMAL(18, 2),
    spend_change_threshold DECIMAL(5, 2)
)
RETURNS TABLE (
    brand_name VARCHAR,
    category VARCHAR,
    group_label VARCHAR,
    spend_pre DECIMAL(18, 2),
    spend_post DECIMAL(18, 2),
    spend_percent_diff DECIMAL(10, 2),
    cpc_percent_diff DECIMAL(10, 2),
    sales_percent_diff DECIMAL(10, 2),
    share_pre DECIMAL(10, 4),
    share_post DECIMAL(10, 4)
)
LANGUAGE SQL
AS
$$
    DECLARE
        v_category_name VARCHAR := category_name;
        v_key_date DATE := key_date;
        v_pre_period_days INTEGER := pre_period_days;
        v_post_period_days INTEGER := post_period_days;
        v_min_spend_threshold DECIMAL(18, 2) := min_spend_threshold;
        v_spend_change_threshold DECIMAL(5, 2) := spend_change_threshold;
        v_pre_start_date DATE;
        v_pre_end_date DATE;
        v_post_start_date DATE;
        v_post_end_date DATE;
        res RESULTSET;
    BEGIN
        v_pre_start_date := DATEADD(day, -v_pre_period_days, v_key_date);
        v_pre_end_date := DATEADD(day, -1, v_key_date);
        v_post_start_date := v_key_date;
        v_post_end_date := DATEADD(day, v_post_period_days - 1, v_key_date);

        res := (
            -- Aggregate daily SP metrics by campaign
            WITH sp_daily_agg AS (
                SELECT
                    afd.event_date_pt,
                    afd.campaign_id::VARCHAR AS campaign_id,
                    COALESCE(SUM(afd.billable_spend_usd), 0) AS daily_spend,
                    COALESCE(SUM(afd.clicks), 0) AS daily_clicks,
                    COALESCE(SUM(afd.attributed_sales_usd), 0) AS daily_sales
                FROM ads.ads_dwh.agg_featured_product_daily afd
                WHERE 1 = 1
                    AND afd.event_date_pt >= :v_pre_start_date
                    AND afd.event_date_pt <= :v_post_end_date
                GROUP BY afd.event_date_pt, afd.campaign_id::VARCHAR
            ),
            -- Map campaigns to L1 entities (advertisers)
            campaign_entity_map AS (
                SELECT DISTINCT
                    c.id::VARCHAR AS campaign_id,
                    dmm.entity_level_1_id_comprehensive
                FROM rds.ads_production.campaigns c
                INNER JOIN instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings dmm
                    ON c.account_id = dmm.account_id
                WHERE 1 = 1
                    AND c.campaign_type = 'featured_product'
                    AND c.exchange_name IS NULL
                    AND dmm.entity_level_1_id_comprehensive IS NOT NULL
            ),
            -- Pre-period SP metrics by campaign
            pre_period_perf AS (
                SELECT
                    sp.campaign_id,
                    SUM(sp.daily_spend) AS spend_pre,
                    SUM(sp.daily_clicks) AS clicks_pre,
                    SUM(sp.daily_sales) AS sales_pre
                FROM sp_daily_agg sp
                WHERE 1 = 1
                    AND sp.event_date_pt >= :v_pre_start_date
                    AND sp.event_date_pt <= :v_pre_end_date
                GROUP BY sp.campaign_id
            ),
            -- Post-period SP metrics by campaign
            post_period_perf AS (
                SELECT
                    sp.campaign_id,
                    SUM(sp.daily_spend) AS spend_post,
                    SUM(sp.daily_clicks) AS clicks_post,
                    SUM(sp.daily_sales) AS sales_post
                FROM sp_daily_agg sp
                WHERE 1 = 1
                    AND sp.event_date_pt >= :v_post_start_date
                    AND sp.event_date_pt <= :v_post_end_date
                GROUP BY sp.campaign_id
            ),
            -- Aggregate SP metrics to L1 entity level and classify by spend change
            entity_sp_perf AS (
                SELECT
                    cem.entity_level_1_id_comprehensive AS entity_id,
                    SUM(COALESCE(ppp.spend_pre, 0)) AS spend_pre,
                    SUM(COALESCE(post.spend_post, 0)) AS spend_post,
                    SUM(COALESCE(ppp.clicks_pre, 0)) AS clicks_pre,
                    SUM(COALESCE(post.clicks_post, 0)) AS clicks_post,
                    SUM(COALESCE(ppp.sales_pre, 0)) AS sales_pre,
                    SUM(COALESCE(post.sales_post, 0)) AS sales_post
                FROM campaign_entity_map cem
                LEFT JOIN pre_period_perf ppp ON cem.campaign_id = ppp.campaign_id
                LEFT JOIN post_period_perf post ON cem.campaign_id = post.campaign_id
                WHERE 1 = 1
                    AND (COALESCE(ppp.spend_pre, 0) > 0 OR COALESCE(post.spend_post, 0) > 0)
                GROUP BY cem.entity_level_1_id_comprehensive
                HAVING SUM(COALESCE(ppp.spend_pre, 0)) >= :v_min_spend_threshold
                    OR SUM(COALESCE(post.spend_post, 0)) >= :v_min_spend_threshold
            ),
            -- Classify entities by spend direction
            entity_classified AS (
                SELECT
                    esp.*,
                    CASE
                        WHEN esp.spend_pre = 0 AND esp.spend_post > 0 THEN 100.00
                        WHEN esp.spend_pre = 0 THEN 0.00
                        ELSE DIV0((esp.spend_post - esp.spend_pre) * 100, esp.spend_pre)
                    END AS spend_pct_diff,
                    CASE
                        WHEN (esp.spend_pre = 0 AND esp.spend_post > 0)
                            OR (esp.spend_pre > 0 AND DIV0((esp.spend_post - esp.spend_pre) * 100, esp.spend_pre) >= :v_spend_change_threshold * 100)
                        THEN 'Increased Spend'
                        WHEN esp.spend_pre > 0
                            AND DIV0((esp.spend_post - esp.spend_pre) * 100, esp.spend_pre) <= -(:v_spend_change_threshold * 100)
                        THEN 'Decreased Spend'
                        ELSE NULL
                    END AS spend_group
                FROM entity_sp_perf esp
            ),
            -- L1 entity category sales (pre-period) from order data — uses delivered_entity_level_1_id directly
            entity_cat_sales_pre AS (
                SELECT
                    aoi.delivered_entity_level_1_id AS entity_id,
                    MAX(aoi.delivered_entity_level_1) AS entity_name,
                    SUM(aoi.final_charge_amt_usd) AS cat_sales_pre
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_pre_start_date
                    AND aoi.delivered_date_pt <= :v_pre_end_date
                    AND aoi.delivered_entity_category = :v_category_name
                    AND aoi.country_id = 840
                GROUP BY aoi.delivered_entity_level_1_id
            ),
            -- L1 entity category sales (post-period)
            entity_cat_sales_post AS (
                SELECT
                    aoi.delivered_entity_level_1_id AS entity_id,
                    SUM(aoi.final_charge_amt_usd) AS cat_sales_post
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_post_start_date
                    AND aoi.delivered_date_pt <= :v_post_end_date
                    AND aoi.delivered_entity_category = :v_category_name
                    AND aoi.country_id = 840
                GROUP BY aoi.delivered_entity_level_1_id
            ),
            -- Total category sales for share denominator
            category_total_pre AS (
                SELECT SUM(aoi.final_charge_amt_usd) AS total_cat_sales_pre
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_pre_start_date
                    AND aoi.delivered_date_pt <= :v_pre_end_date
                    AND aoi.delivered_entity_category = :v_category_name
                    AND aoi.country_id = 840
            ),
            category_total_post AS (
                SELECT SUM(aoi.final_charge_amt_usd) AS total_cat_sales_post
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_post_start_date
                    AND aoi.delivered_date_pt <= :v_post_end_date
                    AND aoi.delivered_entity_category = :v_category_name
                    AND aoi.country_id = 840
            )
            SELECT
                ecsp.entity_name::VARCHAR AS brand_name,
                :v_category_name::VARCHAR AS category,
                ec.spend_group::VARCHAR AS group_label,
                ec.spend_pre::DECIMAL(18,2) AS spend_pre,
                ec.spend_post::DECIMAL(18,2) AS spend_post,
                ec.spend_pct_diff::DECIMAL(10,2) AS spend_percent_diff,
                DIV0(
                    (DIV0(ec.spend_post, ec.clicks_post) - DIV0(ec.spend_pre, ec.clicks_pre)) * 100,
                    DIV0(ec.spend_pre, ec.clicks_pre)
                )::DECIMAL(10,2) AS cpc_percent_diff,
                DIV0(
                    (ec.sales_post - ec.sales_pre) * 100,
                    ec.sales_pre
                )::DECIMAL(10,2) AS sales_percent_diff,
                DIV0(ecsp.cat_sales_pre, ctp.total_cat_sales_pre)::DECIMAL(10,4) AS share_pre,
                DIV0(COALESCE(ecsp_post.cat_sales_post, 0), ctp_post.total_cat_sales_post)::DECIMAL(10,4) AS share_post
            FROM entity_classified ec
            INNER JOIN entity_cat_sales_pre ecsp
                ON ec.entity_id = ecsp.entity_id
            LEFT JOIN entity_cat_sales_post ecsp_post
                ON ec.entity_id = ecsp_post.entity_id
            CROSS JOIN category_total_pre ctp
            CROSS JOIN category_total_post ctp_post
            WHERE 1 = 1
                AND ec.spend_group IS NOT NULL
            ORDER BY ec.spend_post DESC
        );
        RETURN TABLE(res);
    END;
$$;
