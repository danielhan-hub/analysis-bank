USE ROLE IC_ENG_ROLE;
USE SCHEMA SANDBOX_DB.DANIELHAN;
USE WAREHOUSE DEVELOPER_XL_WH;

----------------------------------------------------------------------------
-- Procedure: SANDBOX_DB.DANIELHAN.ad_driven_cohort_brand_repeat_by_segment
--
-- Purpose: For users whose FIRST ad-driven brand purchase falls in a cohort
--   window, track cumulative brand repeat behavior over a configurable
--   forward observation window, bucketed at a configurable cadence
--   (default 12 weeks / 2-week buckets).
--
--   "Ad-driven" is the UNION of two configurable cohort sources:
--     (A) SP + Display attribution: rows in
--         ads.ads_dwh.multi_touch_click_prioritized_ads_attributions whose
--         joined order_item lands on the target brand. Toggled by
--         v_include_sp_display_attribution (default TRUE).
--     (B) SUAS (Spend & Save) redemption: VALID rows in
--         ads.ads_dwh.fact_spend_promotion_redemption against any of the
--         caller-supplied campaign_id UUIDs (resolved inline to
--         discount_policy_ids via nexus_coupons). Toggled by
--         v_include_suas_redemption (default TRUE).
--   Setting one flag FALSE narrows the cohort to the other source only;
--   both TRUE matches the q2b "any-ad-product" definition.
--
--   Cohort users are split into three mutually exclusive segments by their
--   NTX flags on the cohort-defining order's brand items:
--     - 'NTC'                     : NTB AND NTC on a brand item of the
--                                   cohort order (truly net-new buyer).
--     - 'Prev. Competitor Only'   : NTB AND NOT NTC (had bought the
--                                   category from competitors, never the
--                                   brand).
--     - 'Existing Users'          : NOT NTB on the cohort order (or no NTX
--                                   row found - treated as existing,
--                                   conservative assumption).
--   NTB = NTC + Prev. Competitor Only (derive at viz time by summation;
--   not emitted separately).
--
--   The NTX lookback window is selected via v_ntx_lookback_days in
--   {182, 365}; this controls whether new_to_brand_182_day /
--   new_to_category_182_day or new_to_brand_365_day /
--   new_to_category_365_day columns are read from
--   unified_order_item_ntx. Default is 182 (q2b convention).
--
--   v_include_existing_users (default TRUE) controls whether the
--   Existing Users segment is emitted. Set FALSE to restrict the output
--   to NTB-only segments (q2b convention).
--
--   Two metrics per (segment, bucket), both cumulative through end-of-bucket:
--     - brand_repeat_rate_pct    = (cohort users in segment with >=1 brand
--                                   order in days 0..bucket_end_day) /
--                                   segment_cohort_size
--     - brand_repeat_sales_usd   = SUM of those users' brand-order
--                                   final_charge_amt_usd in days
--                                   0..bucket_end_day
--
--   "Repeat" = ANY brand order (promo or not) EXCEPT the cohort-defining
--   order itself (anti-join on order_id, not on date - a same-day separate
--   brand order still counts as a repeat).
--
-- Output shape: one row per (segment, bucket). With defaults
--   (v_forward_window_days=84, v_bucket_size_days=14,
--   v_include_existing_users=TRUE) -> 18 rows
--   (3 segments x 6 week-end buckets at weeks 2, 4, 6, 8, 10, 12).
--   With v_include_existing_users=FALSE -> 12 rows (q2b shape).
--
-- Constraints:
--   * v_forward_window_days SHOULD be a multiple of v_bucket_size_days;
--     otherwise the last partial bucket is dropped from the grid.
--   * v_promo_campaign_ids is a comma-separated list of nexus_coupons
--     campaign_id UUIDs (no spaces). The procedure resolves them inline
--     to discount_policy_ids - no hardcoded discount_policy_id list.
--     Pass an empty string ('') if v_include_suas_redemption=FALSE.
--   * Forward window is fully observable: callers must ensure
--     v_cohort_window_end + v_forward_window_days <= today, otherwise
--     late-cohort users have truncated observation.
--   * Country scope is enforced via dim_warehouse.country_id (US = 840,
--     CA = 124).
--   * v_ntx_lookback_days must be 182 or 365 (other values fall through
--     and treat all cohort users as Existing).
--
-- SAMPLE CALL:
-- CALL SANDBOX_DB.DANIELHAN.ad_driven_cohort_brand_repeat_by_segment(
--     '5ae514c3-332d-4668-b654-862d95cf755e,16998f0f-578d-411b-8021-eb278004f772,381681f9-6c43-4d5a-81d3-13495fec75de',
--     564770,
--     '2025-10-01'::DATE,
--     '2025-12-31'::DATE,
--     84,
--     14,
--     840,
--     182,
--     FALSE,
--     TRUE,
--     TRUE
-- );
----------------------------------------------------------------------------

CREATE OR REPLACE PROCEDURE SANDBOX_DB.DANIELHAN.ad_driven_cohort_brand_repeat_by_segment(
    v_promo_campaign_ids              STRING,
    v_entity_brand_id                 BIGINT,
    v_cohort_window_start             DATE,
    v_cohort_window_end               DATE,
    v_forward_window_days             INTEGER  DEFAULT 84,
    v_bucket_size_days                INTEGER  DEFAULT 14,
    v_country_id                      BIGINT   DEFAULT 840,
    v_ntx_lookback_days               INTEGER  DEFAULT 182,
    v_include_existing_users          BOOLEAN  DEFAULT TRUE,
    v_include_sp_display_attribution  BOOLEAN  DEFAULT TRUE,
    v_include_suas_redemption         BOOLEAN  DEFAULT TRUE
)
RETURNS TABLE(
    segment                       VARCHAR,
    weeks_since_conversion        FLOAT,
    cohort_size                   NUMBER,
    n_repeaters_through_bucket    NUMBER,
    brand_repeat_rate_pct         FLOAT,
    brand_repeat_sales_usd        FLOAT
)
LANGUAGE SQL
AS
$$
DECLARE
    res RESULTSET DEFAULT (
        WITH
        -- ====================================================================
        -- 1) Parse the input campaign-ID list (CSV of nexus_coupons UUIDs).
        --    Empty input yields 0 rows -> SUAS leg returns nothing, which is
        --    the intended behavior when v_include_suas_redemption=FALSE.
        -- ====================================================================
        promo_campaign_ids AS (
            SELECT TRIM(value)::VARCHAR AS campaign_id
            FROM TABLE(STRTOK_SPLIT_TO_TABLE(:v_promo_campaign_ids, ','))
            WHERE NULLIF(TRIM(value), '') IS NOT NULL
        ),

        -- ====================================================================
        -- 2) ID resolution: derive SUAS discount_policy_ids inline.
        -- ====================================================================
        promo_discount_policies AS (
            SELECT DISTINCT
                discount_policy_id
            FROM instadata.rds_ads.nexus_coupons
            WHERE 1 = 1
              AND campaign_id IN (SELECT campaign_id FROM promo_campaign_ids)
        ),

        -- ====================================================================
        -- 3a) SP+Display ad-attributed brand orders in cohort window.
        --     multi_touch_click_prioritized_ads_attributions covers SP +
        --     Display only (SUAS not present). Attribution table user_id is
        --     VARCHAR -- JOIN by order_id::VARCHAR + order_item_id::VARCHAR
        --     to v2, and pull v2.user_id (NUMBER) so all downstream joins
        --     use the v2 user_id consistently. Brand/country filter applied
        --     via v2 + dim_warehouse. Gated by
        --     v_include_sp_display_attribution.
        -- ====================================================================
        ad_attributed_orders AS (
            SELECT DISTINCT
                v2.user_id           AS user_id,
                v2.order_id          AS order_id,
                v2.delivered_date_pt AS delivered_date_pt
            FROM ads.ads_dwh.multi_touch_click_prioritized_ads_attributions a
            INNER JOIN instadata.etl.agg_ma_order_item_daily_v2 v2
                ON  v2.order_id::VARCHAR      = a.order_id::VARCHAR
                AND v2.order_item_id::VARCHAR = a.order_item_id::VARCHAR
            INNER JOIN (SELECT DISTINCT partner_id, country_id FROM instadata.dwh.dim_warehouse) w
                ON v2.partner_id = w.partner_id
            WHERE 1 = 1
              AND :v_include_sp_display_attribution = TRUE
              AND v2.delivered_entity_brand_id = :v_entity_brand_id
              AND w.country_id                 = :v_country_id
              AND v2.delivered_date_pt         BETWEEN :v_cohort_window_start AND :v_cohort_window_end
              AND a.attributable_event_date_time_pt::DATE BETWEEN
                  DATEADD(day, -90, :v_cohort_window_start) AND :v_cohort_window_end
              AND a.order_item_created_date_time_pt::DATE BETWEEN
                  :v_cohort_window_start AND :v_cohort_window_end
        ),

        -- ====================================================================
        -- 3b) SUAS-redemption brand orders in cohort window.
        --     fact_spend_promotion_redemption already at user x order grain.
        --     user_id matches v2.user_id (NUMBER). Gated by
        --     v_include_suas_redemption.
        -- ====================================================================
        suas_redemption_orders AS (
            SELECT
                user_id,
                order_id,
                delivered_date_pt
            FROM ads.ads_dwh.fact_spend_promotion_redemption
            WHERE 1 = 1
              AND :v_include_suas_redemption = TRUE
              AND overall_status     = 'VALID'
              AND delivered_date_pt  BETWEEN :v_cohort_window_start AND :v_cohort_window_end
              AND discount_policy_id IN (SELECT discount_policy_id FROM promo_discount_policies)
        ),

        -- ====================================================================
        -- 3c) UNION: all candidate ad-driven cohort orders in the window.
        -- ====================================================================
        candidate_orders AS (
            SELECT user_id, order_id, delivered_date_pt FROM ad_attributed_orders
            UNION
            SELECT user_id, order_id, delivered_date_pt FROM suas_redemption_orders
        ),

        -- ====================================================================
        -- 4) Cohort: each user's FIRST candidate ad-driven order in window.
        --    Tiebreaker: smallest order_id within the same delivered date.
        -- ====================================================================
        cohort AS (
            SELECT
                user_id,
                order_id          AS cohort_order_id,
                delivered_date_pt AS cohort_date
            FROM candidate_orders
            QUALIFY ROW_NUMBER() OVER (
                PARTITION BY user_id
                ORDER BY delivered_date_pt ASC, order_id ASC
            ) = 1
        ),

        -- ====================================================================
        -- 5) NTB/NTC flags per cohort user.
        --    Pull NTX flags for brand items on each user's cohort order,
        --    aggregated to user grain. Inner subquery restricts NTX rows
        --    to brand items only via INNER JOINs to v2 (entity_brand_id)
        --    and dim_warehouse (country). Outer LEFT JOIN ensures users
        --    with no matching brand NTX rows default to f_ntb=0, f_ntc=0
        --    (treated as Existing Users -- conservative).
        --
        --    NTX lookback (182 vs 365) is selected via :v_ntx_lookback_days.
        --    ntx.user_id is VARCHAR (UUID); v2.user_id is NUMBER -- cast
        --    both sides per dd_general gotcha. Partition filter on
        --    ntx.order_item_created_date_pt always included per dd gotcha.
        -- ====================================================================
        cohort_ntx AS (
            SELECT
                cc.user_id,
                cc.cohort_order_id,
                cc.cohort_date,
                COALESCE(MAX(
                    CASE
                        WHEN :v_ntx_lookback_days = 182 THEN IFF(brand_ntx.new_to_brand_182_day = TRUE, 1, 0)
                        WHEN :v_ntx_lookback_days = 365 THEN IFF(brand_ntx.new_to_brand_365_day = TRUE, 1, 0)
                        ELSE 0
                    END
                ), 0) AS f_ntb,
                COALESCE(MAX(
                    CASE
                        WHEN :v_ntx_lookback_days = 182 THEN IFF(brand_ntx.new_to_category_182_day = TRUE, 1, 0)
                        WHEN :v_ntx_lookback_days = 365 THEN IFF(brand_ntx.new_to_category_365_day = TRUE, 1, 0)
                        ELSE 0
                    END
                ), 0) AS f_ntc
            FROM cohort cc
            LEFT JOIN (
                SELECT
                    ntx.user_id,
                    ntx.order_id,
                    ntx.new_to_brand_182_day,
                    ntx.new_to_category_182_day,
                    ntx.new_to_brand_365_day,
                    ntx.new_to_category_365_day
                FROM ads.ads_dwh.unified_order_item_ntx ntx
                INNER JOIN instadata.etl.agg_ma_order_item_daily_v2 v2
                    ON  v2.user_id::VARCHAR       = ntx.user_id::VARCHAR
                    AND v2.order_id::VARCHAR      = ntx.order_id::VARCHAR
                    AND v2.order_item_id::VARCHAR = ntx.order_item_id::VARCHAR
                INNER JOIN (SELECT DISTINCT partner_id, country_id FROM instadata.dwh.dim_warehouse) w
                    ON v2.partner_id = w.partner_id
                WHERE 1 = 1
                  AND v2.delivered_entity_brand_id   = :v_entity_brand_id
                  AND w.country_id                   = :v_country_id
                  AND ntx.order_item_created_date_pt BETWEEN :v_cohort_window_start AND :v_cohort_window_end
                  AND v2.delivered_date_pt           BETWEEN :v_cohort_window_start AND :v_cohort_window_end
            ) brand_ntx
                ON  brand_ntx.user_id::VARCHAR  = cc.user_id::VARCHAR
                AND brand_ntx.order_id::VARCHAR = cc.cohort_order_id::VARCHAR
            GROUP BY cc.user_id, cc.cohort_order_id, cc.cohort_date
        ),

        -- ====================================================================
        -- 6) Segment label: NTC / Prev. Competitor Only / Existing Users.
        --    Existing Users dropped here when v_include_existing_users=FALSE
        --    (q2b NTB-only convention).
        -- ====================================================================
        cohort_labeled AS (
            SELECT
                user_id,
                cohort_order_id,
                cohort_date,
                CASE
                    WHEN f_ntb = 1 AND f_ntc = 1 THEN 'NTC'
                    WHEN f_ntb = 1 AND f_ntc = 0 THEN 'Prev. Competitor Only'
                    ELSE                               'Existing Users'
                END AS segment
            FROM cohort_ntx
            WHERE 1 = 1
              AND (:v_include_existing_users = TRUE OR f_ntb = 1)
        ),

        -- ====================================================================
        -- 7) Forward-window brand purchases per cohort user, carrying segment.
        --    Anti-join on cohort_order_id (not on date). Country scope via
        --    dim_warehouse.
        -- ====================================================================
        forward_brand_purchases AS (
            SELECT
                c.user_id,
                c.segment,
                o.order_id,
                o.delivered_date_pt,
                DATEDIFF(day, c.cohort_date, o.delivered_date_pt)  AS days_since,
                SUM(o.final_charge_amt_usd)                         AS order_brand_sales_usd
            FROM cohort_labeled c
            INNER JOIN instadata.etl.agg_ma_order_item_daily_v2 o
                ON o.user_id = c.user_id
            INNER JOIN (SELECT DISTINCT partner_id, country_id FROM instadata.dwh.dim_warehouse) w
                ON o.partner_id = w.partner_id
            WHERE 1 = 1
              AND o.delivered_entity_brand_id = :v_entity_brand_id
              AND w.country_id                = :v_country_id
              AND o.order_id                 != c.cohort_order_id
              AND o.delivered_date_pt        >= c.cohort_date
              AND o.delivered_date_pt        <= DATEADD(day, :v_forward_window_days, c.cohort_date)
            GROUP BY c.user_id, c.segment, o.order_id, o.delivered_date_pt,
                     DATEDIFF(day, c.cohort_date, o.delivered_date_pt)
        ),

        -- ====================================================================
        -- 8) Bucket assignment: ceil(days_since / bucket_size). Day 0 falls
        --    into bucket 1 (cohort-day same-day separate brand orders count).
        -- ====================================================================
        forward_purchases_bucketed AS (
            SELECT
                user_id,
                segment,
                order_id,
                order_brand_sales_usd,
                days_since,
                LEAST(
                    GREATEST(CEIL(days_since::FLOAT / :v_bucket_size_days), 1),
                    :v_forward_window_days / :v_bucket_size_days
                )::INTEGER AS bucket_id
            FROM forward_brand_purchases
            WHERE days_since BETWEEN 0 AND :v_forward_window_days
        ),

        -- ====================================================================
        -- 9) Bucket grid (1..N) and per-segment cohort sizes.
        --    Bucket end is reported as weeks_since_conversion (FLOAT) so
        --    sub-week buckets (e.g. 7d) and multi-week buckets (e.g. 14d, 30d)
        --    all render cleanly on the chart x-axis.
        -- ====================================================================
        bucket_grid AS (
            SELECT
                (idx + 1)                                                       AS bucket_id,
                ((idx + 1) * :v_bucket_size_days)::INTEGER                      AS days_through_bucket,
                ((idx + 1) * :v_bucket_size_days / 7.0)::FLOAT                  AS weeks_since_conversion
            FROM (
                SELECT SEQ4() AS idx
                FROM TABLE(GENERATOR(ROWCOUNT => 10000))
            )
            WHERE (idx + 1) <= (:v_forward_window_days / :v_bucket_size_days)
        ),

        -- Hardcode segment names so all expected segments always appear,
        -- even if empty. 'Existing Users' is conditionally included based
        -- on v_include_existing_users.
        segments AS (
            SELECT 'NTC'                   AS segment
            UNION ALL SELECT 'Prev. Competitor Only'
            UNION ALL SELECT 'Existing Users' WHERE :v_include_existing_users = TRUE
        ),

        segment_bucket_grid AS (
            SELECT s.segment, bg.bucket_id, bg.weeks_since_conversion
            FROM segments s
            CROSS JOIN bucket_grid bg
        ),

        cohort_size_by_segment AS (
            SELECT segment, COUNT(*) AS n
            FROM cohort_labeled
            GROUP BY segment
        ),

        -- ====================================================================
        -- 10) Cumulative aggregation: for each segment x bucket N, count
        --     distinct cohort users with any qualifying purchase in buckets
        --     <= N and sum their sales.
        -- ====================================================================
        cumulative_per_bucket AS (
            SELECT
                g.segment,
                g.weeks_since_conversion,
                COUNT(DISTINCT p.user_id)                  AS n_repeaters_through_bucket,
                COALESCE(SUM(p.order_brand_sales_usd), 0)  AS sales_through_bucket_usd
            FROM segment_bucket_grid g
            LEFT JOIN forward_purchases_bucketed p
                ON  p.segment   = g.segment
                AND p.bucket_id <= g.bucket_id
            GROUP BY g.segment, g.weeks_since_conversion
        )

        -- ====================================================================
        -- 11) FINAL: emit one row per (segment, bucket).
        -- ====================================================================
        SELECT
            cpb.segment                                                         AS segment,
            cpb.weeks_since_conversion::FLOAT                                   AS weeks_since_conversion,
            COALESCE(cs.n, 0)                                                   AS cohort_size,
            cpb.n_repeaters_through_bucket                                      AS n_repeaters_through_bucket,
            DIV0(cpb.n_repeaters_through_bucket, cs.n)::FLOAT                   AS brand_repeat_rate_pct,
            cpb.sales_through_bucket_usd::FLOAT                                 AS brand_repeat_sales_usd
        FROM cumulative_per_bucket cpb
        LEFT JOIN cohort_size_by_segment cs
            ON cpb.segment = cs.segment
        ORDER BY
            CASE cpb.segment
                WHEN 'NTC'                   THEN 1
                WHEN 'Prev. Competitor Only' THEN 2
                WHEN 'Existing Users'        THEN 3
            END,
            cpb.weeks_since_conversion
    );
BEGIN
    RETURN TABLE(res);
END;
$$;
