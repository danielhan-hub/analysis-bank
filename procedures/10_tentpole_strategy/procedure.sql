USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;

-- Procedure: TENTPOLE_STRATEGY
-- Purpose: Track brand performance, media mix, and new customer acquisition across pre/tentpole/post periods
-- CALL TENTPOLE_STRATEGY('27028', 'SOFT DRINKS', 'Labor Day 2025', '2025-08-20', '2025-09-07', 30, 30, 840)

CREATE OR REPLACE PROCEDURE TENTPOLE_STRATEGY(
    brand_id VARCHAR,
    category_name VARCHAR,
    tentpole_name VARCHAR,
    tentpole_start DATE,
    tentpole_end DATE,
    pre_period_days INTEGER DEFAULT 30,
    post_period_days INTEGER DEFAULT 30,
    country_id INTEGER DEFAULT 840
)
RETURNS TABLE (
    week_start DATE,
    period_label VARCHAR,
    category_share DECIMAL(10, 4),
    total_ad_spend DECIMAL(18, 2),
    sp_spend DECIMAL(18, 2),
    display_spend DECIMAL(18, 2),
    other_tactic_spend DECIMAL(18, 2),
    paid_impression_share DECIMAL(10, 4),
    new_customers INTEGER
)
LANGUAGE SQL
AS
$$
    DECLARE
        v_brand_id VARCHAR := brand_id;
        v_category_name VARCHAR := category_name;
        v_tentpole_name VARCHAR := tentpole_name;
        v_tentpole_start DATE := tentpole_start;
        v_tentpole_end DATE := tentpole_end;
        v_pre_days INTEGER := pre_period_days;
        v_post_days INTEGER := post_period_days;
        v_country_id INTEGER := country_id;
        v_pre_start DATE;
        v_post_end DATE;
        res RESULTSET;
    BEGIN
        v_pre_start := DATEADD(day, -v_pre_days, v_tentpole_start);
        v_post_end := DATEADD(day, v_post_days, v_tentpole_end);

        res := (
            -- Define pre/tentpole/post period windows
            WITH period_definitions AS (
                SELECT
                    :v_pre_start AS pre_period_start,
                    DATEADD(day, -1, :v_tentpole_start) AS pre_period_end,
                    :v_tentpole_start AS tentpole_period_start,
                    :v_tentpole_end AS tentpole_period_end,
                    DATEADD(day, 1, :v_tentpole_end) AS post_period_start,
                    :v_post_end AS post_period_end
            ),
            -- Get all category sales across entire period
            all_category_sales AS (
                SELECT
                    aoi.delivered_date_pt,
                    aoi.delivered_entity_level_1_id,
                    aoi.delivered_entity_category,
                    COALESCE(SUM(aoi.final_charge_amt_usd), 0) AS daily_sales
                FROM instadata.etl.agg_ma_order_item_daily_v2 aoi
                WHERE 1 = 1
                    AND aoi.country_id = :v_country_id
                    AND aoi.delivered_date_pt >= (SELECT pre_period_start FROM period_definitions)
                    AND aoi.delivered_date_pt <= (SELECT post_period_end FROM period_definitions)
                    AND aoi.delivered_entity_category = :v_category_name
                GROUP BY
                    aoi.delivered_date_pt,
                    aoi.delivered_entity_level_1_id,
                    aoi.delivered_entity_category
            ),
            -- Filter target brand from category sales
            brand_sales AS (
                SELECT
                    delivered_date_pt,
                    delivered_entity_level_1_id,
                    COALESCE(SUM(daily_sales), 0) AS brand_daily_sales
                FROM all_category_sales
                WHERE 1 = 1
                    AND delivered_entity_level_1_id = :v_brand_id
                GROUP BY
                    delivered_date_pt,
                    delivered_entity_level_1_id
            ),
            -- Total category sales by day
            category_daily_totals AS (
                SELECT
                    delivered_date_pt,
                    COALESCE(SUM(daily_sales), 0) AS category_daily_sales
                FROM all_category_sales
                WHERE 1 = 1
                GROUP BY delivered_date_pt
            ),
            -- Sponsored products spend for brand campaigns
            sp_spend_daily AS (
                SELECT
                    afd.event_date_pt,
                    afd.campaign_id,
                    COALESCE(SUM(afd.billable_spend_usd), 0) AS sp_daily_spend
                FROM ads.ads_dwh.agg_featured_product_daily afd
                INNER JOIN rds.ads_production.campaigns c
                    ON afd.campaign_id::VARCHAR = c.id::VARCHAR
                    AND c.campaign_type = 'featured_product'
                    AND c.exchange_name IS NULL
                INNER JOIN instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings cem
                    ON c.account_id = cem.account_id
                WHERE 1 = 1
                    AND afd.event_date_pt >= (SELECT pre_period_start FROM period_definitions)
                    AND afd.event_date_pt <= (SELECT post_period_end FROM period_definitions)
                    AND cem.entity_level_1_id_comprehensive = :v_brand_id
                GROUP BY
                    afd.event_date_pt,
                    afd.campaign_id
            ),
            -- Aggregate SP spend by day
            sp_spend_brand AS (
                SELECT
                    event_date_pt,
                    COALESCE(SUM(sp_daily_spend), 0) AS sp_brand_daily_spend
                FROM sp_spend_daily
                WHERE 1 = 1
                GROUP BY event_date_pt
            ),
            -- Display spend for brand campaigns
            display_spend_daily AS (
                SELECT
                    add2.event_date_pt,
                    add2.campaign_id,
                    COALESCE(SUM(add2.ad_spend_usd), 0) AS display_daily_spend
                FROM instadata.etl.agg_display_daily_v2_ma add2
                INNER JOIN rds.ads_production.campaigns c
                    ON add2.campaign_id::VARCHAR = c.id::VARCHAR
                    AND c.campaign_type = 'display'
                    AND c.exchange_name IS NULL
                INNER JOIN instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings cem
                    ON c.account_id = cem.account_id
                WHERE 1 = 1
                    AND add2.event_date_pt >= (SELECT pre_period_start FROM period_definitions)
                    AND add2.event_date_pt <= (SELECT post_period_end FROM period_definitions)
                    AND cem.entity_level_1_id_comprehensive = :v_brand_id
                GROUP BY
                    add2.event_date_pt,
                    add2.campaign_id
            ),
            -- Aggregate display spend by day
            display_spend_brand AS (
                SELECT
                    event_date_pt,
                    COALESCE(SUM(display_daily_spend), 0) AS display_brand_daily_spend
                FROM display_spend_daily
                WHERE 1 = 1
                GROUP BY event_date_pt
            ),
            -- Brand impression share
            impression_share_daily AS (
                SELECT
                    tao.event_date_pt,
                    tao.campaign_id,
                    COALESCE(SUM(tao.won_viewable_block), 0) AS won_impressions,
                    COALESCE(SUM(tao.eligible_viewable_block), 0) AS eligible_impressions
                FROM ADS.ADS_DWH.TOTAL_AUCTION_OPPORTUNITY tao
                INNER JOIN rds.ads_production.campaigns c
                    ON tao.campaign_id::VARCHAR = c.id::VARCHAR
                    AND c.exchange_name IS NULL
                INNER JOIN instadata.etl.dim_ma_account_entity_salesforce_comprehensive_mappings cem
                    ON c.account_id = cem.account_id
                WHERE 1 = 1
                    AND tao.event_date_pt >= (SELECT pre_period_start FROM period_definitions)
                    AND tao.event_date_pt <= (SELECT post_period_end FROM period_definitions)
                    AND cem.entity_level_1_id_comprehensive = :v_brand_id
                GROUP BY
                    tao.event_date_pt,
                    tao.campaign_id
            ),
            -- Aggregate paid impression share by day
            impression_share_brand AS (
                SELECT
                    event_date_pt,
                    DIV0(COALESCE(SUM(won_impressions), 0), COALESCE(SUM(eligible_impressions), 1)) AS daily_pis
                FROM impression_share_daily
                WHERE 1 = 1
                GROUP BY event_date_pt
            ),
            -- New-to-brand customer count by day
            ntb_daily AS (
                SELECT
                    uoi.order_item_created_date_pt,
                    COALESCE(SUM(CASE WHEN uoi.new_to_brand_365_day = TRUE THEN 1 ELSE 0 END), 0) AS new_to_brand_count
                FROM ads.ads_dwh.unified_order_item_ntx uoi
                INNER JOIN instadata.etl.agg_ma_order_item_daily_v2 aoi
                    ON uoi.order_id::VARCHAR = aoi.order_id::VARCHAR
                    AND uoi.user_id::VARCHAR = aoi.user_id::VARCHAR
                    AND uoi.order_item_created_date_pt = aoi.delivered_date_pt
                WHERE 1 = 1
                    AND aoi.country_id = :v_country_id
                    AND uoi.order_item_created_date_pt >= (SELECT pre_period_start FROM period_definitions)
                    AND uoi.order_item_created_date_pt <= (SELECT post_period_end FROM period_definitions)
                    AND aoi.delivered_date_pt >= (SELECT pre_period_start FROM period_definitions)
                    AND aoi.delivered_date_pt <= (SELECT post_period_end FROM period_definitions)
                    AND aoi.delivered_entity_level_1_id = :v_brand_id
                GROUP BY uoi.order_item_created_date_pt
            ),
            -- Consolidate all daily metrics into single timeline
            daily_combined AS (
                SELECT
                    COALESCE(bs.delivered_date_pt, spb.event_date_pt, dsb.event_date_pt, isb.event_date_pt, ntb.order_item_created_date_pt) AS event_date_pt,
                    COALESCE(bs.brand_daily_sales, 0) AS brand_sales,
                    COALESCE(cdt.category_daily_sales, 0) AS category_sales,
                    COALESCE(spb.sp_brand_daily_spend, 0) AS sp_spend,
                    COALESCE(dsb.display_brand_daily_spend, 0) AS display_spend,
                    COALESCE(spb.sp_brand_daily_spend, 0) + COALESCE(dsb.display_brand_daily_spend, 0) AS total_spend,
                    COALESCE(isb.daily_pis, 0) AS pis,
                    COALESCE(ntb.new_to_brand_count, 0) AS ntb_count
                FROM brand_sales bs
                FULL OUTER JOIN category_daily_totals cdt ON bs.delivered_date_pt = cdt.delivered_date_pt
                FULL OUTER JOIN sp_spend_brand spb ON COALESCE(bs.delivered_date_pt, cdt.delivered_date_pt) = spb.event_date_pt
                FULL OUTER JOIN display_spend_brand dsb ON COALESCE(bs.delivered_date_pt, cdt.delivered_date_pt, spb.event_date_pt) = dsb.event_date_pt
                FULL OUTER JOIN impression_share_brand isb ON COALESCE(bs.delivered_date_pt, cdt.delivered_date_pt, spb.event_date_pt, dsb.event_date_pt) = isb.event_date_pt
                FULL OUTER JOIN ntb_daily ntb ON COALESCE(bs.delivered_date_pt, cdt.delivered_date_pt, spb.event_date_pt, dsb.event_date_pt, isb.event_date_pt) = ntb.order_item_created_date_pt
                WHERE 1 = 1
                    AND COALESCE(bs.delivered_date_pt, cdt.delivered_date_pt, spb.event_date_pt, dsb.event_date_pt, isb.event_date_pt, ntb.order_item_created_date_pt)
                        >= (SELECT pre_period_start FROM period_definitions)
                    AND COALESCE(bs.delivered_date_pt, cdt.delivered_date_pt, spb.event_date_pt, dsb.event_date_pt, isb.event_date_pt, ntb.order_item_created_date_pt)
                        <= (SELECT post_period_end FROM period_definitions)
            ),
            -- Aggregate by week and period
            weekly_aggregated AS (
                SELECT
                    DATE_TRUNC('week', dc.event_date_pt) AS week_start,
                    CASE
                        WHEN dc.event_date_pt >= (SELECT pre_period_start FROM period_definitions)
                         AND dc.event_date_pt < (SELECT tentpole_period_start FROM period_definitions) THEN 'Pre'
                        WHEN dc.event_date_pt >= (SELECT tentpole_period_start FROM period_definitions)
                         AND dc.event_date_pt <= (SELECT tentpole_period_end FROM period_definitions) THEN 'Tentpole'
                        WHEN dc.event_date_pt > (SELECT tentpole_period_end FROM period_definitions)
                         AND dc.event_date_pt <= (SELECT post_period_end FROM period_definitions) THEN 'Post'
                    END AS period_label,
                    DIV0(COALESCE(SUM(dc.brand_sales), 0), COALESCE(SUM(dc.category_sales), 1)) AS weekly_category_share,
                    COALESCE(SUM(dc.sp_spend), 0) AS weekly_sp_spend,
                    COALESCE(SUM(dc.display_spend), 0) AS weekly_display_spend,
                    COALESCE(SUM(dc.total_spend), 0) AS weekly_total_spend,
                    COALESCE(SUM(CASE WHEN dc.total_spend > 0 THEN dc.pis * dc.total_spend ELSE 0 END), 0) / COALESCE(NULLIF(COALESCE(SUM(CASE WHEN dc.total_spend > 0 THEN dc.total_spend ELSE 0 END), 0), 0), 1) AS weekly_pis,
                    COALESCE(SUM(dc.ntb_count), 0) AS weekly_ntb
                FROM daily_combined dc
                WHERE 1 = 1
                GROUP BY
                    DATE_TRUNC('week', dc.event_date_pt),
                    CASE
                        WHEN dc.event_date_pt >= (SELECT pre_period_start FROM period_definitions)
                         AND dc.event_date_pt < (SELECT tentpole_period_start FROM period_definitions) THEN 'Pre'
                        WHEN dc.event_date_pt >= (SELECT tentpole_period_start FROM period_definitions)
                         AND dc.event_date_pt <= (SELECT tentpole_period_end FROM period_definitions) THEN 'Tentpole'
                        WHEN dc.event_date_pt > (SELECT tentpole_period_end FROM period_definitions)
                         AND dc.event_date_pt <= (SELECT post_period_end FROM period_definitions) THEN 'Post'
                    END
            )
            SELECT
                week_start::DATE AS week_start,
                period_label::VARCHAR AS period_label,
                weekly_category_share::DECIMAL(10,4) AS category_share,
                weekly_total_spend::DECIMAL(18,2) AS total_ad_spend,
                weekly_sp_spend::DECIMAL(18,2) AS sp_spend,
                weekly_display_spend::DECIMAL(18,2) AS display_spend,
                (weekly_total_spend - weekly_sp_spend - weekly_display_spend)::DECIMAL(18,2) AS other_tactic_spend,
                weekly_pis::DECIMAL(10,4) AS paid_impression_share,
                weekly_ntb::INTEGER AS new_customers
            FROM weekly_aggregated
            WHERE 1 = 1
            ORDER BY week_start ASC
        );
        RETURN TABLE(res);
    END;
$$;
