USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;
USE WAREHOUSE DEVELOPER_XL_WH;

----------------------------------------------------------------------------
-- Procedure: SANDBOX_DB.DANIELHAN.a_20260506_065c82
--
-- Purpose: Monthly cumulative SP ROAS for a *fixed cohort* of users who
--          clicked on a target set of SP campaigns during a defined
--          cohort window. Returns one row per calendar month over the
--          full chart window with monthly + cumulative sales, spend,
--          and ROAS so charts can show how returns build over time as
--          repeat / latent purchases arrive.
--
-- Cohort definition:
--   Distinct users with at least one billable SP click on ANY of the
--   listed campaign IDs between v_chart_start and v_cohort_month_end
--   (inclusive). Their downstream SP-attributed sales (purchases of
--   products featured by these campaigns, regardless of which campaign
--   ad they clicked on) and SP click spend on these campaigns are then
--   tracked across the full v_chart_start ... v_chart_end window.
--
-- Why a fixed cohort: this isolates "what does a January clicker do over
-- the next several months?" — useful for arguing that SP returns
-- compound past the click month (delivery lag, repeat purchase, basket
-- expansion). For a vanilla all-clickers ROAS, set v_cohort_month_end =
-- v_chart_end so every clicker in the window is included.
--
-- SAMPLE CALL:
-- CALL SANDBOX_DB.DANIELHAN.a_20260506_065c82(
--     '266584,297686,311207',
--     '2026-01-01'::DATE,
--     '2026-04-30'::DATE,
--     '2026-01-31'::DATE
-- );
----------------------------------------------------------------------------

CREATE OR REPLACE PROCEDURE SANDBOX_DB.DANIELHAN.a_20260506_065c82(
    v_campaign_ids       STRING,
    v_chart_start        DATE,
    v_chart_end          DATE,
    v_cohort_month_end   DATE
)
RETURNS TABLE(
    month             DATE,
    monthly_sales     FLOAT,
    monthly_spend     FLOAT,
    cumulative_sales  FLOAT,
    cumulative_spend  FLOAT,
    cumulative_roas   FLOAT
)
LANGUAGE SQL
AS
$$
DECLARE
    res RESULTSET DEFAULT (
        WITH campaign_ids AS (
            -- Parse the comma-separated campaign-id list into a single-column table.
            SELECT TRY_CAST(value AS BIGINT) AS campaign_id
            FROM TABLE(STRTOK_SPLIT_TO_TABLE(:v_campaign_ids, ','))
        ),

        campaign_products AS (
            -- Universe of products featured by any of the target campaigns
            -- during the chart window. Limits the sales join to attributable SKUs.
            SELECT DISTINCT afd.product_id
            FROM ads.ads_dwh.agg_featured_product_daily afd
            INNER JOIN campaign_ids ci ON afd.campaign_id = ci.campaign_id
            WHERE 1 = 1
              AND afd.event_date_pt BETWEEN :v_chart_start AND :v_cohort_month_end
        ),

        clickers AS (
            -- Cohort: distinct users with a billable SP click on ANY target
            -- campaign during the cohort window only. Partition prune on UTC
            -- (correctness on PT). Restricts ALL downstream sales/spend to
            -- this same user set, so cumulative ROAS reflects what THIS cohort
            -- did, not the campaign's wave-2 audience.
            SELECT DISTINCT cc.user_id
            FROM ads.ads_dwh.consolidated_conversions cc
            INNER JOIN campaign_ids ci ON cc.campaign_id = ci.campaign_id
            WHERE 1 = 1
              AND cc.event_date_time_utc  BETWEEN :v_chart_start::TIMESTAMP
                                              AND DATEADD(day, 1, :v_cohort_month_end::TIMESTAMP)
              AND TO_DATE(cc.event_date_time_pt) BETWEEN :v_chart_start AND :v_cohort_month_end
              AND cc.charged_nanos_usd    > 0
              AND cc.event_name           = 'click.click_featured_product'
        ),

        monthly_sales AS (
            -- SP-attributed sales by the cohort, by delivered month, across
            -- the full chart window. Joined to campaign_products so we only
            -- count purchases of products these campaigns featured.
            SELECT
                DATE_TRUNC('month', aoi.delivered_date_pt)::DATE AS month,
                SUM(aoi.final_charge_amt_usd)::FLOAT             AS sales
            FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
            INNER JOIN clickers          c ON aoi.user_id            = c.user_id
            INNER JOIN campaign_products p ON aoi.ordered_product_id = p.product_id
            WHERE 1 = 1
              AND aoi.delivered_date_pt BETWEEN :v_chart_start AND :v_chart_end
            GROUP BY 1
        ),

        monthly_spend AS (
            -- SP click spend by the cohort on the target campaigns, by month,
            -- across the full chart window. Spend after the cohort window is
            -- the cohort's own repeat clicks on the same campaigns.
            SELECT
                DATE_TRUNC('month', TO_DATE(cc.event_date_time_pt))::DATE AS month,
                SUM(cc.charged_nanos_usd * 0.000000001)::FLOAT            AS spend
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
            ms.month                                                              AS month,
            ms.sales                                                              AS monthly_sales,
            COALESCE(msp.spend, 0)::FLOAT                                         AS monthly_spend,
            SUM(ms.sales)               OVER (ORDER BY ms.month)::FLOAT           AS cumulative_sales,
            SUM(COALESCE(msp.spend, 0)) OVER (ORDER BY ms.month)::FLOAT           AS cumulative_spend,
            DIV0(
                SUM(ms.sales)               OVER (ORDER BY ms.month),
                SUM(COALESCE(msp.spend, 0)) OVER (ORDER BY ms.month)
            )::FLOAT                                                              AS cumulative_roas
        FROM monthly_sales ms
        LEFT JOIN monthly_spend msp ON ms.month = msp.month
        ORDER BY ms.month
    );
BEGIN
    RETURN TABLE(res);
END;
$$;
