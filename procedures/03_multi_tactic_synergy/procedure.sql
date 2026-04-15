USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- PROCEDURE: MULTI_TACTIC_SYNERGY
-- PURPOSE: Measure synergy effects of multi-channel ad combinations (SP + Display) on reach, conversion, and basket metrics.
-- SAMPLE CALL: CALL MULTI_TACTIC_SYNERGY(45678, 'Frozen Foods', '2025-10-01', '2025-12-31', 365, 840);

CREATE OR REPLACE PROCEDURE MULTI_TACTIC_SYNERGY(
    brand_id INTEGER,
    category_name VARCHAR,
    period_start DATE,
    period_end DATE,
    ntb_lookback_days INTEGER DEFAULT 365,
    country_id INTEGER DEFAULT 840
)
RETURNS TABLE(tactic_combination VARCHAR, unique_users_reached INTEGER, pct_increase_vs_sp_only DECIMAL(18,4), pct_of_daily_category_buyers DECIMAL(18,4), conversion_rate_index DECIMAL(18,4), avg_basket_value DECIMAL(18,4), avg_units_per_order DECIMAL(18,4), pct_ntb DECIMAL(18,4))
LANGUAGE SQL
AS
$$
DECLARE
    v_brand_id INTEGER := brand_id;
    v_category_name VARCHAR := category_name;
    v_period_start DATE := period_start;
    v_period_end DATE := period_end;
    v_ntb_lookback_days INTEGER := ntb_lookback_days;
    v_country_id INTEGER := country_id;
    res RESULTSET;
BEGIN

    -- Stage 1: Build foundation tables for campaign and account mapping
    CREATE OR REPLACE TEMPORARY TABLE temp_campaigns AS
    SELECT DISTINCT
        c.id::VARCHAR as campaign_id,
        c.account_id::VARCHAR as account_id
    FROM rds.ads_production.campaigns c
    WHERE 1 = 1
        AND c.exchange_name IS NULL
        AND c.id IS NOT NULL;

    CREATE OR REPLACE TEMPORARY TABLE temp_account_mapping AS
    SELECT DISTINCT
        m.account_id::VARCHAR as account_id,
        m.entity_level_1_id_comprehensive::VARCHAR as entity_level_1_id
    FROM instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings m
    WHERE 1 = 1
        AND m.account_id IS NOT NULL
        AND m.entity_level_1_id_comprehensive = :v_brand_id;

    -- Filter campaigns to selected brand only
    CREATE OR REPLACE TEMPORARY TABLE temp_filtered_campaigns AS
    SELECT DISTINCT
        c.campaign_id,
        am.entity_level_1_id
    FROM temp_campaigns c
    INNER JOIN temp_account_mapping am
        ON c.account_id = am.account_id
    WHERE 1 = 1;

    -- Stage 2: Capture user-level ad exposures with attributed orders
    CREATE OR REPLACE TEMPORARY TABLE temp_ad_exposures AS
    SELECT
        a.user_id::VARCHAR as user_id,
        a.attributable_event_date_time_pt::DATE as click_date,
        CASE
            WHEN a.ad_group_type = 'FEATURED_PRODUCT' THEN 'SP'
            WHEN a.ad_group_type = 'display' THEN 'Display'
            ELSE 'Other'
        END as tactic,
        a.campaign_id::VARCHAR as campaign_id,
        a.order_id::VARCHAR as order_id,
        a.order_item_product_id::VARCHAR as order_item_product_id,
        a.attributed_sales_nanos_usd,
        a.order_item_created_date_time_pt::DATE as order_date
    FROM ads.ads_dwh.multi_touch_click_prioritized_ads_attributions a
    WHERE 1 = 1
        AND a.attributable_event_date_time_pt::DATE >= :v_period_start
        AND a.attributable_event_date_time_pt::DATE <= :v_period_end
        AND a.user_id IS NOT NULL
        AND a.campaign_id IS NOT NULL
        AND a.ad_group_type IN ('FEATURED_PRODUCT', 'display')
        AND EXISTS (
            SELECT 1
            FROM temp_filtered_campaigns tfc
            WHERE tfc.campaign_id = a.campaign_id::VARCHAR
        );

    -- Classify each user by tactic mix (SP only, Display only, or both)
    CREATE OR REPLACE TEMPORARY TABLE temp_user_tactics AS
    SELECT
        user_id,
        LISTAGG(DISTINCT tactic, ', ') WITHIN GROUP (ORDER BY tactic) as tactics_exposed,
        COUNT(DISTINCT tactic) as tactic_count,
        MAX(CASE WHEN tactic = 'SP' THEN 1 ELSE 0 END) as has_sp,
        MAX(CASE WHEN tactic = 'Display' THEN 1 ELSE 0 END) as has_display
    FROM temp_ad_exposures
    WHERE 1 = 1
    GROUP BY user_id;

    -- Label tactic combinations for reach analysis
    CREATE OR REPLACE TEMPORARY TABLE temp_tactic_combinations AS
    SELECT
        user_id,
        CASE
            WHEN has_sp = 1 AND has_display = 0 THEN 'SP Only'
            WHEN has_sp = 0 AND has_display = 1 THEN 'Display Only'
            WHEN has_sp = 1 AND has_display = 1 THEN 'SP + Display'
            ELSE 'Other'
        END as tactic_combination
    FROM temp_user_tactics
    WHERE 1 = 1
        AND (has_sp = 1 OR has_display = 1);

    -- Join exposures to actual orders and assign tactic combination
    CREATE OR REPLACE TEMPORARY TABLE temp_orders_with_tactic AS
    SELECT
        ae.user_id,
        ae.order_id,
        ae.order_date,
        tc.tactic_combination,
        oio.delivered_date_pt,
        oio.final_charge_amt_usd,
        oio.picked_quantity
    FROM temp_ad_exposures ae
    INNER JOIN temp_tactic_combinations tc
        ON ae.user_id = tc.user_id
    INNER JOIN instadata.etl.agg_ma_order_item_daily_v2 oio
        ON ae.user_id = oio.user_id::VARCHAR
        AND ae.order_id = oio.order_id::VARCHAR
        AND oio.delivered_date_pt >= :v_period_start
        AND oio.delivered_date_pt <= :v_period_end
        AND oio.delivered_entity_level_1_id = :v_brand_id
        AND oio.delivered_entity_category = :v_category_name
    WHERE 1 = 1
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ae.order_id, ae.user_id ORDER BY ae.order_date DESC) = 1;

    -- Enrich orders with new-to-brand flag for cohort analysis
    CREATE OR REPLACE TEMPORARY TABLE temp_orders_with_ntb AS
    SELECT
        owt.user_id,
        owt.order_id,
        owt.order_date,
        owt.tactic_combination,
        owt.delivered_date_pt,
        owt.final_charge_amt_usd,
        owt.picked_quantity,
        COALESCE(nti.new_to_brand_365_day, 0) as is_ntb
    FROM temp_orders_with_tactic owt
    LEFT JOIN ads.ads_dwh.unified_order_item_ntx nti
        ON owt.user_id = nti.user_id::VARCHAR
        AND owt.order_id = nti.order_id::VARCHAR
        AND nti.order_item_created_date_pt >= :v_period_start
        AND nti.order_item_created_date_pt <= :v_period_end
    WHERE 1 = 1
        AND owt.delivered_date_pt >= :v_period_start
        AND owt.delivered_date_pt <= :v_period_end;

    -- Stage 3: Compute reach metrics by tactic combination
    CREATE OR REPLACE TEMPORARY TABLE temp_reach_by_combination AS
    SELECT
        tactic_combination,
        COUNT(DISTINCT user_id) as unique_users_reached
    FROM temp_tactic_combinations
    WHERE 1 = 1
    GROUP BY tactic_combination;

    -- Extract SP-only baseline for synergy calculation
    CREATE OR REPLACE TEMPORARY TABLE temp_sp_only_reach AS
    SELECT
        COALESCE(COUNT(DISTINCT user_id), 0) as sp_only_reach
    FROM temp_tactic_combinations
    WHERE 1 = 1
        AND tactic_combination = 'SP Only';

    -- Get category-level buyer universe for reach penetration calculation
    CREATE OR REPLACE TEMPORARY TABLE temp_daily_category_buyers AS
    SELECT
        COUNT(DISTINCT user_id) as daily_category_buyer_count
    FROM instadata.etl.agg_ma_order_item_daily_v2
    WHERE 1 = 1
        AND delivered_date_pt >= :v_period_start
        AND delivered_date_pt <= :v_period_end
        AND delivered_entity_category = :v_category_name
        AND country_id = :v_country_id;

    -- Stage 4: Calculate conversion and basket metrics by tactic
    CREATE OR REPLACE TEMPORARY TABLE temp_conversions_by_combination AS
    SELECT
        tactic_combination,
        COUNT(DISTINCT user_id) as converting_users,
        COUNT(DISTINCT order_id) as order_count,
        COALESCE(SUM(final_charge_amt_usd), 0) as total_value,
        COALESCE(SUM(picked_quantity), 0) as total_units,
        COUNT(DISTINCT CASE WHEN is_ntb = 1 THEN user_id END) as ntb_user_count,
        COUNT(DISTINCT user_id) as user_count
    FROM temp_orders_with_ntb
    WHERE 1 = 1
    GROUP BY tactic_combination;

    -- Compute reach metrics with synergy uplift vs. SP-only
    CREATE OR REPLACE TEMPORARY TABLE temp_final_reach AS
    SELECT
        rc.tactic_combination,
        rc.unique_users_reached,
        ROUND(
            ((rc.unique_users_reached - (SELECT sp_only_reach FROM temp_sp_only_reach)) / NULLIF((SELECT sp_only_reach FROM temp_sp_only_reach), 0)) * 100.0,
            4
        ) as pct_increase_vs_sp_only,
        ROUND(
            (rc.unique_users_reached / NULLIF((SELECT daily_category_buyer_count FROM temp_daily_category_buyers), 0)) * 100.0,
            4
        ) as pct_of_daily_category_buyers
    FROM temp_reach_by_combination rc
    WHERE 1 = 1;

    -- Calculate conversion rate and basket metrics
    CREATE OR REPLACE TEMPORARY TABLE temp_final_conversions AS
    SELECT
        tactic_combination,
        ROUND(
            DIV0(order_count, user_count) * 100.0,
            4
        ) as conversion_rate_pct,
        ROUND(
            DIV0(total_value, order_count),
            4
        ) as avg_basket_value,
        ROUND(
            DIV0(total_units, order_count),
            4
        ) as avg_units_per_order,
        ROUND(
            DIV0(ntb_user_count, user_count) * 100.0,
            4
        ) as pct_ntb
    FROM temp_conversions_by_combination
    WHERE 1 = 1;

    -- Index conversion rate relative to best performer (baseline 100)
    CREATE OR REPLACE TEMPORARY TABLE temp_conversion_index AS
    SELECT
        fc.tactic_combination,
        ROUND(
            DIV0(fc.conversion_rate_pct, MAX(fc.conversion_rate_pct) OVER ()) * 100.0,
            4
        ) as conversion_rate_index
    FROM temp_final_conversions fc
    WHERE 1 = 1;

    -- Stage 5: Final union of reach, conversion, and ntb metrics
    res := (SELECT
        fr.tactic_combination::VARCHAR as tactic_combination,
        fr.unique_users_reached::INTEGER as unique_users_reached,
        COALESCE(fr.pct_increase_vs_sp_only, 0.0000)::DECIMAL(18,4) as pct_increase_vs_sp_only,
        COALESCE(fr.pct_of_daily_category_buyers, 0.0000)::DECIMAL(18,4) as pct_of_daily_category_buyers,
        COALESCE(ci.conversion_rate_index, 0.0000)::DECIMAL(18,4) as conversion_rate_index,
        COALESCE(fc.avg_basket_value, 0.0000)::DECIMAL(18,4) as avg_basket_value,
        COALESCE(fc.avg_units_per_order, 0.0000)::DECIMAL(18,4) as avg_units_per_order,
        COALESCE(fc.pct_ntb, 0.0000)::DECIMAL(18,4) as pct_ntb
    FROM temp_final_reach fr
    LEFT JOIN temp_conversion_index ci
        ON fr.tactic_combination = ci.tactic_combination
    LEFT JOIN temp_final_conversions fc
        ON fr.tactic_combination = fc.tactic_combination
    WHERE 1 = 1
    ORDER BY fr.unique_users_reached DESC);

    RETURN TABLE(res);
END;
$$;
