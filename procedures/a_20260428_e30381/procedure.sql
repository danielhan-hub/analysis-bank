USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- Procedure: LIFT_TEST_INCREMENTALITY
-- Purpose: Quantify sales lift and IROAS from A/B test with 95% confidence interval
-- CALL LIFT_TEST_INCREMENTALITY('exp_12345', 67890, '2025-09-01', '2025-09-30', 0.95, 1.96, 840)

CREATE OR REPLACE PROCEDURE LIFT_TEST_INCREMENTALITY(
    experiment_id VARCHAR,
    brand_id INTEGER,
    test_start DATE,
    test_end DATE,
    confidence_level DECIMAL(10, 2) DEFAULT 0.95,
    z_score DECIMAL(10, 4) DEFAULT 1.96,
    country_id INTEGER DEFAULT 840
)
RETURNS TABLE (
    test_group_size INTEGER,
    control_group_size INTEGER,
    test_sales DECIMAL(18, 2),
    control_sales DECIMAL(18, 2),
    incremental_sales DECIMAL(18, 2),
    lift_pct DECIMAL(18, 4),
    ad_spend DECIMAL(18, 2),
    iroas DECIMAL(18, 4),
    ci_lower DECIMAL(18, 4),
    ci_upper DECIMAL(18, 4)
)
LANGUAGE SQL
AS
$$
    DECLARE
        v_experiment_id VARCHAR := experiment_id;
        v_brand_id INTEGER := brand_id;
        v_test_start DATE := test_start;
        v_test_end DATE := test_end;
        v_confidence_level DECIMAL(10, 2) := confidence_level;
        v_z_score DECIMAL(10, 4) := z_score;
        v_country_id INTEGER := country_id;
        v_is_suas BOOLEAN;
        res RESULTSET;
    BEGIN

        -- Determine experiment type (SUAS roulette vs standard LIFT/AB_V2)
        CREATE OR REPLACE TEMPORARY TABLE temp_experiment_type AS
        SELECT
            CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM INSTADATA.GSHEETS.gs_ma_suas_experiment_mapping
                    WHERE 1 = 1
                        AND experiment_id = :v_experiment_id
                ) THEN TRUE
                ELSE FALSE
            END AS is_suas;

        v_is_suas := (SELECT is_suas FROM temp_experiment_type);

        IF (v_is_suas) THEN

            -- SUAS path: Get user-level test/control assignments from roulette system
            CREATE OR REPLACE TEMPORARY TABLE temp_user_assignments AS
            SELECT
                user_id::VARCHAR AS user_id,
                CASE
                    WHEN variant = 'control' THEN 'control'
                    ELSE 'treatment'
                END AS variant,
                CASE
                    WHEN variant = 'control' THEN 0
                    ELSE 1
                END AS bucket_num
            FROM instadata.etl.ma_suas_roulette_assignments
            WHERE 1 = 1
                AND experiment = :v_experiment_id
                AND user_id IS NOT NULL;

            -- Sum ad spend for treatment group only
            CREATE OR REPLACE TEMPORARY TABLE temp_ad_spend AS
            SELECT
                COALESCE(SUM(spr.applied_amount_usd + spr.instacart_fee_usd), 0) AS total_spend
            FROM ads.ads_dwh.fact_spend_promotion_redemption spr
            INNER JOIN instadata.etl.ma_suas_roulette_assignments mra
                ON spr.user_id = mra.user_id
                AND mra.experiment = :v_experiment_id
                AND mra.variant != 'control'
            WHERE 1 = 1
                AND spr.overall_status = 'VALID'
                AND spr.delivered_date_pt >= :v_test_start
                AND spr.delivered_date_pt <= :v_test_end;

        ELSE

            -- LIFT/AB_V2 path: Fetch experiment metadata and variant mappings from Nexus
            CREATE OR REPLACE TEMPORARY TABLE temp_experiment_details AS
            SELECT
                id::VARCHAR AS experiment_id,
                experiment_name,
                experiment_type
            FROM INSTADATA.RDS_ADS.NEXUS_AB_EXPERIMENTS
            WHERE 1 = 1
                AND id::VARCHAR = :v_experiment_id
                AND experiment_type IN ('LIFT', 'AB_V2');

            -- Get all variant names for this experiment
            CREATE OR REPLACE TEMPORARY TABLE temp_variant_mapping AS
            SELECT DISTINCT
                variant
            FROM ads.ads_dwh.ads_experiment_evaluations
            WHERE 1 = 1
                AND experiment_id::VARCHAR = :v_experiment_id;

            -- Map users to test/control with bucket salt for stratified analysis
            CREATE OR REPLACE TEMPORARY TABLE temp_user_assignments AS
            SELECT
                user_id::VARCHAR AS user_id,
                CASE
                    WHEN variant IN ('Holdout', 'Control') AND ted.experiment_type = 'LIFT' THEN
                        CASE WHEN variant = 'Holdout' THEN 'control' ELSE 'treatment' END
                    WHEN variant = 'Control' AND ted.experiment_type != 'LIFT' THEN 'control'
                    ELSE 'treatment'
                END AS variant,
                MOD(ABS(HASH(user_id::VARCHAR, 'experiment_salt')), 20) AS bucket_num
            FROM ads.ads_dwh.ads_experiment_evaluations aee
            INNER JOIN temp_experiment_details ted
                ON aee.experiment_id::VARCHAR = ted.experiment_id
            WHERE 1 = 1
                AND aee.experiment_id::VARCHAR = :v_experiment_id
                AND aee.user_id IS NOT NULL;

            -- Calculate spend from experiment entities (campaigns)
            CREATE OR REPLACE TEMPORARY TABLE temp_ad_spend AS
            SELECT
                COALESCE(SUM(afd.billable_spend_usd), 0) AS total_spend
            FROM ads.ads_dwh.agg_featured_product_daily afd
            INNER JOIN INSTADATA.RDS_ADS.NEXUS_EXPERIMENT_ENTITIES nee
                ON afd.campaign_id::VARCHAR = nee.entity_id::VARCHAR
            WHERE 1 = 1
                AND nee.experiment_id::VARCHAR = :v_experiment_id
                AND afd.event_date_pt >= :v_test_start
                AND afd.event_date_pt <= :v_test_end
                AND nee.entity_type = 'CampaignWithId';

        END IF;

        -- Join users to their sales during test window; controlled by brand and country
        CREATE OR REPLACE TEMPORARY TABLE temp_user_sales AS
        SELECT
            ua.user_id,
            ua.variant,
            ua.bucket_num,
            COALESCE(SUM(aoi.final_charge_amt_usd), 0) AS user_sales
        FROM temp_user_assignments ua
        LEFT JOIN instadata.etl.agg_ma_order_item_daily_v2 aoi
            ON ua.user_id = aoi.user_id::VARCHAR
            AND aoi.delivered_date_pt >= :v_test_start
            AND aoi.delivered_date_pt <= :v_test_end
            AND aoi.delivered_entity_level_1_id = :v_brand_id
            AND aoi.country_id = :v_country_id
        WHERE 1 = 1
        GROUP BY ua.user_id, ua.variant, ua.bucket_num;

        -- Aggregate sales by variant and stratification bucket for variance calc
        CREATE OR REPLACE TEMPORARY TABLE temp_bucket_aggregation AS
        SELECT
            variant,
            bucket_num,
            COUNT(DISTINCT user_id) AS bucket_users,
            COALESCE(SUM(user_sales), 0) AS bucket_sales,
            COALESCE(DIV0(SUM(user_sales), COUNT(DISTINCT user_id)), 0) AS avg_user_sales,
            COALESCE(STDDEV_POP(user_sales), 0) AS sales_stddev,
            COUNT(DISTINCT user_id) AS n_users
        FROM temp_user_sales
        WHERE 1 = 1
        GROUP BY variant, bucket_num;

        -- Group-level aggregates: test vs control totals and means
        CREATE OR REPLACE TEMPORARY TABLE temp_group_metrics AS
        SELECT
            variant,
            COUNT(DISTINCT user_id) AS group_size,
            COALESCE(SUM(user_sales), 0) AS total_sales,
            COALESCE(DIV0(SUM(user_sales), COUNT(DISTINCT user_id)), 0) AS avg_sales_per_user,
            COALESCE(STDDEV_POP(user_sales), 0) AS group_stddev,
            COUNT(DISTINCT user_id) AS group_n
        FROM temp_user_sales
        WHERE 1 = 1
        GROUP BY variant;

        -- Bucket-level variance used for confidence interval around IROAS
        CREATE OR REPLACE TEMPORARY TABLE temp_bucket_stats AS
        SELECT
            variant,
            COUNT(DISTINCT bucket_num) AS num_buckets,
            COALESCE(AVG(bucket_sales), 0) AS mean_bucket_sales,
            COALESCE(STDDEV_POP(bucket_sales), 0) AS stddev_bucket_sales,
            COALESCE(SUM(bucket_sales), 0) AS total_variant_sales,
            COALESCE(COUNT(DISTINCT bucket_num), 0) AS bucket_count
        FROM temp_bucket_aggregation
        WHERE 1 = 1
        GROUP BY variant;

        -- Extract scalar values into variables
        LET v_test_size INTEGER := COALESCE((SELECT group_size FROM temp_group_metrics WHERE variant = 'treatment' LIMIT 1), 0);
        LET v_ctrl_size INTEGER := COALESCE((SELECT group_size FROM temp_group_metrics WHERE variant = 'control' LIMIT 1), 0);
        LET v_test_sales DECIMAL(18,2) := COALESCE((SELECT total_sales FROM temp_group_metrics WHERE variant = 'treatment' LIMIT 1), 0);
        LET v_ctrl_sales DECIMAL(18,2) := COALESCE((SELECT total_sales FROM temp_group_metrics WHERE variant = 'control' LIMIT 1), 0);
        LET v_total_spend DECIMAL(18,2) := (SELECT total_spend FROM temp_ad_spend);
        LET v_incr_sales DECIMAL(18,2) := v_test_sales - v_ctrl_sales;
        LET v_iroas DECIMAL(18,4) := DIV0(v_incr_sales, v_total_spend);

        -- Bucket-level stats for confidence interval
        LET v_treat_stddev DECIMAL(18,4) := COALESCE((SELECT stddev_bucket_sales FROM temp_bucket_stats WHERE variant = 'treatment' LIMIT 1), 0);
        LET v_treat_buckets INTEGER := COALESCE((SELECT bucket_count FROM temp_bucket_stats WHERE variant = 'treatment' LIMIT 1), 1);
        LET v_ctrl_stddev DECIMAL(18,4) := COALESCE((SELECT stddev_bucket_sales FROM temp_bucket_stats WHERE variant = 'control' LIMIT 1), 0);
        LET v_ctrl_buckets INTEGER := COALESCE((SELECT bucket_count FROM temp_bucket_stats WHERE variant = 'control' LIMIT 1), 1);

        -- Standard error of the difference in bucket-level sales
        LET v_se DECIMAL(18,4) := SQRT(
            POWER(v_treat_stddev, 2) / v_treat_buckets
            + POWER(v_ctrl_stddev, 2) / v_ctrl_buckets
        );

        CREATE OR REPLACE TEMPORARY TABLE temp_final_results AS
        SELECT
            :v_test_size AS test_group_size,
            :v_ctrl_size AS control_group_size,
            :v_test_sales AS test_sales,
            :v_ctrl_sales AS control_sales,
            :v_incr_sales AS incremental_sales,
            DIV0(:v_incr_sales * 100, :v_ctrl_sales) AS lift_pct,
            :v_total_spend AS ad_spend,
            :v_iroas AS iroas,
            :v_iroas - (:v_z_score * :v_se) AS ci_lower,
            :v_iroas + (:v_z_score * :v_se) AS ci_upper;

        DROP TABLE IF EXISTS temp_experiment_type;
        DROP TABLE IF EXISTS temp_experiment_details;
        DROP TABLE IF EXISTS temp_variant_mapping;
        DROP TABLE IF EXISTS temp_user_assignments;
        DROP TABLE IF EXISTS temp_ad_spend;
        DROP TABLE IF EXISTS temp_user_sales;
        DROP TABLE IF EXISTS temp_bucket_aggregation;
        DROP TABLE IF EXISTS temp_group_metrics;
        DROP TABLE IF EXISTS temp_bucket_stats;

        res := (
            SELECT
                test_group_size::INTEGER AS test_group_size,
                control_group_size::INTEGER AS control_group_size,
                test_sales::DECIMAL(18,2) AS test_sales,
                control_sales::DECIMAL(18,2) AS control_sales,
                incremental_sales::DECIMAL(18,2) AS incremental_sales,
                lift_pct::DECIMAL(18,4) AS lift_pct,
                ad_spend::DECIMAL(18,2) AS ad_spend,
                iroas::DECIMAL(18,4) AS iroas,
                ci_lower::DECIMAL(18,4) AS ci_lower,
                ci_upper::DECIMAL(18,4) AS ci_upper
            FROM temp_final_results
        );

        RETURN TABLE(res);

    END;
$$;
