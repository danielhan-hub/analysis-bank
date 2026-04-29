USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- Procedure: CO_BRANDED_CROSS_SHOP
-- Purpose: Measure co-branded partnership impact via cross-shopping, trial rates, and category expansion
-- Sample CALL: CALL CO_BRANDED_CROSS_SHOP('27028', '27904', '2025-10-01', '2025-10-31', 30, 365, 840)

CREATE OR REPLACE PROCEDURE CO_BRANDED_CROSS_SHOP(
    brand_a_id VARCHAR,
    brand_b_id VARCHAR,
    campaign_start DATE,
    campaign_end DATE,
    pre_period_days INTEGER,
    ntb_lookback_days INTEGER DEFAULT 365,
    country_id INTEGER DEFAULT 840
)
RETURNS TABLE (
    metric_type VARCHAR,
    period VARCHAR,
    total_orders INTEGER,
    orders_with_both INTEGER,
    cross_shop_pct DECIMAL(10, 4),
    cross_shop_pct_change DECIMAL(10, 4),
    brand VARCHAR,
    partner_loyal_customers INTEGER,
    tried_this_brand_during_campaign INTEGER,
    trial_rate DECIMAL(10, 4),
    new_category VARCHAR,
    orders_in_category INTEGER
)
LANGUAGE SQL
AS
$$
    DECLARE
        v_brand_a_id VARCHAR := brand_a_id;
        v_brand_b_id VARCHAR := brand_b_id;
        v_campaign_start DATE := campaign_start;
        v_campaign_end DATE := campaign_end;
        v_pre_days INTEGER := pre_period_days;
        v_ntb_days INTEGER := ntb_lookback_days;
        v_country_id INTEGER := country_id;
        v_pre_start_date DATE;
        v_pre_end_date DATE;
        res RESULTSET;
    BEGIN
        v_pre_start_date := DATEADD(day, -v_pre_days, v_campaign_start);
        v_pre_end_date := DATEADD(day, -1, v_campaign_start);

        res := (
            -- Baseline co-shopping: orders containing items from both brands during pre-campaign period
            WITH pre_period_cross_shop AS (
                SELECT
                    'Pre-Campaign' AS period,
                    COUNT(DISTINCT aoi.order_id::VARCHAR) AS total_orders,
                    COUNT(DISTINCT CASE
                        WHEN a_exists.order_id IS NOT NULL AND b_exists.order_id IS NOT NULL
                        THEN aoi.order_id::VARCHAR
                    END) AS orders_with_both
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                LEFT JOIN (
                    SELECT DISTINCT order_id::VARCHAR AS order_id
                    FROM instadata.etl.agg_ma_order_item_daily_v2
                    WHERE 1 = 1
                        AND delivered_date_pt >= :v_pre_start_date
                        AND delivered_date_pt <= :v_pre_end_date
                        AND delivered_entity_level_1_id::VARCHAR = :v_brand_a_id
                        AND country_id = :v_country_id
                ) a_exists ON aoi.order_id::VARCHAR = a_exists.order_id
                LEFT JOIN (
                    SELECT DISTINCT order_id::VARCHAR AS order_id
                    FROM instadata.etl.agg_ma_order_item_daily_v2
                    WHERE 1 = 1
                        AND delivered_date_pt >= :v_pre_start_date
                        AND delivered_date_pt <= :v_pre_end_date
                        AND delivered_entity_level_1_id::VARCHAR = :v_brand_b_id
                        AND country_id = :v_country_id
                ) b_exists ON aoi.order_id::VARCHAR = b_exists.order_id
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_pre_start_date
                    AND aoi.delivered_date_pt <= :v_pre_end_date
                    AND aoi.country_id = :v_country_id
                    AND (a_exists.order_id IS NOT NULL OR b_exists.order_id IS NOT NULL)
            ),
            -- Campaign-period co-shopping to measure lift from partnership activation
            campaign_period_cross_shop AS (
                SELECT
                    'Campaign' AS period,
                    COUNT(DISTINCT aoi.order_id::VARCHAR) AS total_orders,
                    COUNT(DISTINCT CASE
                        WHEN a_exists.order_id IS NOT NULL AND b_exists.order_id IS NOT NULL
                        THEN aoi.order_id::VARCHAR
                    END) AS orders_with_both
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                LEFT JOIN (
                    SELECT DISTINCT order_id::VARCHAR AS order_id
                    FROM instadata.etl.agg_ma_order_item_daily_v2
                    WHERE 1 = 1
                        AND delivered_date_pt >= :v_campaign_start
                        AND delivered_date_pt <= :v_campaign_end
                        AND delivered_entity_level_1_id::VARCHAR = :v_brand_a_id
                        AND country_id = :v_country_id
                ) a_exists ON aoi.order_id::VARCHAR = a_exists.order_id
                LEFT JOIN (
                    SELECT DISTINCT order_id::VARCHAR AS order_id
                    FROM instadata.etl.agg_ma_order_item_daily_v2
                    WHERE 1 = 1
                        AND delivered_date_pt >= :v_campaign_start
                        AND delivered_date_pt <= :v_campaign_end
                        AND delivered_entity_level_1_id::VARCHAR = :v_brand_b_id
                        AND country_id = :v_country_id
                ) b_exists ON aoi.order_id::VARCHAR = b_exists.order_id
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_campaign_start
                    AND aoi.delivered_date_pt <= :v_campaign_end
                    AND aoi.country_id = :v_country_id
                    AND (a_exists.order_id IS NOT NULL OR b_exists.order_id IS NOT NULL)
            ),
            -- Compute cross-shopping percentage for both periods
            cross_shop_summary AS (
                SELECT
                    period,
                    total_orders,
                    orders_with_both,
                    DIV0(orders_with_both * 100.0, total_orders) AS cross_shop_pct
                FROM (
                    SELECT * FROM pre_period_cross_shop
                    UNION ALL
                    SELECT * FROM campaign_period_cross_shop
                )
            ),
            -- Calculate period-over-period percentage point change in cross-shop rate
            cross_shop_comparison AS (
                SELECT
                    'CrossShop_Summary' AS metric_type,
                    csp.period,
                    csp.total_orders,
                    csp.orders_with_both,
                    csp.cross_shop_pct,
                    DIV0(
                        csp.cross_shop_pct - pre.cross_shop_pct,
                        pre.cross_shop_pct
                    ) * 100 AS cross_shop_pct_change
                FROM cross_shop_summary csp
                CROSS JOIN (
                    SELECT cross_shop_pct
                    FROM cross_shop_summary
                    WHERE period = 'Pre-Campaign'
                ) pre
            ),
            -- Identify customers loyal to brand A during pre-period baseline
            brand_a_loyal_customers AS (
                SELECT DISTINCT
                    aoi.user_id::VARCHAR AS user_id
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_pre_start_date
                    AND aoi.delivered_date_pt <= :v_pre_end_date
                    AND aoi.delivered_entity_level_1_id::VARCHAR = :v_brand_a_id
                    AND aoi.country_id = :v_country_id
            ),
            -- Identify customers loyal to brand B during pre-period baseline
            brand_b_loyal_customers AS (
                SELECT DISTINCT
                    aoi.user_id::VARCHAR AS user_id
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_pre_start_date
                    AND aoi.delivered_date_pt <= :v_pre_end_date
                    AND aoi.delivered_entity_level_1_id::VARCHAR = :v_brand_b_id
                    AND aoi.country_id = :v_country_id
            ),
            -- Measure trial of partner brand B among brand A's loyal customer base during campaign
            brand_b_trial_from_a_base AS (
                SELECT
                    'Trial_Analysis' AS metric_type,
                    :v_brand_b_id AS brand,
                    COUNT(DISTINCT bala.user_id) AS partner_loyal_customers,
                    COUNT(DISTINCT CASE
                        WHEN uoin.new_to_brand_365_day = TRUE
                        THEN uoin.user_id::VARCHAR
                    END) AS tried_this_brand_during_campaign,
                    DIV0(
                        COUNT(DISTINCT CASE
                            WHEN uoin.new_to_brand_365_day = TRUE
                            THEN uoin.user_id::VARCHAR
                        END) * 100.0,
                        COUNT(DISTINCT bala.user_id)
                    ) AS trial_rate
                FROM brand_a_loyal_customers bala
                LEFT JOIN ads.ads_dwh.unified_order_item_ntx uoin
                    ON bala.user_id = uoin.user_id::VARCHAR
                    AND uoin.order_item_created_date_pt >= :v_campaign_start
                    AND uoin.order_item_created_date_pt <= :v_campaign_end
                WHERE 1 = 1
            ),
            -- Measure trial of partner brand A among brand B's loyal customer base during campaign
            brand_a_trial_from_b_base AS (
                SELECT
                    'Trial_Analysis' AS metric_type,
                    :v_brand_a_id AS brand,
                    COUNT(DISTINCT blba.user_id) AS partner_loyal_customers,
                    COUNT(DISTINCT CASE
                        WHEN uoin.new_to_brand_365_day = TRUE
                        THEN uoin.user_id::VARCHAR
                    END) AS tried_this_brand_during_campaign,
                    DIV0(
                        COUNT(DISTINCT CASE
                            WHEN uoin.new_to_brand_365_day = TRUE
                            THEN uoin.user_id::VARCHAR
                        END) * 100.0,
                        COUNT(DISTINCT blba.user_id)
                    ) AS trial_rate
                FROM brand_b_loyal_customers blba
                LEFT JOIN ads.ads_dwh.unified_order_item_ntx uoin
                    ON blba.user_id = uoin.user_id::VARCHAR
                    AND uoin.order_item_created_date_pt >= :v_campaign_start
                    AND uoin.order_item_created_date_pt <= :v_campaign_end
                WHERE 1 = 1
            ),
            -- Combine trial rates for both directions
            trial_analysis_combined AS (
                SELECT * FROM brand_b_trial_from_a_base
                UNION ALL
                SELECT * FROM brand_a_trial_from_b_base
            ),
            -- Baseline product categories for brand A during pre-period
            brand_a_categories_pre AS (
                SELECT DISTINCT
                    aoi.delivered_entity_category
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_pre_start_date
                    AND aoi.delivered_date_pt <= :v_pre_end_date
                    AND aoi.delivered_entity_level_1_id::VARCHAR = :v_brand_a_id
                    AND aoi.country_id = :v_country_id
            ),
            -- Baseline product categories for brand B during pre-period
            brand_b_categories_pre AS (
                SELECT DISTINCT
                    aoi.delivered_entity_category
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_pre_start_date
                    AND aoi.delivered_date_pt <= :v_pre_end_date
                    AND aoi.delivered_entity_level_1_id::VARCHAR = :v_brand_b_id
                    AND aoi.country_id = :v_country_id
            ),
            -- Identify new categories brand A expanded into during campaign
            brand_a_new_categories AS (
                SELECT DISTINCT
                    aoi.delivered_entity_category
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_campaign_start
                    AND aoi.delivered_date_pt <= :v_campaign_end
                    AND aoi.delivered_entity_level_1_id::VARCHAR = :v_brand_a_id
                    AND aoi.country_id = :v_country_id
                    AND aoi.delivered_entity_category NOT IN (SELECT delivered_entity_category FROM brand_a_categories_pre)
            ),
            -- Identify new categories brand B expanded into during campaign
            brand_b_new_categories AS (
                SELECT DISTINCT
                    aoi.delivered_entity_category
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_campaign_start
                    AND aoi.delivered_date_pt <= :v_campaign_end
                    AND aoi.delivered_entity_level_1_id::VARCHAR = :v_brand_b_id
                    AND aoi.country_id = :v_country_id
                    AND aoi.delivered_entity_category NOT IN (SELECT delivered_entity_category FROM brand_b_categories_pre)
            ),
            -- Measure volume in newly entered categories for brand A
            aisle_expansion_brand_a AS (
                SELECT
                    'Aisle_Expansion' AS metric_type,
                    :v_brand_a_id AS brand,
                    bnc.delivered_entity_category AS new_category,
                    COUNT(DISTINCT aoi.order_id::VARCHAR) AS orders_in_category
                FROM brand_a_new_categories bnc
                INNER JOIN instadata.etl.agg_ma_order_item_daily_v2 aoi
                    ON aoi.delivered_entity_category = bnc.delivered_entity_category
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_campaign_start
                    AND aoi.delivered_date_pt <= :v_campaign_end
                    AND aoi.delivered_entity_level_1_id::VARCHAR = :v_brand_a_id
                    AND aoi.country_id = :v_country_id
                GROUP BY bnc.delivered_entity_category
            ),
            -- Measure volume in newly entered categories for brand B
            aisle_expansion_brand_b AS (
                SELECT
                    'Aisle_Expansion' AS metric_type,
                    :v_brand_b_id AS brand,
                    bnc.delivered_entity_category AS new_category,
                    COUNT(DISTINCT aoi.order_id::VARCHAR) AS orders_in_category
                FROM brand_b_new_categories bnc
                INNER JOIN instadata.etl.agg_ma_order_item_daily_v2 aoi
                    ON aoi.delivered_entity_category = bnc.delivered_entity_category
                WHERE 1 = 1
                    AND aoi.delivered_date_pt >= :v_campaign_start
                    AND aoi.delivered_date_pt <= :v_campaign_end
                    AND aoi.delivered_entity_level_1_id::VARCHAR = :v_brand_b_id
                    AND aoi.country_id = :v_country_id
                GROUP BY bnc.delivered_entity_category
            ),
            -- Combine new category footprints for both brands
            aisle_expansion_combined AS (
                SELECT * FROM aisle_expansion_brand_a
                UNION ALL
                SELECT * FROM aisle_expansion_brand_b
            ),
            -- Shape cross-shop section into unified output schema
            final_output_cross_shop AS (
                SELECT
                    metric_type,
                    period,
                    total_orders,
                    orders_with_both,
                    cross_shop_pct,
                    cross_shop_pct_change,
                    NULL::VARCHAR AS brand,
                    NULL::INTEGER AS partner_loyal_customers,
                    NULL::INTEGER AS tried_this_brand_during_campaign,
                    NULL::DECIMAL(10, 4) AS trial_rate,
                    NULL::VARCHAR AS new_category,
                    NULL::INTEGER AS orders_in_category
                FROM cross_shop_comparison
            ),
            -- Shape trial section into unified output schema
            final_output_trial AS (
                SELECT
                    metric_type,
                    NULL::VARCHAR AS period,
                    NULL::INTEGER AS total_orders,
                    NULL::INTEGER AS orders_with_both,
                    NULL::DECIMAL(10, 4) AS cross_shop_pct,
                    NULL::DECIMAL(10, 4) AS cross_shop_pct_change,
                    brand,
                    partner_loyal_customers,
                    tried_this_brand_during_campaign,
                    trial_rate,
                    NULL::VARCHAR AS new_category,
                    NULL::INTEGER AS orders_in_category
                FROM trial_analysis_combined
            ),
            -- Shape aisle expansion section into unified output schema
            final_output_aisle AS (
                SELECT
                    metric_type,
                    NULL::VARCHAR AS period,
                    NULL::INTEGER AS total_orders,
                    NULL::INTEGER AS orders_with_both,
                    NULL::DECIMAL(10, 4) AS cross_shop_pct,
                    NULL::DECIMAL(10, 4) AS cross_shop_pct_change,
                    brand,
                    NULL::INTEGER AS partner_loyal_customers,
                    NULL::INTEGER AS tried_this_brand_during_campaign,
                    NULL::DECIMAL(10, 4) AS trial_rate,
                    new_category,
                    orders_in_category
                FROM aisle_expansion_combined
            )
            SELECT
                metric_type::VARCHAR,
                period::VARCHAR,
                total_orders::INTEGER,
                orders_with_both::INTEGER,
                cross_shop_pct::DECIMAL(10,4),
                cross_shop_pct_change::DECIMAL(10,4),
                brand::VARCHAR,
                partner_loyal_customers::INTEGER,
                tried_this_brand_during_campaign::INTEGER,
                trial_rate::DECIMAL(10,4),
                new_category::VARCHAR,
                orders_in_category::INTEGER
            FROM final_output_cross_shop
            WHERE 1 = 1
            UNION ALL
            SELECT
                metric_type::VARCHAR,
                period::VARCHAR,
                total_orders::INTEGER,
                orders_with_both::INTEGER,
                cross_shop_pct::DECIMAL(10,4),
                cross_shop_pct_change::DECIMAL(10,4),
                brand::VARCHAR,
                partner_loyal_customers::INTEGER,
                tried_this_brand_during_campaign::INTEGER,
                trial_rate::DECIMAL(10,4),
                new_category::VARCHAR,
                orders_in_category::INTEGER
            FROM final_output_trial
            WHERE 1 = 1
            UNION ALL
            SELECT
                metric_type::VARCHAR,
                period::VARCHAR,
                total_orders::INTEGER,
                orders_with_both::INTEGER,
                cross_shop_pct::DECIMAL(10,4),
                cross_shop_pct_change::DECIMAL(10,4),
                brand::VARCHAR,
                partner_loyal_customers::INTEGER,
                tried_this_brand_during_campaign::INTEGER,
                trial_rate::DECIMAL(10,4),
                new_category::VARCHAR,
                orders_in_category::INTEGER
            FROM final_output_aisle
            WHERE 1 = 1
            ORDER BY 1, 2, 7, 11
        );
        RETURN TABLE(res);
    END;
$$;
