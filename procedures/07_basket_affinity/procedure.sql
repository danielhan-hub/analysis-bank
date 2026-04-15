USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- Procedure: BASKET_AFFINITY
-- Purpose: Identify high-affinity adjacent categories and untapped customer penetration opportunities for cross-sell
-- Sample CALL: CALL BASKET_AFFINITY(27028, 'SOFT DRINKS', '2025-10-01', '2025-12-31', 3, 1.2, 20, 840)

CREATE OR REPLACE PROCEDURE BASKET_AFFINITY(
  brand_id INTEGER,
  category_name VARCHAR,
  period_start DATE,
  period_end DATE,
  loyalty_threshold INTEGER DEFAULT 3,
  affinity_min_index FLOAT DEFAULT 1.2,
  top_n_categories INTEGER DEFAULT 20,
  country_id INTEGER DEFAULT 840
)
RETURNS TABLE (
  result_type VARCHAR,
  adjacent_category VARCHAR,
  loyal_purchase_rate FLOAT,
  benchmark_purchase_rate FLOAT,
  affinity_index FLOAT,
  total_repeating_category_buyers INTEGER,
  have_tried_brand INTEGER,
  have_not_tried_brand INTEGER,
  untapped_pct FLOAT
)
LANGUAGE SQL
AS
$$
DECLARE
  v_brand_id INTEGER := brand_id;
  v_category_name VARCHAR := category_name;
  v_period_start DATE := period_start;
  v_period_end DATE := period_end;
  v_loyalty_threshold INTEGER := loyalty_threshold;
  v_affinity_min_index FLOAT := affinity_min_index;
  v_top_n_categories INTEGER := top_n_categories;
  v_country_id INTEGER := country_id;
  res RESULTSET;
BEGIN

  res := (
    -- Filter all order data by country and valid order IDs
    WITH order_items_filtered AS (
      SELECT
        oif.delivered_date_pt,
        oif.user_id,
        oif.order_id,
        oif.delivered_entity_level_1_id,
        oif.delivered_entity_category,
        oif.delivered_entity_brand,
        oif.final_charge_amt_usd
      FROM instadata.etl.agg_ma_order_item_daily_v2 oif
      WHERE 1 = 1
        AND oif.country_id = :v_country_id
        AND oif.delivered_date_pt >= :v_period_start
        AND oif.delivered_date_pt <= :v_period_end
        AND REGEXP_LIKE(oif.order_id::VARCHAR, '^[0-9]+$')
    ),

    -- Identify repeat customers who meet loyalty threshold (N+ purchases of target brand)
    loyal_customers AS (
      SELECT
        user_id,
        COUNT(DISTINCT order_id) AS brand_purchase_count
      FROM order_items_filtered
      WHERE 1 = 1
        AND delivered_entity_level_1_id = :v_brand_id
      GROUP BY user_id
      HAVING COUNT(DISTINCT order_id) >= :v_loyalty_threshold
    ),

    -- Map loyal customers' purchases in adjacent categories
    loyal_customer_categories AS (
      SELECT
        lc.user_id,
        oif.delivered_entity_category,
        COUNT(DISTINCT oif.order_id) AS category_purchase_count
      FROM loyal_customers lc
      INNER JOIN order_items_filtered oif
        ON lc.user_id = oif.user_id
      WHERE 1 = 1
        AND oif.delivered_entity_level_1_id != :v_brand_id
        AND oif.delivered_entity_category != :v_category_name
      GROUP BY lc.user_id, oif.delivered_entity_category
    ),

    -- Count loyal customers purchasing in each adjacent category
    loyal_customers_who_bought_category AS (
      SELECT
        delivered_entity_category,
        COUNT(DISTINCT user_id) AS loyal_buyers_in_category
      FROM loyal_customer_categories
      GROUP BY delivered_entity_category
    ),

    -- Total loyal customer population for rate calculations
    loyal_customer_base AS (
      SELECT COUNT(DISTINCT user_id) AS total_loyal_customers
      FROM loyal_customers
    ),

    -- Population denominator: all users purchasing in each adjacent category
    all_category_buyers AS (
      SELECT
        delivered_entity_category,
        COUNT(DISTINCT user_id) AS all_category_buyers_count
      FROM order_items_filtered
      WHERE 1 = 1
        AND delivered_entity_category != :v_category_name
      GROUP BY delivered_entity_category
    ),

    -- Total user population for benchmark rate calculations
    all_users_base AS (
      SELECT COUNT(DISTINCT user_id) AS total_all_users
      FROM order_items_filtered
    ),

    -- Compute affinity index
    affinity_calculations AS (
      SELECT
        lcbc.delivered_entity_category,
        COALESCE(lcbc.loyal_buyers_in_category, 0) AS loyal_buyers_in_category,
        COALESCE(lcb.total_loyal_customers, 0) AS total_loyal_customers,
        COALESCE(acb.all_category_buyers_count, 0) AS all_category_buyers_count,
        COALESCE(aub.total_all_users, 0) AS total_all_users,
        DIV0(
          COALESCE(lcbc.loyal_buyers_in_category, 0)::FLOAT,
          COALESCE(lcb.total_loyal_customers, 0)::FLOAT
        ) AS loyal_purchase_rate,
        DIV0(
          COALESCE(acb.all_category_buyers_count, 0)::FLOAT,
          COALESCE(aub.total_all_users, 0)::FLOAT
        ) AS benchmark_purchase_rate,
        DIV0(
          COALESCE(lcbc.loyal_buyers_in_category, 0)::FLOAT,
          COALESCE(lcb.total_loyal_customers, 0)::FLOAT
        ) / NULLIF(
          DIV0(
            COALESCE(acb.all_category_buyers_count, 0)::FLOAT,
            COALESCE(aub.total_all_users, 0)::FLOAT
          ),
          0
        ) AS affinity_index
      FROM loyal_customers_who_bought_category lcbc
      CROSS JOIN loyal_customer_base lcb
      LEFT JOIN all_category_buyers acb
        ON lcbc.delivered_entity_category = acb.delivered_entity_category
      CROSS JOIN all_users_base aub
      WHERE 1 = 1
    ),

    -- Filter to top N categories by affinity index that meet minimum threshold
    top_affinity_categories AS (
      SELECT
        delivered_entity_category,
        loyal_purchase_rate,
        benchmark_purchase_rate,
        affinity_index,
        ROW_NUMBER() OVER (ORDER BY affinity_index DESC) AS rn
      FROM affinity_calculations
      WHERE 1 = 1
        AND affinity_index >= :v_affinity_min_index
      QUALIFY ROW_NUMBER() OVER (ORDER BY affinity_index DESC) <= :v_top_n_categories
    ),

    -- Measure untapped penetration
    penetration_gap_analysis AS (
      SELECT
        cat_users.delivered_entity_category,
        COUNT(DISTINCT cat_users.user_id) AS total_repeating_category_buyers,
        COUNT(DISTINCT brand_users.user_id) AS have_tried_brand,
        COUNT(DISTINCT cat_users.user_id) - COUNT(DISTINCT brand_users.user_id) AS have_not_tried_brand
      FROM (
        SELECT DISTINCT user_id, delivered_entity_category
        FROM order_items_filtered
        WHERE 1 = 1
          AND delivered_entity_category != :v_category_name
      ) cat_users
      LEFT JOIN (
        SELECT DISTINCT user_id
        FROM order_items_filtered
        WHERE 1 = 1
          AND delivered_entity_level_1_id = :v_brand_id
      ) brand_users
        ON cat_users.user_id = brand_users.user_id
      GROUP BY cat_users.delivered_entity_category
    ),

    -- Format affinity results
    affinity_output AS (
      SELECT
        'affinity_index' AS result_type,
        tac.delivered_entity_category AS adjacent_category,
        tac.loyal_purchase_rate,
        tac.benchmark_purchase_rate,
        tac.affinity_index,
        NULL::INTEGER AS total_repeating_category_buyers,
        NULL::INTEGER AS have_tried_brand,
        NULL::INTEGER AS have_not_tried_brand,
        NULL::FLOAT AS untapped_pct
      FROM top_affinity_categories tac
      WHERE 1 = 1
    ),

    -- Format penetration results
    penetration_output AS (
      SELECT
        'penetration_gap' AS result_type,
        pga.delivered_entity_category AS adjacent_category,
        NULL::FLOAT AS loyal_purchase_rate,
        NULL::FLOAT AS benchmark_purchase_rate,
        NULL::FLOAT AS affinity_index,
        pga.total_repeating_category_buyers,
        pga.have_tried_brand,
        pga.have_not_tried_brand,
        DIV0(
          pga.have_not_tried_brand::FLOAT,
          pga.total_repeating_category_buyers::FLOAT
        ) AS untapped_pct
      FROM penetration_gap_analysis pga
      WHERE 1 = 1
        AND pga.total_repeating_category_buyers > 0
    )

    SELECT
      result_type::VARCHAR AS result_type,
      adjacent_category::VARCHAR AS adjacent_category,
      loyal_purchase_rate::FLOAT AS loyal_purchase_rate,
      benchmark_purchase_rate::FLOAT AS benchmark_purchase_rate,
      affinity_index::FLOAT AS affinity_index,
      total_repeating_category_buyers::INTEGER AS total_repeating_category_buyers,
      have_tried_brand::INTEGER AS have_tried_brand,
      have_not_tried_brand::INTEGER AS have_not_tried_brand,
      untapped_pct::FLOAT AS untapped_pct
    FROM affinity_output

    UNION ALL

    SELECT
      result_type::VARCHAR,
      adjacent_category::VARCHAR,
      loyal_purchase_rate::FLOAT,
      benchmark_purchase_rate::FLOAT,
      affinity_index::FLOAT,
      total_repeating_category_buyers::INTEGER,
      have_tried_brand::INTEGER,
      have_not_tried_brand::INTEGER,
      untapped_pct::FLOAT
    FROM penetration_output
  );

  RETURN TABLE(res);
END;
$$;
