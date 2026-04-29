USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- PROCEDURE: NTB_COHORT_LTV
-- PURPOSE: Track new-to-brand customer cohorts acquired via SP through follow-up window, measuring retention and incremental LTV by month.
-- SAMPLE CALL: CALL NTB_COHORT_LTV('27028', 'SOFT DRINKS', '2025-10-01', '2025-12-31', 180, 365, 840);

CREATE OR REPLACE PROCEDURE NTB_COHORT_LTV(
    brand_id VARCHAR,
    category_name VARCHAR,
    acquisition_period_start DATE,
    acquisition_period_end DATE,
    follow_up_window_days INTEGER DEFAULT 180,
    ntb_lookback_days INTEGER DEFAULT 365,
    country_id INTEGER DEFAULT 840
)
RETURNS TABLE (
    output_type VARCHAR,
    cohort_label VARCHAR,
    cohort_size INTEGER,
    acquisition_period_sales DECIMAL(18, 2),
    acquisition_period_spend DECIMAL(18, 2),
    initial_roas DECIMAL(10, 4),
    time_bucket VARCHAR,
    cumulative_sales DECIMAL(18, 2),
    cumulative_roas DECIMAL(10, 4),
    pct_cohort_retained DECIMAL(10, 4),
    incremental_sales_this_period DECIMAL(18, 2)
)
LANGUAGE SQL
AS
$$
    DECLARE
        v_brand_id VARCHAR := brand_id;
        v_category_name VARCHAR := category_name;
        v_acq_start DATE := acquisition_period_start;
        v_acq_end DATE := acquisition_period_end;
        v_followup_days INTEGER := follow_up_window_days;
        v_ntb_days INTEGER := ntb_lookback_days;
        v_country_id INTEGER := country_id;
        v_followup_end_date DATE;
        res RESULTSET;
    BEGIN
        v_followup_end_date := DATEADD(day, v_followup_days, v_acq_end);

        res := (
            -- Identify first SP-attributed purchases from NTB customers during acquisition period
            WITH ntb_cohort_identification AS (
                SELECT
                    muatpa.user_id::VARCHAR AS user_id,
                    muatpa.order_id::VARCHAR AS order_id,
                    uoi.order_item_created_date_pt,
                    cem.entity_level_1_id_comprehensive,
                    COALESCE(muatpa.attributed_sales_nanos_usd / 1000000000.0, 0) AS attributed_sales_usd,
                    COALESCE(SUM(afd.billable_spend_usd), 0) AS sp_spend_usd
                FROM ads.ads_dwh.multi_touch_click_prioritized_ads_attributions muatpa
                INNER JOIN ads.ads_dwh.unified_order_item_ntx uoi
                    ON muatpa.user_id::VARCHAR = uoi.user_id::VARCHAR
                    AND muatpa.order_id::VARCHAR = uoi.order_id::VARCHAR
                    AND uoi.order_item_created_date_pt >= :v_acq_start
                    AND uoi.order_item_created_date_pt <= :v_acq_end
                INNER JOIN rds.ads_production.campaigns c
                    ON muatpa.campaign_id::VARCHAR = c.id::VARCHAR
                    AND c.campaign_type = 'featured_product'
                    AND c.exchange_name IS NULL
                INNER JOIN instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings cem
                    ON c.account_id = cem.account_id
                INNER JOIN ads.ads_dwh.agg_featured_product_daily afd
                    ON c.id::VARCHAR = afd.campaign_id::VARCHAR
                    AND afd.event_date_pt >= DATEADD(day, -:v_ntb_days, uoi.order_item_created_date_pt)
                    AND afd.event_date_pt <= uoi.order_item_created_date_pt
                WHERE 1 = 1
                    AND muatpa.ad_group_type = 'FEATURED_PRODUCT'
                    AND REGEXP_LIKE(muatpa.order_id::VARCHAR, '^[0-9]+$')
                    AND cem.entity_level_1_id_comprehensive = :v_brand_id
                    AND (uoi.new_to_brand_365_day = TRUE OR uoi.new_to_brand_182_day = TRUE)
                GROUP BY
                    muatpa.user_id::VARCHAR,
                    muatpa.order_id::VARCHAR,
                    uoi.order_item_created_date_pt,
                    cem.entity_level_1_id_comprehensive,
                    muatpa.attributed_sales_nanos_usd
                QUALIFY ROW_NUMBER() OVER (PARTITION BY muatpa.user_id::VARCHAR, cem.entity_level_1_id_comprehensive ORDER BY uoi.order_item_created_date_pt ASC) = 1
            ),
            -- Extract unique NTB customers acquired during period
            cohort_users AS (
                SELECT DISTINCT
                    user_id,
                    entity_level_1_id_comprehensive
                FROM ntb_cohort_identification
            ),
            -- Summarize acquisition cohort size and initial ROAS
            acquisition_period_summary AS (
                SELECT
                    entity_level_1_id_comprehensive,
                    COUNT(DISTINCT user_id) AS cohort_size,
                    COALESCE(SUM(attributed_sales_usd), 0) AS acq_period_sales,
                    COALESCE(SUM(sp_spend_usd), 0) AS acq_period_spend
                FROM ntb_cohort_identification
                GROUP BY entity_level_1_id_comprehensive
            ),
            -- Capture all brand purchases by cohort users after acquisition period ends
            all_subsequent_purchases AS (
                SELECT
                    cu.user_id,
                    cu.entity_level_1_id_comprehensive,
                    aoi.delivered_date_pt,
                    COALESCE(aoi.final_charge_amt_usd, 0) AS order_amount,
                    DATEDIFF(day, :v_acq_end, aoi.delivered_date_pt) AS days_since_acq_end
                FROM cohort_users cu
                INNER JOIN instadata.etl.agg_ma_order_item_daily_v2 aoi
                    ON cu.user_id::VARCHAR = aoi.user_id::VARCHAR
                    AND aoi.delivered_entity_level_1_id::VARCHAR = cu.entity_level_1_id_comprehensive::VARCHAR
                    AND aoi.delivered_date_pt > :v_acq_end
                    AND aoi.delivered_date_pt <= :v_followup_end_date
                    AND aoi.country_id = :v_country_id
                WHERE 1 = 1
            ),
            -- Bucket purchases into monthly time intervals for cohort tracking
            time_bucketed_purchases AS (
                SELECT
                    user_id,
                    entity_level_1_id_comprehensive,
                    CASE
                        WHEN days_since_acq_end <= 30 THEN 'Month 1'
                        WHEN days_since_acq_end <= 60 THEN 'Month 2'
                        WHEN days_since_acq_end <= 90 THEN 'Month 3'
                        WHEN days_since_acq_end <= 120 THEN 'Month 4'
                        WHEN days_since_acq_end <= 150 THEN 'Month 5'
                        WHEN days_since_acq_end <= 180 THEN 'Month 6'
                        WHEN days_since_acq_end <= 365 THEN 'Month 12'
                        ELSE 'Beyond 12 months'
                    END AS time_bucket,
                    delivered_date_pt,
                    order_amount
                FROM all_subsequent_purchases
                WHERE 1 = 1
                    AND days_since_acq_end <= :v_followup_days
            ),
            -- Aggregate users and sales within each time bucket
            cumulative_sales_by_bucket AS (
                SELECT
                    entity_level_1_id_comprehensive,
                    time_bucket,
                    COUNT(DISTINCT user_id) AS users_in_bucket,
                    COALESCE(SUM(order_amount), 0) AS bucket_sales
                FROM time_bucketed_purchases
                WHERE 1 = 1
                GROUP BY entity_level_1_id_comprehensive, time_bucket
            ),
            -- Cumulative rollup across buckets to measure total LTV progression
            cumulative_aggregation AS (
                SELECT
                    entity_level_1_id_comprehensive,
                    time_bucket,
                    COALESCE(SUM(bucket_sales), 0) AS cumulative_sales,
                    COALESCE(SUM(users_in_bucket), 0) AS cumulative_users
                FROM cumulative_sales_by_bucket
                WHERE 1 = 1
                GROUP BY entity_level_1_id_comprehensive, time_bucket
            ),
            -- Output type 1: Summary-level metrics for cohort during acquisition period
            cohort_summary_output AS (
                SELECT
                    'COHORT_SUMMARY' AS output_type,
                    CONCAT(
                        DATE_TRUNC('quarter', :v_acq_start),
                        ' NTB via SP - ',
                        :v_category_name
                    ) AS cohort_label,
                    aps.cohort_size,
                    aps.acq_period_sales AS acquisition_period_sales,
                    aps.acq_period_spend AS acquisition_period_spend,
                    DIV0(aps.acq_period_sales, aps.acq_period_spend) AS initial_roas,
                    CAST(NULL AS VARCHAR) AS time_bucket,
                    CAST(NULL AS DECIMAL(18,2)) AS cumulative_sales,
                    CAST(NULL AS DECIMAL(10,4)) AS cumulative_roas,
                    CAST(NULL AS DECIMAL(10,4)) AS pct_cohort_retained,
                    CAST(NULL AS DECIMAL(18,2)) AS incremental_sales_this_period
                FROM acquisition_period_summary aps
                WHERE 1 = 1
                    AND aps.entity_level_1_id_comprehensive = :v_brand_id
            ),
            -- Output type 2: Monthly breakdown showing LTV progression, ROAS, and retention rate
            cohort_over_time_output AS (
                SELECT
                    'COHORT_OVER_TIME' AS output_type,
                    CONCAT(
                        DATE_TRUNC('quarter', :v_acq_start),
                        ' NTB via SP - ',
                        :v_category_name
                    ) AS cohort_label,
                    aps.cohort_size,
                    aps.acq_period_sales AS acquisition_period_sales,
                    aps.acq_period_spend AS acquisition_period_spend,
                    DIV0(aps.acq_period_sales, aps.acq_period_spend) AS initial_roas,
                    csb.time_bucket,
                    ca.cumulative_sales,
                    DIV0(ca.cumulative_sales, aps.acq_period_spend) AS cumulative_roas,
                    DIV0(ca.cumulative_users * 100.0, aps.cohort_size) AS pct_cohort_retained,
                    COALESCE(csb.bucket_sales, 0) AS incremental_sales_this_period
                FROM acquisition_period_summary aps
                LEFT JOIN cumulative_sales_by_bucket csb
                    ON aps.entity_level_1_id_comprehensive = csb.entity_level_1_id_comprehensive
                LEFT JOIN cumulative_aggregation ca
                    ON aps.entity_level_1_id_comprehensive = ca.entity_level_1_id_comprehensive
                    AND csb.time_bucket = ca.time_bucket
                WHERE 1 = 1
                    AND aps.entity_level_1_id_comprehensive = :v_brand_id
            )
            SELECT
                output_type::VARCHAR AS output_type,
                cohort_label::VARCHAR AS cohort_label,
                cohort_size::INTEGER AS cohort_size,
                acquisition_period_sales::DECIMAL(18,2) AS acquisition_period_sales,
                acquisition_period_spend::DECIMAL(18,2) AS acquisition_period_spend,
                initial_roas::DECIMAL(10,4) AS initial_roas,
                time_bucket::VARCHAR AS time_bucket,
                cumulative_sales::DECIMAL(18,2) AS cumulative_sales,
                cumulative_roas::DECIMAL(10,4) AS cumulative_roas,
                pct_cohort_retained::DECIMAL(10,4) AS pct_cohort_retained,
                incremental_sales_this_period::DECIMAL(18,2) AS incremental_sales_this_period
            FROM cohort_summary_output
            WHERE 1 = 1
            UNION ALL
            SELECT
                output_type::VARCHAR,
                cohort_label::VARCHAR,
                cohort_size::INTEGER,
                acquisition_period_sales::DECIMAL(18,2),
                acquisition_period_spend::DECIMAL(18,2),
                initial_roas::DECIMAL(10,4),
                time_bucket::VARCHAR,
                cumulative_sales::DECIMAL(18,2),
                cumulative_roas::DECIMAL(10,4),
                pct_cohort_retained::DECIMAL(10,4),
                incremental_sales_this_period::DECIMAL(18,2)
            FROM cohort_over_time_output
            WHERE 1 = 1
            ORDER BY cohort_label, output_type DESC, time_bucket ASC
        );
        RETURN TABLE(res);
    END;
$$;
