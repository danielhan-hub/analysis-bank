USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- PROCEDURE: GOING_DARK_IMPACT
-- PURPOSE: Measure brand impact during and after a pause in SP advertising, tracking category share, sales, lapse rate, and impression share trends.
-- SAMPLE CALL: CALL GOING_DARK_IMPACT(12345, 'SOFT DRINKS', '2025-07-01', '2025-12-31', '2025-09-01', '2025-10-31', 30, 840);

CREATE OR REPLACE PROCEDURE GOING_DARK_IMPACT(
    brand_id INT,
    category_name VARCHAR,
    analysis_start_date DATE,
    analysis_end_date DATE,
    dark_period_start DATE,
    dark_period_end DATE,
    lapsed_threshold_days INT,
    country_id INT DEFAULT 840
)
RETURNS TABLE(
    period_label VARCHAR,
    week_start DATE,
    category_share FLOAT,
    attributed_sales FLOAT,
    total_sales FLOAT,
    sp_spend FLOAT,
    lapsed_user_rate FLOAT,
    impression_share FLOAT
)
LANGUAGE SQL
AS
$$
    DECLARE
        v_brand_id INT := brand_id;
        v_category_name VARCHAR := category_name;
        v_analysis_start_date DATE := analysis_start_date;
        v_analysis_end_date DATE := analysis_end_date;
        v_dark_period_start DATE := dark_period_start;
        v_dark_period_end DATE := dark_period_end;
        v_lapsed_threshold_days INT := lapsed_threshold_days;
        v_country_id INT := country_id;
        res RESULTSET;
    BEGIN
        res := (
            -- Map brand's SP campaigns via account → entity mapping
            WITH brand_campaigns AS (
                SELECT DISTINCT
                    c.id::VARCHAR AS campaign_id
                FROM rds.ads_production.campaigns c
                INNER JOIN instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings dmm
                    ON c.account_id = dmm.account_id
                WHERE 1 = 1
                    AND c.campaign_type = 'featured_product'
                    AND c.exchange_name IS NULL
                    AND dmm.entity_level_1_id_comprehensive = :v_brand_id
            ),

            -- Weekly SP performance for the brand
            sp_weekly AS (
                SELECT
                    DATE_TRUNC('WEEK', spd.event_date_pt) AS week_start,
                    SUM(spd.billable_spend_usd) AS weekly_spend,
                    SUM(spd.attributed_sales_usd) AS weekly_attributed_sales,
                    SUM(spd.impressions) AS weekly_impressions
                FROM ads.ads_dwh.agg_featured_product_daily spd
                INNER JOIN brand_campaigns bc ON spd.campaign_id::VARCHAR = bc.campaign_id
                WHERE 1 = 1
                    AND spd.event_date_pt >= :v_analysis_start_date
                    AND spd.event_date_pt <= :v_analysis_end_date
                GROUP BY DATE_TRUNC('WEEK', spd.event_date_pt)
            ),

            -- Weekly impression share for the brand's campaigns
            imp_share_weekly AS (
                SELECT
                    DATE_TRUNC('WEEK', tao.event_date_pt) AS week_start,
                    DIV0(SUM(tao.won_viewable_block), SUM(tao.eligible_viewable_block)) AS impression_share
                FROM ads.ads_dwh.total_auction_opportunity tao
                INNER JOIN brand_campaigns bc ON tao.campaign_id::VARCHAR = bc.campaign_id
                WHERE 1 = 1
                    AND tao.event_date_pt >= :v_analysis_start_date
                    AND tao.event_date_pt <= :v_analysis_end_date
                GROUP BY DATE_TRUNC('WEEK', tao.event_date_pt)
            ),

            -- Brand's weekly category sales
            brand_cat_weekly AS (
                SELECT
                    DATE_TRUNC('WEEK', aoi.delivered_date_pt) AS week_start,
                    SUM(aoi.final_charge_amt_usd) AS brand_cat_sales
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_analysis_start_date
                    AND aoi.delivered_date_pt <= :v_analysis_end_date
                    AND aoi.delivered_entity_level_1_id = :v_brand_id
                    AND aoi.delivered_entity_category = :v_category_name
                    AND aoi.country_id = :v_country_id
                GROUP BY DATE_TRUNC('WEEK', aoi.delivered_date_pt)
            ),

            -- Total category sales (all brands) for share denominator
            total_cat_weekly AS (
                SELECT
                    DATE_TRUNC('WEEK', aoi.delivered_date_pt) AS week_start,
                    SUM(aoi.final_charge_amt_usd) AS total_cat_sales
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_analysis_start_date
                    AND aoi.delivered_date_pt <= :v_analysis_end_date
                    AND aoi.delivered_entity_category = :v_category_name
                    AND aoi.country_id = :v_country_id
                GROUP BY DATE_TRUNC('WEEK', aoi.delivered_date_pt)
            ),

            -- Users who purchased from the brand before the dark period (baseline cohort)
            pre_dark_users AS (
                SELECT DISTINCT aoi.user_id
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_analysis_start_date
                    AND aoi.delivered_date_pt < :v_dark_period_start
                    AND aoi.delivered_entity_level_1_id = :v_brand_id
                    AND aoi.country_id = :v_country_id
            ),

            -- Count pre-dark cohort users still active each week (purchased within lapsed_threshold_days before week end)
            weekly_retention AS (
                SELECT
                    DATE_TRUNC('WEEK', aoi.delivered_date_pt) AS week_start,
                    COUNT(DISTINCT CASE
                        WHEN aoi.user_id IN (SELECT user_id FROM pre_dark_users) THEN aoi.user_id
                    END) AS retained_users
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_analysis_start_date
                    AND aoi.delivered_date_pt <= :v_analysis_end_date
                    AND aoi.delivered_entity_level_1_id = :v_brand_id
                    AND aoi.country_id = :v_country_id
                GROUP BY DATE_TRUNC('WEEK', aoi.delivered_date_pt)
            ),

            pre_dark_user_count AS (
                SELECT COUNT(*) AS total_pre_dark_users FROM pre_dark_users
            )

            SELECT
                CASE
                    WHEN bcw.week_start < :v_dark_period_start THEN 'Pre-Dark'
                    WHEN bcw.week_start >= :v_dark_period_start AND bcw.week_start <= :v_dark_period_end THEN 'Dark'
                    ELSE 'Recovery'
                END::VARCHAR AS period_label,
                bcw.week_start::DATE AS week_start,
                DIV0(bcw.brand_cat_sales, tcw.total_cat_sales)::FLOAT AS category_share,
                COALESCE(sw.weekly_attributed_sales, 0)::FLOAT AS attributed_sales,
                bcw.brand_cat_sales::FLOAT AS total_sales,
                COALESCE(sw.weekly_spend, 0)::FLOAT AS sp_spend,
                DIV0(pdc.total_pre_dark_users - COALESCE(wr.retained_users, 0), pdc.total_pre_dark_users)::FLOAT AS lapsed_user_rate,
                COALESCE(isw.impression_share, 0)::FLOAT AS impression_share
            FROM brand_cat_weekly bcw
            LEFT JOIN total_cat_weekly tcw ON bcw.week_start = tcw.week_start
            LEFT JOIN sp_weekly sw ON bcw.week_start = sw.week_start
            LEFT JOIN imp_share_weekly isw ON bcw.week_start = isw.week_start
            LEFT JOIN weekly_retention wr ON bcw.week_start = wr.week_start
            CROSS JOIN pre_dark_user_count pdc
            WHERE 1 = 1
            ORDER BY bcw.week_start ASC
        );
        RETURN TABLE(res);
    END;
$$;
