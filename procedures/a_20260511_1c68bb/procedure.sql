USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;
USE WAREHOUSE DEVELOPER_XL_WH;

----------------------------------------------------------------------------
-- Procedure: SANDBOX_DB.DANIELHAN.a_20260511_1c68bb
-- Purpose:   For an SP advertiser (account_id), define a clicker cohort
--            over a cohort window, then compute bucketed and cumulative
--            SP-attributed sales, SP click spend, and ROAS for that
--            cohort across the full chart window.
--
-- Methodology:
--   D1: Re-resolve SP campaign IDs at query time via campaigns_records
--       (overlap with the chart window, workflow_state='active').
--   D2: Anchor on account_id + campaign_type — derive campaign_ids inside
--       SQL; no hardcoded IN-list, no name filters.
--   Cohort: distinct users with a billable SP click on ANY of the resolved
--       campaigns DURING the cohort window (a subset of the chart window).
--   Sales:  agg_ma_order_item_daily_v2 filtered to (cohort users) ×
--           (products bought from those campaigns) over the chart window.
--   Spend:  consolidated_conversions billable click spend by the cohort
--           on the campaigns over the chart window.
--   Bucket: DATE_TRUNC(:v_bucket, ...) — accepts 'month', 'quarter', 'week',
--           or any Snowflake date_or_time_part. Cumulative columns are
--           running sums over the bucket order.
--
-- Output columns (FLOAT-cast to satisfy RETURNS TABLE strict typing):
--   bucket            -- DATE; first day of the bucket
--   bucket_sales      -- SP-attributed sales in the bucket (cohort users)
--   bucket_spend      -- SP click spend in the bucket (cohort users)
--   cumulative_sales  -- running SUM of bucket_sales
--   cumulative_spend  -- running SUM of bucket_spend
--   cumulative_roas   -- cumulative_sales / cumulative_spend (DIV0-safe)
--
-- SAMPLE CALL:
--   CALL SANDBOX_DB.DANIELHAN.a_20260511_1c68bb(
--       45,                          -- v_account_id (Vital Farms)
--       '2025-07-01'::DATE,          -- v_chart_start
--       '2025-12-31'::DATE,          -- v_chart_end
--       '2025-07-01'::DATE,          -- v_cohort_start
--       '2025-09-30'::DATE,          -- v_cohort_end (Q3 2025 clickers)
--       'quarter',                   -- v_bucket
--       'featured_product'           -- v_campaign_type
--   );
----------------------------------------------------------------------------

CREATE OR REPLACE PROCEDURE SANDBOX_DB.DANIELHAN.a_20260511_1c68bb(
    v_account_id    BIGINT,
    v_chart_start   DATE,
    v_chart_end     DATE,
    v_cohort_start  DATE,
    v_cohort_end    DATE,
    v_bucket        VARCHAR DEFAULT 'quarter',
    v_campaign_type VARCHAR DEFAULT 'featured_product'
)
RETURNS TABLE (
    bucket           DATE,
    bucket_sales     FLOAT,
    bucket_spend     FLOAT,
    cumulative_sales FLOAT,
    cumulative_spend FLOAT,
    cumulative_roas  FLOAT
)
LANGUAGE SQL
AS
$$
DECLARE
    v_campaign_ids STRING;
    res            RESULTSET;
BEGIN
    -- Step 1: Resolve campaign IDs dynamically (D1/D2).
    -- Overlap predicate captures any campaign active at any point in the chart window.
    SELECT LISTAGG(DISTINCT after:id::BIGINT, ',') WITHIN GROUP (ORDER BY after:id::BIGINT)
      INTO :v_campaign_ids
    FROM rds.ads_production.campaigns_records
    WHERE 1 = 1
      AND after:account_id::BIGINT     = :v_account_id
      AND after:campaign_type::STRING  = :v_campaign_type
      AND after:workflow_state::STRING = 'active'
      AND current_from <= :v_chart_end::TIMESTAMP
      AND COALESCE(current_to, CURRENT_DATE()::TIMESTAMP) >= :v_chart_start::TIMESTAMP;

    -- Step 2: Cohort + bucketed cumulative ROAS calculation.
    res := (
        WITH campaign_ids AS (
            SELECT TRY_CAST(value AS BIGINT) AS campaign_id
            FROM TABLE(STRTOK_SPLIT_TO_TABLE(:v_campaign_ids, ','))
        ),

        campaign_products AS (
            -- Products that received SP impressions/clicks on the target
            -- campaigns during the chart window (defines the SP-attributed
            -- product universe for sales tracking).
            SELECT DISTINCT afd.product_id
            FROM ads.ads_dwh.agg_featured_product_daily afd
            INNER JOIN campaign_ids ci ON afd.campaign_id = ci.campaign_id
            WHERE 1 = 1
              AND afd.event_date_pt BETWEEN :v_chart_start AND :v_chart_end
        ),

        clickers AS (
            -- Cohort: distinct users with a billable SP click on any target
            -- campaign within the cohort window only. Partition prune on utc;
            -- correctness on pt.
            SELECT DISTINCT cc.user_id
            FROM ads.ads_dwh.consolidated_conversions cc
            INNER JOIN campaign_ids ci ON cc.campaign_id = ci.campaign_id
            WHERE 1 = 1
              AND cc.event_date_time_utc  BETWEEN :v_cohort_start::TIMESTAMP
                                              AND DATEADD(day, 1, :v_cohort_end::TIMESTAMP)
              AND TO_DATE(cc.event_date_time_pt) BETWEEN :v_cohort_start AND :v_cohort_end
              AND cc.charged_nanos_usd    > 0
              AND cc.event_name           = 'click.click_featured_product'
        ),

        sales_per_bucket AS (
            -- Sales by the cohort on the campaigns' products, bucketed over
            -- the chart window.
            SELECT
                DATE_TRUNC(:v_bucket, aoi.delivered_date_pt) AS bucket,
                SUM(aoi.final_charge_amt_usd)::FLOAT         AS sales
            FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
            INNER JOIN clickers          c ON aoi.user_id            = c.user_id
            INNER JOIN campaign_products p ON aoi.ordered_product_id = p.product_id
            WHERE 1 = 1
              AND aoi.delivered_date_pt BETWEEN :v_chart_start AND :v_chart_end
            GROUP BY 1
        ),

        spend_per_bucket AS (
            -- SP click spend by the cohort on the campaigns, bucketed over
            -- the chart window. (Cohort sees only their own clicks counted.)
            SELECT
                DATE_TRUNC(:v_bucket, TO_DATE(cc.event_date_time_pt)) AS bucket,
                SUM(cc.charged_nanos_usd * 0.000000001)::FLOAT        AS spend
            FROM ads.ads_dwh.consolidated_conversions cc
            INNER JOIN campaign_ids ci ON cc.campaign_id = ci.campaign_id
            INNER JOIN clickers      c  ON cc.user_id    = c.user_id
            WHERE 1 = 1
              AND cc.event_date_time_utc  BETWEEN :v_chart_start::TIMESTAMP
                                              AND DATEADD(day, 1, :v_chart_end::TIMESTAMP)
              AND TO_DATE(cc.event_date_time_pt) BETWEEN :v_chart_start AND :v_chart_end
              AND cc.charged_nanos_usd    > 0
              AND cc.event_name           = 'click.click_featured_product'
            GROUP BY 1
        )

        SELECT
            sb.bucket::DATE                                                          AS bucket,
            sb.sales::FLOAT                                                          AS bucket_sales,
            COALESCE(spb.spend, 0)::FLOAT                                            AS bucket_spend,
            (SUM(sb.sales)              OVER (ORDER BY sb.bucket))::FLOAT            AS cumulative_sales,
            (SUM(COALESCE(spb.spend,0)) OVER (ORDER BY sb.bucket))::FLOAT            AS cumulative_spend,
            DIV0(
                SUM(sb.sales)              OVER (ORDER BY sb.bucket),
                SUM(COALESCE(spb.spend,0)) OVER (ORDER BY sb.bucket)
            )::FLOAT                                                                 AS cumulative_roas
        FROM sales_per_bucket sb
        LEFT JOIN spend_per_bucket spb ON sb.bucket = spb.bucket
        ORDER BY sb.bucket
    );
    RETURN TABLE(res);
END;
$$;
