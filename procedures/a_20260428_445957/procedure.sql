USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- Procedure: SHOPPABLE_DISPLAY_REACH
-- Purpose: Identify media channel gaps and reach optimization: search terms and users underexposed to shoppable products
-- Sample CALL: CALL SHOPPABLE_DISPLAY_REACH(27028, 'SOFT DRINKS', '2025-10-01', '2025-12-31', 840)

CREATE OR REPLACE PROCEDURE SHOPPABLE_DISPLAY_REACH(
  brand_id INT,
  category_name VARCHAR,
  period_start DATE,
  period_end DATE,
  country_id INT DEFAULT 840
)
RETURNS TABLE (output_view VARCHAR)
LANGUAGE SQL
AS
$$
DECLARE
  v_brand_id INT := brand_id;
  v_category_name VARCHAR := category_name;
  v_period_start DATE := period_start;
  v_period_end DATE := period_end;
  v_country_id INT := country_id;
  res RESULTSET;
BEGIN

  -- Aggregate shoppable product (SP) performance by search term
  CREATE OR REPLACE TEMPORARY TABLE sp_search_term_sales AS
  SELECT
    CAST(c.id AS VARCHAR) AS campaign_id,
    afp.search_term,
    SUM(afp.attributed_sales_usd) AS sp_attributed_sales_usd,
    SUM(afp.billable_spend_usd) AS sp_billable_spend_usd,
    COUNT(DISTINCT afp.event_date_pt) AS days_active
  FROM
    ads.ads_dwh.agg_featured_product_daily afp
    INNER JOIN rds.ads_production.campaigns c
      ON CAST(afp.campaign_id AS VARCHAR) = CAST(c.id AS VARCHAR)
    INNER JOIN instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings aem
      ON c.account_id = aem.account_id
  WHERE 1 = 1
    AND afp.event_date_pt >= :v_period_start
    AND afp.event_date_pt <= :v_period_end
    AND c.campaign_type = 'featured_product'
    AND c.exchange_name IS NULL
    AND aem.entity_level_1_id_comprehensive = :v_brand_id
    AND afp.search_term IS NOT NULL
    AND afp.search_term != ''
  GROUP BY
    c.id,
    afp.search_term;

  -- Aggregate display performance by search term
  CREATE OR REPLACE TEMPORARY TABLE display_search_term_sales AS
  SELECT
    CAST(c.id AS VARCHAR) AS campaign_id,
    d.search_term,
    SUM(d.halo_attributed_sales_multi_touch_click_prioritized_usd) AS display_attributed_sales_usd,
    COUNT(DISTINCT d.event_date_pt) AS days_active
  FROM
    instadata.etl.agg_display_daily_v2_ma d
    INNER JOIN rds.ads_production.campaigns c
      ON CAST(d.campaign_id AS VARCHAR) = CAST(c.id AS VARCHAR)
    INNER JOIN instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings aem
      ON c.account_id = aem.account_id
  WHERE 1 = 1
    AND d.event_date_pt >= :v_period_start
    AND d.event_date_pt <= :v_period_end
    AND c.campaign_type = 'display'
    AND c.exchange_name IS NULL
    AND aem.entity_level_1_id_comprehensive = :v_brand_id
    AND d.search_term IS NOT NULL
    AND d.search_term != ''
  GROUP BY
    c.id,
    d.search_term;

  -- Compare channel coverage to identify underserved search terms
  CREATE OR REPLACE TEMPORARY TABLE search_term_gap_analysis AS
  SELECT
    COALESCE(d.search_term, sp.search_term) AS search_term,
    COALESCE(sp.sp_attributed_sales_usd, 0) AS sp_attributed_sales_usd,
    COALESCE(d.display_attributed_sales_usd, 0) AS display_attributed_sales_usd,
    CASE
      WHEN COALESCE(d.display_attributed_sales_usd, 0) > 0
        AND COALESCE(sp.sp_attributed_sales_usd, 0) = 0
      THEN 1
      ELSE 0
    END AS is_gap_term,
    CASE
      WHEN COALESCE(sp.sp_attributed_sales_usd, 0) = 0 THEN 'Display Only'
      WHEN COALESCE(d.display_attributed_sales_usd, 0) = 0 THEN 'SP Only'
      ELSE 'Both'
    END AS term_coverage
  FROM
    sp_search_term_sales sp
    FULL OUTER JOIN display_search_term_sales d
      ON CAST(sp.search_term AS VARCHAR) = CAST(d.search_term AS VARCHAR)
  WHERE 1 = 1
    AND (COALESCE(sp.sp_attributed_sales_usd, 0) > 0
      OR COALESCE(d.display_attributed_sales_usd, 0) > 0);

  -- Track user-level exposure to both display and shoppable product campaigns
  CREATE OR REPLACE TEMPORARY TABLE user_campaign_exposure AS
  SELECT
    mtcpa.user_id,
    CAST(mtcpa.campaign_id AS VARCHAR) AS campaign_id,
    c.campaign_type,
    aem.entity_level_1_id_comprehensive AS brand_id_matched
  FROM
    ads.ads_dwh.multi_touch_click_prioritized_ads_attributions mtcpa
    INNER JOIN rds.ads_production.campaigns c
      ON CAST(mtcpa.campaign_id AS VARCHAR) = CAST(c.id AS VARCHAR)
    INNER JOIN instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings aem
      ON c.account_id = aem.account_id
  WHERE 1 = 1
    AND c.exchange_name IS NULL
    AND aem.entity_level_1_id_comprehensive = :v_brand_id
    AND mtcpa.ad_group_type IN ('FEATURED_PRODUCT', 'display')
    AND mtcpa.attributable_event_date_time_pt::DATE >= :v_period_start
    AND mtcpa.attributable_event_date_time_pt::DATE <= :v_period_end;

  -- Classify users by exposure patterns
  CREATE OR REPLACE TEMPORARY TABLE user_exposure_groups AS
  SELECT
    user_id,
    MAX(CASE WHEN campaign_type = 'display' THEN 1 ELSE 0 END) AS exposed_to_display,
    MAX(CASE WHEN campaign_type = 'featured_product' THEN 1 ELSE 0 END) AS exposed_to_sp,
    CASE
      WHEN MAX(CASE WHEN campaign_type = 'display' THEN 1 ELSE 0 END) = 1
        AND MAX(CASE WHEN campaign_type = 'featured_product' THEN 1 ELSE 0 END) = 0
      THEN 'Display Only'
      WHEN MAX(CASE WHEN campaign_type = 'featured_product' THEN 1 ELSE 0 END) = 1
        AND MAX(CASE WHEN campaign_type = 'display' THEN 1 ELSE 0 END) = 0
      THEN 'SP Only'
      WHEN MAX(CASE WHEN campaign_type = 'display' THEN 1 ELSE 0 END) = 1
        AND MAX(CASE WHEN campaign_type = 'featured_product' THEN 1 ELSE 0 END) = 1
      THEN 'Both'
      ELSE 'Unknown'
    END AS exposure_group
  FROM
    user_campaign_exposure
  GROUP BY
    user_id;

  -- Capture most recent NTB status for each user
  CREATE OR REPLACE TEMPORARY TABLE order_item_ntb_prep AS
  SELECT
    uoi.user_id,
    uoi.new_to_brand_365_day,
    ROW_NUMBER() OVER (PARTITION BY uoi.user_id ORDER BY uoi.order_item_created_date_pt DESC) AS rn
  FROM
    ads.ads_dwh.unified_order_item_ntx uoi
  WHERE 1 = 1
    AND uoi.order_item_created_date_pt >= :v_period_start
    AND uoi.order_item_created_date_pt <= :v_period_end
  QUALIFY ROW_NUMBER() OVER (PARTITION BY uoi.user_id ORDER BY uoi.order_item_created_date_pt DESC) = 1;

  -- Measure new-to-brand conversion by exposure group
  CREATE OR REPLACE TEMPORARY TABLE user_overlap_summary AS
  SELECT
    ueg.exposure_group,
    COUNT(DISTINCT ueg.user_id) AS unique_users,
    COALESCE(SUM(CASE WHEN oin.new_to_brand_365_day = TRUE THEN 1 ELSE 0 END), 0) AS ntb_users,
    COALESCE(SUM(CASE WHEN oin.new_to_brand_365_day = FALSE THEN 1 ELSE 0 END), 0) AS non_ntb_users,
    DIV0(
      COALESCE(SUM(CASE WHEN oin.new_to_brand_365_day = TRUE THEN 1 ELSE 0 END), 0),
      COUNT(DISTINCT ueg.user_id)
    ) AS pct_ntb
  FROM
    user_exposure_groups ueg
    LEFT JOIN order_item_ntb_prep oin
      ON CAST(ueg.user_id AS VARCHAR) = CAST(oin.user_id AS VARCHAR)
  WHERE 1 = 1
  GROUP BY
    ueg.exposure_group;

  -- Consolidate search and user-level gap metrics
  CREATE OR REPLACE TEMPORARY TABLE summary_metrics AS
  SELECT
    :v_brand_id AS brand_id,
    :v_category_name AS category_name,
    :v_period_start AS period_start_date,
    :v_period_end AS period_end_date,
    COALESCE(SUM(CASE WHEN stga.is_gap_term = 1 THEN stga.display_attributed_sales_usd ELSE 0 END), 0) AS total_display_gap_sales,
    COALESCE(SUM(CASE WHEN stga.term_coverage = 'Display Only' THEN stga.display_attributed_sales_usd ELSE 0 END), 0) AS display_only_term_sales,
    DIV0(
      (SELECT COUNT(DISTINCT user_id) FROM user_exposure_groups WHERE exposure_group = 'Display Only'),
      (SELECT COUNT(DISTINCT user_id) FROM user_exposure_groups WHERE exposed_to_display = 1)
    ) AS pct_display_users_not_exposed_to_sp,
    (SELECT COUNT(DISTINCT user_id) FROM user_exposure_groups WHERE exposure_group = 'Display Only') AS display_only_users,
    (SELECT COUNT(DISTINCT user_id) FROM user_exposure_groups WHERE exposed_to_display = 1) AS total_display_users
  FROM
    search_term_gap_analysis stga
  WHERE 1 = 1;

  -- Output 1: Search term opportunities
  CREATE OR REPLACE TEMPORARY TABLE RESULT_SEARCH_TERM_GAP AS
  SELECT
    search_term,
    sp_attributed_sales_usd,
    display_attributed_sales_usd,
    is_gap_term,
    term_coverage,
    RANK() OVER (ORDER BY display_attributed_sales_usd DESC) AS sales_rank
  FROM
    search_term_gap_analysis
  WHERE 1 = 1
  ORDER BY
    display_attributed_sales_usd DESC;

  -- Output 2: User audience stratification
  CREATE OR REPLACE TEMPORARY TABLE RESULT_USER_EXPOSURE_OVERLAP AS
  SELECT
    exposure_group,
    unique_users,
    ntb_users,
    non_ntb_users,
    pct_ntb
  FROM
    user_overlap_summary
  WHERE 1 = 1
  ORDER BY
    unique_users DESC;

  -- Output 3: Summary KPIs
  CREATE OR REPLACE TEMPORARY TABLE RESULT_SUMMARY_METRICS AS
  SELECT
    brand_id,
    category_name,
    period_start_date,
    period_end_date,
    total_display_gap_sales,
    display_only_term_sales,
    pct_display_users_not_exposed_to_sp,
    display_only_users,
    total_display_users,
    CASE
      WHEN total_display_users > 0 THEN 'Analysis Complete'
      ELSE 'Insufficient Data'
    END AS analysis_status
  FROM
    summary_metrics
  WHERE 1 = 1;

  res := (SELECT 'RESULT_SEARCH_TERM_GAP' AS output_view
    UNION ALL
    SELECT 'RESULT_USER_EXPOSURE_OVERLAP' AS output_view
    UNION ALL
    SELECT 'RESULT_SUMMARY_METRICS' AS output_view);

  RETURN TABLE(res);

END;
$$;
