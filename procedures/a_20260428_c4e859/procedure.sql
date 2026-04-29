USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- Procedure: IMPULSE_PLACEMENT
-- Purpose: Compare trial and repeat rates for impulse vs standard placements, segmented by customer novelty
-- CALL IMPULSE_PLACEMENT(67890, 'SOFT DRINKS', '2025-09-01', '2025-09-30', 365, 840)

CREATE OR REPLACE PROCEDURE IMPULSE_PLACEMENT(
    brand_id BIGINT,
    category_name VARCHAR,
    campaign_start DATE,
    campaign_end DATE,
    ntb_lookback_days INT DEFAULT 365,
    country_id INT DEFAULT 840
)
RETURNS TABLE (
    metric_name VARCHAR,
    metric_value NUMBER
)
LANGUAGE SQL
AS
$$
DECLARE
    v_brand_id BIGINT := brand_id;
    v_category_name VARCHAR := category_name;
    v_campaign_start DATE := campaign_start;
    v_campaign_end DATE := campaign_end;
    v_ntb_lookback_days INT := ntb_lookback_days;
    v_country_id INT := country_id;
    v_ntb_lookback_start DATE;
    res RESULTSET;
BEGIN

    v_ntb_lookback_start := DATEADD(day, -v_ntb_lookback_days, v_campaign_start);

    -- Get impulse placement events scoped to brand, joined to multi-touch attributed orders
    CREATE OR REPLACE TEMP TABLE temp_impulse_placements AS
    SELECT
        afpd.event_date_pt::DATE AS event_date,
        afpd.product_id,
        afpd.campaign_id,
        afpd.placement_type,
        afpd.attributed_sales_usd,
        fp.user_id,
        fp.order_id,
        fp.order_item_product_id::VARCHAR AS order_item_product_id,
        fp.ad_group_type
    FROM ads.ads_dwh.agg_featured_product_daily afpd
    INNER JOIN ads.ads_dwh.multi_touch_click_prioritized_ads_attributions fp
        ON afpd.product_id::VARCHAR = fp.order_item_product_id::VARCHAR
        AND afpd.event_date_pt::DATE = fp.attributable_event_date_time_pt::DATE
    INNER JOIN rds.ads_production.campaigns cam
        ON afpd.campaign_id::VARCHAR = cam.id::VARCHAR
        AND cam.campaign_type = 'featured_product'
        AND cam.exchange_name IS NULL
    INNER JOIN instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings dme
        ON cam.account_id = dme.account_id
        AND dme.entity_level_1_id_comprehensive = :v_brand_id
    WHERE 1 = 1
        AND afpd.event_date_pt >= :v_campaign_start
        AND afpd.event_date_pt <= :v_campaign_end
        AND REGEXP_LIKE(afpd.placement_type::VARCHAR, '(impulse|featured_impulse)', 'i')
        AND fp.ad_group_type = 'FEATURED_PRODUCT';

    -- Classify impulse buyers by newness (NTB/NTP/NTC/Existing)
    CREATE OR REPLACE TEMP TABLE temp_ntb_classification AS
    SELECT
        ti.event_date,
        ti.product_id,
        ti.campaign_id,
        ti.placement_type,
        ti.attributed_sales_usd,
        ti.user_id,
        ti.order_id::VARCHAR AS order_id,
        ti.order_item_product_id,
        ntx.new_to_brand_365_day,
        ntx.new_to_product_365_day,
        ntx.new_to_category_365_day,
        ntx.order_item_created_date_pt::DATE AS purchase_date,
        CASE
            WHEN ntx.new_to_brand_365_day = TRUE THEN 'NTB'
            WHEN ntx.new_to_product_365_day = TRUE AND ntx.new_to_brand_365_day = FALSE THEN 'NTP'
            WHEN ntx.new_to_category_365_day = TRUE AND ntx.new_to_product_365_day = FALSE THEN 'NTC'
            ELSE 'Existing'
        END AS customer_type
    FROM temp_impulse_placements ti
    LEFT JOIN ads.ads_dwh.unified_order_item_ntx ntx
        ON ti.order_id::VARCHAR = ntx.order_id::VARCHAR
        AND ti.order_item_product_id::VARCHAR = ntx.product_id::VARCHAR
        AND ntx.order_item_created_date_pt >= :v_ntb_lookback_start
        AND ntx.order_item_created_date_pt <= DATEADD(day, 0, :v_campaign_end)
    WHERE 1 = 1;

    -- Measure repeat brand purchases in 7/14/30 days post initial impulse trial
    CREATE OR REPLACE TEMP TABLE temp_repeat_purchases AS
    SELECT
        tnc.user_id,
        tnc.order_id,
        tnc.product_id,
        tnc.customer_type,
        tnc.purchase_date,
        COALESCE(SUM(CASE WHEN amd.delivered_date_pt >= DATEADD(day, 1, tnc.purchase_date)
                            AND amd.delivered_date_pt <= DATEADD(day, 7, tnc.purchase_date)
                            AND amd.delivered_entity_level_1_id = :v_brand_id THEN 1 ELSE 0 END), 0) AS repeat_7day,
        COALESCE(SUM(CASE WHEN amd.delivered_date_pt >= DATEADD(day, 1, tnc.purchase_date)
                            AND amd.delivered_date_pt <= DATEADD(day, 14, tnc.purchase_date)
                            AND amd.delivered_entity_level_1_id = :v_brand_id THEN 1 ELSE 0 END), 0) AS repeat_14day,
        COALESCE(SUM(CASE WHEN amd.delivered_date_pt >= DATEADD(day, 1, tnc.purchase_date)
                            AND amd.delivered_date_pt <= DATEADD(day, 30, tnc.purchase_date)
                            AND amd.delivered_entity_level_1_id = :v_brand_id THEN 1 ELSE 0 END), 0) AS repeat_30day
    FROM temp_ntb_classification tnc
    LEFT JOIN (
        SELECT
            user_id::VARCHAR AS user_id,
            delivered_date_pt,
            delivered_entity_level_1_id,
            order_id
        FROM instadata.etl.agg_ma_order_item_daily_v2
        WHERE 1 = 1
            AND country_id = :v_country_id
            AND delivered_date_pt >= :v_campaign_start
            AND delivered_date_pt <= DATEADD(day, 30, :v_campaign_end)
    ) amd
        ON tnc.user_id::VARCHAR = amd.user_id
        AND amd.delivered_date_pt >= tnc.purchase_date
        AND amd.delivered_date_pt <= DATEADD(day, 30, tnc.purchase_date)
    WHERE 1 = 1
    GROUP BY
        tnc.user_id,
        tnc.order_id,
        tnc.product_id,
        tnc.customer_type,
        tnc.purchase_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY tnc.order_id ORDER BY tnc.purchase_date) = 1;

    -- Non-impulse placements: build independent temp tables for fair comparison
    CREATE OR REPLACE TEMP TABLE temp_non_impulse_placements AS
    SELECT
        afpd.event_date_pt::DATE AS event_date,
        afpd.product_id,
        afpd.campaign_id,
        afpd.placement_type,
        afpd.attributed_sales_usd,
        fp_ni.user_id,
        fp_ni.order_id,
        fp_ni.order_item_product_id::VARCHAR AS order_item_product_id
    FROM ads.ads_dwh.agg_featured_product_daily afpd
    INNER JOIN ads.ads_dwh.multi_touch_click_prioritized_ads_attributions fp_ni
        ON afpd.product_id::VARCHAR = fp_ni.order_item_product_id::VARCHAR
        AND afpd.event_date_pt::DATE = fp_ni.attributable_event_date_time_pt::DATE
    INNER JOIN rds.ads_production.campaigns cam
        ON afpd.campaign_id::VARCHAR = cam.id::VARCHAR
        AND cam.campaign_type = 'featured_product'
        AND cam.exchange_name IS NULL
    INNER JOIN instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings dme
        ON cam.account_id = dme.account_id
        AND dme.entity_level_1_id_comprehensive = :v_brand_id
    WHERE 1 = 1
        AND afpd.event_date_pt >= :v_campaign_start
        AND afpd.event_date_pt <= :v_campaign_end
        AND fp_ni.ad_group_type = 'FEATURED_PRODUCT'
        AND NOT REGEXP_LIKE(afpd.placement_type::VARCHAR, '(impulse|featured_impulse)', 'i');

    -- Classify non-impulse buyers by newness (NTB/NTP/NTC/Existing)
    CREATE OR REPLACE TEMP TABLE temp_ni_ntb_classification AS
    SELECT
        tni.event_date,
        tni.product_id,
        tni.campaign_id,
        tni.attributed_sales_usd,
        tni.user_id,
        tni.order_id::VARCHAR AS order_id,
        tni.order_item_product_id,
        ntx.order_item_created_date_pt::DATE AS purchase_date,
        CASE
            WHEN ntx.new_to_brand_365_day = TRUE THEN 'NTB'
            WHEN ntx.new_to_product_365_day = TRUE AND ntx.new_to_brand_365_day = FALSE THEN 'NTP'
            WHEN ntx.new_to_category_365_day = TRUE AND ntx.new_to_product_365_day = FALSE THEN 'NTC'
            ELSE 'Existing'
        END AS customer_type
    FROM temp_non_impulse_placements tni
    LEFT JOIN ads.ads_dwh.unified_order_item_ntx ntx
        ON tni.order_id::VARCHAR = ntx.order_id::VARCHAR
        AND tni.order_item_product_id::VARCHAR = ntx.product_id::VARCHAR
        AND ntx.order_item_created_date_pt >= :v_ntb_lookback_start
        AND ntx.order_item_created_date_pt <= DATEADD(day, 0, :v_campaign_end)
    WHERE 1 = 1;

    -- Measure repeat purchases for non-impulse buyers (7/14/30-day windows)
    CREATE OR REPLACE TEMP TABLE temp_ni_repeat_purchases AS
    SELECT
        tnc.user_id,
        tnc.order_id,
        tnc.product_id,
        tnc.customer_type,
        tnc.purchase_date,
        COALESCE(SUM(CASE WHEN amd.delivered_date_pt >= DATEADD(day, 1, tnc.purchase_date)
                            AND amd.delivered_date_pt <= DATEADD(day, 7, tnc.purchase_date)
                            AND amd.delivered_entity_level_1_id = :v_brand_id THEN 1 ELSE 0 END), 0) AS repeat_7day,
        COALESCE(SUM(CASE WHEN amd.delivered_date_pt >= DATEADD(day, 1, tnc.purchase_date)
                            AND amd.delivered_date_pt <= DATEADD(day, 14, tnc.purchase_date)
                            AND amd.delivered_entity_level_1_id = :v_brand_id THEN 1 ELSE 0 END), 0) AS repeat_14day,
        COALESCE(SUM(CASE WHEN amd.delivered_date_pt >= DATEADD(day, 1, tnc.purchase_date)
                            AND amd.delivered_date_pt <= DATEADD(day, 30, tnc.purchase_date)
                            AND amd.delivered_entity_level_1_id = :v_brand_id THEN 1 ELSE 0 END), 0) AS repeat_30day
    FROM temp_ni_ntb_classification tnc
    LEFT JOIN (
        SELECT
            user_id::VARCHAR AS user_id,
            delivered_date_pt,
            delivered_entity_level_1_id,
            order_id
        FROM instadata.etl.agg_ma_order_item_daily_v2
        WHERE 1 = 1
            AND country_id = :v_country_id
            AND delivered_date_pt >= :v_campaign_start
            AND delivered_date_pt <= DATEADD(day, 30, :v_campaign_end)
    ) amd
        ON tnc.user_id::VARCHAR = amd.user_id
        AND amd.delivered_date_pt >= tnc.purchase_date
        AND amd.delivered_date_pt <= DATEADD(day, 30, tnc.purchase_date)
    WHERE 1 = 1
    GROUP BY
        tnc.user_id,
        tnc.order_id,
        tnc.product_id,
        tnc.customer_type,
        tnc.purchase_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY tnc.order_id ORDER BY tnc.purchase_date) = 1;

    -- Side-by-side comparison: impulse vs non-impulse trial rates, customer mix, repeat behavior
    CREATE OR REPLACE TEMP TABLE temp_impulse_vs_nonimpulse AS
    SELECT
        'impulse' AS placement_variant,
        COUNT(DISTINCT ti.campaign_id) AS campaign_count,
        COALESCE(SUM(ti.attributed_sales_usd), 0) AS total_sales,
        ROUND(100.0 * DIV0(
            SUM(CASE WHEN tnc.customer_type = 'NTB' THEN 1 ELSE 0 END),
            COUNT(*)
        ), 2) AS ntb_trial_rate,
        ROUND(100.0 * DIV0(
            SUM(CASE WHEN tnc.customer_type IN ('NTB', 'NTP', 'NTC') THEN 1 ELSE 0 END),
            COUNT(*)
        ), 2) AS new_customer_rate,
        ROUND(100.0 * DIV0(
            SUM(CASE WHEN trp.repeat_7day > 0 THEN 1 ELSE 0 END),
            COUNT(*)
        ), 2) AS repeat_rate_7day,
        ROUND(100.0 * DIV0(
            SUM(CASE WHEN trp.repeat_14day > 0 THEN 1 ELSE 0 END),
            COUNT(*)
        ), 2) AS repeat_rate_14day,
        ROUND(100.0 * DIV0(
            SUM(CASE WHEN trp.repeat_30day > 0 THEN 1 ELSE 0 END),
            COUNT(*)
        ), 2) AS repeat_rate_30day,
        COUNT(*) AS total_first_purchases
    FROM temp_impulse_placements ti
    LEFT JOIN temp_ntb_classification tnc
        ON ti.order_id::VARCHAR = tnc.order_id
        AND ti.order_item_product_id::VARCHAR = tnc.order_item_product_id
    LEFT JOIN temp_repeat_purchases trp
        ON tnc.user_id = trp.user_id
        AND tnc.order_id = trp.order_id
    WHERE 1 = 1
        AND tnc.purchase_date >= :v_campaign_start
        AND tnc.purchase_date <= :v_campaign_end
    GROUP BY placement_variant

    UNION ALL

    SELECT
        'non_impulse' AS placement_variant,
        COUNT(DISTINCT tni.campaign_id) AS campaign_count,
        COALESCE(SUM(tni.attributed_sales_usd), 0) AS total_sales,
        ROUND(100.0 * DIV0(
            SUM(CASE WHEN ni_ntc.customer_type = 'NTB' THEN 1 ELSE 0 END),
            COUNT(*)
        ), 2) AS ntb_trial_rate,
        ROUND(100.0 * DIV0(
            SUM(CASE WHEN ni_ntc.customer_type IN ('NTB', 'NTP', 'NTC') THEN 1 ELSE 0 END),
            COUNT(*)
        ), 2) AS new_customer_rate,
        ROUND(100.0 * DIV0(
            SUM(CASE WHEN ni_trp.repeat_7day > 0 THEN 1 ELSE 0 END),
            COUNT(*)
        ), 2) AS repeat_rate_7day,
        ROUND(100.0 * DIV0(
            SUM(CASE WHEN ni_trp.repeat_14day > 0 THEN 1 ELSE 0 END),
            COUNT(*)
        ), 2) AS repeat_rate_14day,
        ROUND(100.0 * DIV0(
            SUM(CASE WHEN ni_trp.repeat_30day > 0 THEN 1 ELSE 0 END),
            COUNT(*)
        ), 2) AS repeat_rate_30day,
        COUNT(*) AS total_first_purchases
    FROM temp_non_impulse_placements tni
    LEFT JOIN temp_ni_ntb_classification ni_ntc
        ON tni.order_id::VARCHAR = ni_ntc.order_id
        AND tni.order_item_product_id::VARCHAR = ni_ntc.order_item_product_id
    LEFT JOIN temp_ni_repeat_purchases ni_trp
        ON ni_ntc.user_id = ni_trp.user_id
        AND ni_ntc.order_id = ni_trp.order_id
    WHERE 1 = 1
        AND ni_ntc.purchase_date >= :v_campaign_start
        AND ni_ntc.purchase_date <= :v_campaign_end
    GROUP BY placement_variant;

    -- Pivot wide comparison into a key-value metric table
    CREATE OR REPLACE TEMP TABLE temp_final_metrics AS
    SELECT
        'impulse_ntb_trial_rate' AS metric_name,
        (SELECT ntb_trial_rate FROM temp_impulse_vs_nonimpulse WHERE placement_variant = 'impulse') AS metric_value

    UNION ALL

    SELECT
        'impulse_repeat_rate_7day' AS metric_name,
        (SELECT repeat_rate_7day FROM temp_impulse_vs_nonimpulse WHERE placement_variant = 'impulse') AS metric_value

    UNION ALL

    SELECT
        'impulse_repeat_rate_14day' AS metric_name,
        (SELECT repeat_rate_14day FROM temp_impulse_vs_nonimpulse WHERE placement_variant = 'impulse') AS metric_value

    UNION ALL

    SELECT
        'impulse_repeat_rate_30day' AS metric_name,
        (SELECT repeat_rate_30day FROM temp_impulse_vs_nonimpulse WHERE placement_variant = 'impulse') AS metric_value

    UNION ALL

    SELECT
        'impulse_total_first_purchases' AS metric_name,
        (SELECT total_first_purchases FROM temp_impulse_vs_nonimpulse WHERE placement_variant = 'impulse') AS metric_value

    UNION ALL

    SELECT
        'non_impulse_ntb_trial_rate' AS metric_name,
        (SELECT ntb_trial_rate FROM temp_impulse_vs_nonimpulse WHERE placement_variant = 'non_impulse') AS metric_value

    UNION ALL

    SELECT
        'non_impulse_repeat_rate_7day' AS metric_name,
        (SELECT repeat_rate_7day FROM temp_impulse_vs_nonimpulse WHERE placement_variant = 'non_impulse') AS metric_value

    UNION ALL

    SELECT
        'non_impulse_repeat_rate_14day' AS metric_name,
        (SELECT repeat_rate_14day FROM temp_impulse_vs_nonimpulse WHERE placement_variant = 'non_impulse') AS metric_value

    UNION ALL

    SELECT
        'non_impulse_repeat_rate_30day' AS metric_name,
        (SELECT repeat_rate_30day FROM temp_impulse_vs_nonimpulse WHERE placement_variant = 'non_impulse') AS metric_value

    UNION ALL

    SELECT
        'non_impulse_total_first_purchases' AS metric_name,
        (SELECT total_first_purchases FROM temp_impulse_vs_nonimpulse WHERE placement_variant = 'non_impulse') AS metric_value;

    res := (
        SELECT
            metric_name::VARCHAR AS metric_name,
            metric_value::NUMBER AS metric_value
        FROM temp_final_metrics
        WHERE 1 = 1
    );

    RETURN TABLE(res);

END;
$$;
