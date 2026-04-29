USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- Procedure: PROMO_EFFECTIVENESS
-- Purpose: Analyze promotional performance via basket composition, repeat purchase rates, and new-to-brand conversion
-- Sample CALL: CALL PROMO_EFFECTIVENESS('SUAS', 98765, '2025-10-01', '2025-10-31', 90, 840)

CREATE OR REPLACE PROCEDURE PROMO_EFFECTIVENESS(
    promo_type VARCHAR,
    campaign_id INTEGER,
    promo_period_start DATE,
    promo_period_end DATE,
    post_purchase_window_days INTEGER DEFAULT 90,
    country_id INTEGER DEFAULT 840
)
RETURNS TABLE (
    metric_group VARCHAR,
    metric_name VARCHAR,
    group_label VARCHAR,
    metric_value DECIMAL(18, 4),
    metric_count INTEGER
)
LANGUAGE SQL
AS
$$
    DECLARE
        v_promo_type VARCHAR := promo_type;
        v_campaign_id INTEGER := campaign_id;
        v_promo_start DATE := promo_period_start;
        v_promo_end DATE := promo_period_end;
        v_post_window_days INTEGER := post_purchase_window_days;
        v_country_id INTEGER := country_id;
        v_post_window_end DATE;
        res RESULTSET;
    BEGIN
        v_post_window_end := DATEADD(day, v_post_window_days, v_promo_end);

        res := (
            -- Consolidate redemption data from SUAS and coupon/gift sources
            WITH redemption_orders AS (
                SELECT
                    user_id::VARCHAR AS user_id,
                    order_id::VARCHAR AS order_id,
                    campaign_id,
                    delivered_date_pt,
                    CASE
                        WHEN :v_promo_type = 'SUAS'
                            THEN 'Redeemed'
                        WHEN :v_promo_type IN ('COUPON', 'FREE_GIFT')
                            THEN 'Redeemed'
                        ELSE 'Non-Redemption'
                    END AS redemption_status
                FROM (
                    SELECT
                        user_id::VARCHAR AS user_id,
                        order_id::VARCHAR AS order_id,
                        campaign_id,
                        delivered_date_pt
                    FROM ads.ads_dwh.fact_spend_promotion_redemption
                    WHERE 1 = 1
                        AND :v_promo_type = 'SUAS'
                        AND campaign_id = :v_campaign_id
                        AND delivered_date_pt BETWEEN :v_promo_start AND :v_promo_end
                        AND overall_status = 'VALID'
                        AND REGEXP_LIKE(order_id::VARCHAR, '^[0-9]+$')

                    UNION ALL

                    SELECT
                        user_id::VARCHAR AS user_id,
                        order_id::VARCHAR AS order_id,
                        campaign_id,
                        delivered_date_pt
                    FROM ads.ads_dwh.fact_coupon_campaign_redemption
                    WHERE 1 = 1
                        AND :v_promo_type IN ('COUPON', 'FREE_GIFT')
                        AND campaign_id = :v_campaign_id
                        AND delivered_date_pt BETWEEN :v_promo_start AND :v_promo_end
                        AND REGEXP_LIKE(order_id::VARCHAR, '^[0-9]+$')
                )
            ),
            -- De-duplicate product taxonomy to latest version for UPC enrichment
            taxonomy_deduped AS (
                SELECT
                    product_id,
                    upc
                FROM instadata.etl.ads_taxonomy_products_extd
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY product_id
                    ORDER BY last_update_ts DESC
                ) = 1
            ),
            -- Enrich redeemed orders with basket composition
            redemption_basket_items AS (
                SELECT
                    ro.user_id,
                    ro.order_id,
                    ro.delivered_date_pt,
                    aoi.delivered_entity_brand_id::VARCHAR,
                    atp.upc,
                    aoi.final_charge_amt_usd,
                    aoi.picked_quantity,
                    COUNT(DISTINCT atp.product_id) OVER (
                        PARTITION BY ro.order_id
                    ) AS brands_in_order,
                    COUNT(DISTINCT atp.upc) OVER (
                        PARTITION BY ro.order_id
                    ) AS upcs_in_order,
                    SUM(aoi.final_charge_amt_usd) OVER (
                        PARTITION BY ro.order_id
                    ) AS order_total_usd,
                    SUM(aoi.picked_quantity) OVER (
                        PARTITION BY ro.order_id
                    ) AS order_total_units
                FROM redemption_orders ro
                INNER JOIN instadata.etl.agg_ma_order_item_daily_v2 aoi
                    ON ro.order_id = aoi.order_id::VARCHAR
                    AND ro.delivered_date_pt = aoi.delivered_date_pt
                    AND aoi.country_id = :v_country_id
                LEFT JOIN ads.ads_dwh.unified_order_item_ntx ntx
                    ON ro.order_id::VARCHAR = ntx.order_id::VARCHAR
                    AND aoi.delivered_product_id::VARCHAR = ntx.product_id::VARCHAR
                    AND ntx.order_item_created_date_pt BETWEEN :v_promo_start AND :v_promo_end
                LEFT JOIN taxonomy_deduped atp
                    ON aoi.delivered_product_id::VARCHAR = atp.product_id::VARCHAR
            ),
            -- Aggregate basket metrics at user-order level
            redemption_basket_metrics AS (
                SELECT
                    'Redemption Baskets' AS basket_group,
                    user_id,
                    order_id,
                    AVG(COALESCE(order_total_usd, 0)) AS avg_basket_value,
                    AVG(COALESCE(order_total_units, 0)) AS avg_units,
                    AVG(COALESCE(brands_in_order, 0)) AS avg_brands,
                    AVG(COALESCE(upcs_in_order, 0)) AS avg_upcs,
                    COUNT(DISTINCT CASE WHEN brands_in_order >= 2 THEN order_id END) AS baskets_with_2plus_brands
                FROM redemption_basket_items
                GROUP BY 1, 2, 3
            ),
            -- Pivot basket metrics into comparable KPIs
            basket_comparison AS (
                SELECT
                    'Basket Composition' AS metric_group,
                    'Avg Basket Value USD' AS metric_name,
                    basket_group AS group_label,
                    DIV0(SUM(avg_basket_value), COUNT(DISTINCT user_id)) AS metric_value,
                    COUNT(DISTINCT order_id) AS metric_count
                FROM redemption_basket_metrics
                GROUP BY 1, 2, 3

                UNION ALL

                SELECT
                    'Basket Composition' AS metric_group,
                    'Avg Units per Basket' AS metric_name,
                    basket_group AS group_label,
                    DIV0(SUM(avg_units), COUNT(DISTINCT user_id)) AS metric_value,
                    COUNT(DISTINCT order_id) AS metric_count
                FROM redemption_basket_metrics
                GROUP BY 1, 2, 3

                UNION ALL

                SELECT
                    'Basket Composition' AS metric_group,
                    'Avg Brands per Basket' AS metric_name,
                    basket_group AS group_label,
                    DIV0(SUM(avg_brands), COUNT(DISTINCT user_id)) AS metric_value,
                    COUNT(DISTINCT order_id) AS metric_count
                FROM redemption_basket_metrics
                GROUP BY 1, 2, 3

                UNION ALL

                SELECT
                    'Basket Composition' AS metric_group,
                    'Avg UPCs per Basket' AS metric_name,
                    basket_group AS group_label,
                    DIV0(SUM(avg_upcs), COUNT(DISTINCT user_id)) AS metric_value,
                    COUNT(DISTINCT order_id) AS metric_count
                FROM redemption_basket_metrics
                GROUP BY 1, 2, 3

                UNION ALL

                SELECT
                    'Basket Composition' AS metric_group,
                    'Pct with 2+ Brands' AS metric_name,
                    basket_group AS group_label,
                    DIV0(
                        SUM(baskets_with_2plus_brands),
                        COUNT(DISTINCT order_id)
                    ) * 100 AS metric_value,
                    COUNT(DISTINCT order_id) AS metric_count
                FROM redemption_basket_metrics
                GROUP BY 1, 2, 3
            ),
            -- Measure repeat purchase behavior at 30-day window post-redemption
            repeat_window_30_days AS (
                SELECT
                    ro.user_id,
                    COUNT(DISTINCT ro.order_id) AS total_redeemers,
                    COUNT(DISTINCT CASE
                        WHEN follow_up.order_id IS NOT NULL
                            THEN ro.user_id
                    END) AS returned_within_30,
                    30 AS window_days
                FROM redemption_orders ro
                LEFT JOIN instadata.etl.agg_ma_order_item_daily_v2 follow_up
                    ON ro.user_id::VARCHAR = follow_up.user_id::VARCHAR
                    AND follow_up.delivered_date_pt > ro.delivered_date_pt
                    AND follow_up.delivered_date_pt <= DATEADD(day, 30, ro.delivered_date_pt)
                GROUP BY 1, 4
            ),
            -- Measure repeat purchase behavior at 60-day window post-redemption
            repeat_window_60_days AS (
                SELECT
                    ro.user_id,
                    COUNT(DISTINCT ro.order_id) AS total_redeemers,
                    COUNT(DISTINCT CASE
                        WHEN follow_up.order_id IS NOT NULL
                            THEN ro.user_id
                    END) AS returned_within_60,
                    60 AS window_days
                FROM redemption_orders ro
                LEFT JOIN instadata.etl.agg_ma_order_item_daily_v2 follow_up
                    ON ro.user_id::VARCHAR = follow_up.user_id::VARCHAR
                    AND follow_up.delivered_date_pt > ro.delivered_date_pt
                    AND follow_up.delivered_date_pt <= DATEADD(day, 60, ro.delivered_date_pt)
                GROUP BY 1, 4
            ),
            -- Measure repeat purchase behavior at 90-day window post-redemption
            repeat_window_90_days AS (
                SELECT
                    ro.user_id,
                    COUNT(DISTINCT ro.order_id) AS total_redeemers,
                    COUNT(DISTINCT CASE
                        WHEN follow_up.order_id IS NOT NULL
                            THEN ro.user_id
                    END) AS returned_within_90,
                    90 AS window_days
                FROM redemption_orders ro
                LEFT JOIN instadata.etl.agg_ma_order_item_daily_v2 follow_up
                    ON ro.user_id::VARCHAR = follow_up.user_id::VARCHAR
                    AND follow_up.delivered_date_pt > ro.delivered_date_pt
                    AND follow_up.delivered_date_pt <= DATEADD(day, 90, ro.delivered_date_pt)
                GROUP BY 1, 4
            ),
            -- Pivot all three repeat windows into a unified metric table
            repeat_analysis AS (
                SELECT
                    'Repeat Purchase' AS metric_group,
                    'Total Redeemers' AS metric_name,
                    CAST(window_days AS VARCHAR) || ' Days' AS group_label,
                    COALESCE(SUM(total_redeemers), 0) AS metric_value,
                    COALESCE(SUM(total_redeemers), 0) AS metric_count
                FROM repeat_window_30_days
                GROUP BY 1, 2, 3

                UNION ALL

                SELECT
                    'Repeat Purchase' AS metric_group,
                    'Repeat Rate Pct' AS metric_name,
                    CAST(window_days AS VARCHAR) || ' Days' AS group_label,
                    DIV0(
                        SUM(returned_within_30),
                        SUM(total_redeemers)
                    ) * 100 AS metric_value,
                    COALESCE(SUM(total_redeemers), 0) AS metric_count
                FROM repeat_window_30_days
                GROUP BY 1, 2, 3

                UNION ALL

                SELECT
                    'Repeat Purchase' AS metric_group,
                    'Total Redeemers' AS metric_name,
                    CAST(window_days AS VARCHAR) || ' Days' AS group_label,
                    COALESCE(SUM(total_redeemers), 0) AS metric_value,
                    COALESCE(SUM(total_redeemers), 0) AS metric_count
                FROM repeat_window_60_days
                GROUP BY 1, 2, 3

                UNION ALL

                SELECT
                    'Repeat Purchase' AS metric_group,
                    'Repeat Rate Pct' AS metric_name,
                    CAST(window_days AS VARCHAR) || ' Days' AS group_label,
                    DIV0(
                        SUM(returned_within_60),
                        SUM(total_redeemers)
                    ) * 100 AS metric_value,
                    COALESCE(SUM(total_redeemers), 0) AS metric_count
                FROM repeat_window_60_days
                GROUP BY 1, 2, 3

                UNION ALL

                SELECT
                    'Repeat Purchase' AS metric_group,
                    'Total Redeemers' AS metric_name,
                    CAST(window_days AS VARCHAR) || ' Days' AS group_label,
                    COALESCE(SUM(total_redeemers), 0) AS metric_value,
                    COALESCE(SUM(total_redeemers), 0) AS metric_count
                FROM repeat_window_90_days
                GROUP BY 1, 2, 3

                UNION ALL

                SELECT
                    'Repeat Purchase' AS metric_group,
                    'Repeat Rate Pct' AS metric_name,
                    CAST(window_days AS VARCHAR) || ' Days' AS group_label,
                    DIV0(
                        SUM(returned_within_90),
                        SUM(total_redeemers)
                    ) * 100 AS metric_value,
                    COALESCE(SUM(total_redeemers), 0) AS metric_count
                FROM repeat_window_90_days
                GROUP BY 1, 2, 3
            ),
            -- Measure new-to-brand acquisition
            ntb_analysis AS (
                SELECT
                    'NTB Analysis' AS metric_group,
                    'NTB Pct of Redeemers' AS metric_name,
                    'New to Brand' AS group_label,
                    DIV0(
                        COUNT(DISTINCT CASE
                            WHEN ntx.new_to_brand_365_day = TRUE
                                THEN ro.user_id
                        END),
                        COUNT(DISTINCT ro.user_id)
                    ) * 100 AS metric_value,
                    COUNT(DISTINCT ro.user_id) AS metric_count
                FROM redemption_orders ro
                LEFT JOIN ads.ads_dwh.unified_order_item_ntx ntx
                    ON ro.user_id::VARCHAR = ntx.user_id::VARCHAR
                    AND ro.order_id::VARCHAR = ntx.order_id::VARCHAR
                    AND ntx.order_item_created_date_pt BETWEEN :v_promo_start AND :v_promo_end
                WHERE 1 = 1
                GROUP BY 1, 2, 3
            )

            SELECT
                metric_group::VARCHAR AS metric_group,
                metric_name::VARCHAR AS metric_name,
                group_label::VARCHAR AS group_label,
                metric_value::DECIMAL(18,4) AS metric_value,
                metric_count::INTEGER AS metric_count
            FROM basket_comparison
            UNION ALL
            SELECT
                metric_group::VARCHAR,
                metric_name::VARCHAR,
                group_label::VARCHAR,
                metric_value::DECIMAL(18,4),
                metric_count::INTEGER
            FROM repeat_analysis
            UNION ALL
            SELECT
                metric_group::VARCHAR,
                metric_name::VARCHAR,
                group_label::VARCHAR,
                metric_value::DECIMAL(18,4),
                metric_count::INTEGER
            FROM ntb_analysis
        );
        RETURN TABLE(res);
    END;
$$;
