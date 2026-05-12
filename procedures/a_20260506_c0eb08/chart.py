"""Generated from analyses/q2/chart.ipynb on promotion.

Renders the cumulative brand-repeat trajectory for a promo-acquired
cohort, split into two views:
  - chart_1.png : Overall (all 3 segments summed -> all SUAS redeemers)
  - chart_2.png : NTB only (NTC + Prev. Competitor Only)

Each chart is a bar+line combo on twin axes:
  - Bars (left axis, IC_DARKGREEN) : cumulative Brand Repeat Rate (%).
  - Line (right axis, ACCENT_ORANGE, with markers) : cumulative
    Brand Repeat Sales ($).

Data is loaded by CALLing
SANDBOX_DB.DANIELHAN.a_20260506_c0eb08 via
instaquery (no CSV dependency at runtime).
"""

from pathlib import Path

import instaquery as iq
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter

# --- Brand colors (from chart_styles.md drop-in block) ---
IC_DARKGREEN = "#003d29"   # Default fills, headlines, primary lines
IC_GREEN     = "#0aad0a"   # The ONE highlight color -- use sparingly
IC_BLACK     = "#343538"   # Text, axis labels, ticks
IC_BG        = "#f6f5f0"   # Cream background -- applied to figure + axes
IC_GRID      = "#598174"   # Grid lines (muted dark-green)

ACCENT_ORANGE  = "#e07c3e"
COLOR_NEGATIVE = "#d62728"
COLOR_NEUTRAL  = "#5b8def"

SERIES_COLORS = [IC_DARKGREEN, IC_GREEN, ACCENT_ORANGE, COLOR_NEUTRAL,
                 "#598174", "#8c564b"]

PHASE_TINT_1 = "#e8efe5"
PHASE_TINT_2 = "#fce8d8"

TITLE_SIZE = 14
AXIS_TITLE = 12
TICK_LABEL = 10
DATA_LABEL = 11
ANNOTATION = 11

FIGSIZE_DEFAULT = (8, 5)
FIGSIZE_WIDE    = (10, 5)
DPI = 150

plt.rcParams["font.family"] = "sans-serif"
plt.rcParams["font.sans-serif"] = ["Instacart Sans", "Arial", "Helvetica Neue", "DejaVu Sans"]


def apply_ic_style(ax, fig=None, *, grid_axis=None, transparent=True):
    if fig is not None:
        if transparent:
            fig.patch.set_alpha(0)
        else:
            fig.patch.set_facecolor(IC_BG)
    if transparent:
        ax.set_facecolor("none")
    else:
        ax.set_facecolor(IC_BG)
    ax.set_axisbelow(True)
    if grid_axis is None:
        ax.grid(False)
    else:
        ax.grid(True, axis=grid_axis, alpha=0.4, linestyle="-",
                color=IC_GRID, linewidth=0.8)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for spine in (ax.spines["left"], ax.spines["bottom"]):
        spine.set_color(IC_BLACK)
        spine.set_linewidth(1.0)
    ax.tick_params(axis="both", colors=IC_BLACK, labelsize=TICK_LABEL)


def pad_ylim_for_labels(ax, headroom_frac=0.12):
    ymin, ymax = ax.get_ylim()
    ax.set_ylim(ymin, ymax + (ymax - ymin) * headroom_frac)


def format_date_axis(ax, freq="month"):
    if freq == "month":
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    elif freq == "quarter":
        ax.xaxis.set_major_locator(mdates.MonthLocator(bymonth=[1, 4, 7, 10]))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))
    elif freq == "week":
        ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%-m/%-d/%y"))


def fmt_money(x, _=None):
    if abs(x) >= 1_000_000:
        return f"${x / 1_000_000:.1f}M"
    if abs(x) >= 1_000:
        return f"${x / 1_000:.0f}K"
    return f"${x:,.0f}"


def fmt_pct(x, _=None):
    return f"{x:.0f}%"


def save_chart(fig, path, *, transparent=True):
    if transparent:
        fig.savefig(path, dpi=DPI, bbox_inches="tight", transparent=True)
    else:
        fig.savefig(path, dpi=DPI, bbox_inches="tight",
                    facecolor=fig.get_facecolor())


# ============================================================================
# Internal helper: render one bar+line combo panel from pre-aggregated arrays.
# ============================================================================
def _render_bar_line_panel(
    weeks,           # list[float] — bucket end weeks (x-axis ticks)
    rate_vals,       # list[float] — cumulative repeat rate (% scale, e.g. 13.4)
    sales_vals,      # list[float] — cumulative repeat sales ($)
    output_path,
    figsize,
    bar_width,
    rate_axis_pad,
    sales_axis_pad,
    sales_label_y_offset_frac,
):
    x_pos = np.arange(len(weeks))
    x_labels = [f"{w:g}" for w in weeks]

    fig, ax_l = plt.subplots(figsize=figsize)

    # --- Bars: cumulative repeat rate (%) ---
    bars = ax_l.bar(
        x_pos, rate_vals,
        color=IC_DARKGREEN, width=bar_width, zorder=2,
        label="Brand Repeat Rate",
    )
    apply_ic_style(ax_l, fig)            # bar+line combo: NO grid on primary
    ax_l.set_xticks(x_pos)
    ax_l.set_xticklabels(x_labels)
    ax_l.set_xlim(-0.6, len(weeks) - 0.4)
    ax_l.yaxis.set_major_formatter(FuncFormatter(fmt_pct))
    ax_l.set_ylim(0, max(rate_vals) * rate_axis_pad)
    ax_l.set_xlabel("Weeks Since Conversion",
                    fontsize=AXIS_TITLE, fontweight="bold")
    ax_l.set_ylabel("Cumulative Brand Repeat Rate (%)",
                    fontsize=AXIS_TITLE, fontweight="bold",
                    color=IC_DARKGREEN)
    ax_l.tick_params(axis="y", colors=IC_DARKGREEN)

    for bar, v in zip(bars, rate_vals):
        ax_l.text(bar.get_x() + bar.get_width() / 2, v,
                  f"{v:.1f}%", ha="center", va="bottom",
                  fontsize=DATA_LABEL, color=IC_DARKGREEN)

    # --- Line: cumulative repeat sales ($) on twin axis ---
    ax_r = ax_l.twinx()
    ax_r.spines["top"].set_visible(False)
    ax_r.spines["right"].set_visible(False)
    ax_r.spines["left"].set_visible(False)
    line, = ax_r.plot(
        x_pos, sales_vals,
        color=ACCENT_ORANGE, linewidth=2.2,
        marker="o", markersize=6,
        markerfacecolor=ACCENT_ORANGE, markeredgecolor=ACCENT_ORANGE,
        zorder=4, label="Brand Repeat Sales",
    )
    ax_r.set_ylim(0, max(sales_vals) * sales_axis_pad)
    ax_r.yaxis.set_major_formatter(FuncFormatter(fmt_money))
    ax_r.set_ylabel("Cumulative Brand Repeat Sales ($)",
                    fontsize=AXIS_TITLE, fontweight="bold",
                    color=ACCENT_ORANGE)
    ax_r.tick_params(axis="y", colors=ACCENT_ORANGE)

    y_offset = max(sales_vals) * sales_label_y_offset_frac
    for xi, v in zip(x_pos, sales_vals):
        ax_r.text(xi, v - y_offset, fmt_money(v),
                  ha="center", va="top",
                  fontsize=DATA_LABEL, color=ACCENT_ORANGE,
                  fontweight="bold")

    ax_l.legend(handles=[bars, line], loc="upper left",
                frameon=False, fontsize=TICK_LABEL)

    fig.tight_layout()
    save_chart(fig, output_path)
    return fig


def render_chart(
    # Required positional args -- same names + order as procedure.sql params
    v_promo_campaign_ids,
    v_entity_brand_id,
    v_cohort_window_start,
    v_cohort_window_end,
    v_forward_window_days=84,
    v_bucket_size_days=14,
    v_country_id=840,
    *,
    # Scaling / formatting kwargs -- defaults reproduce the source-case PNGs
    figsize=FIGSIZE_WIDE,
    bar_width=0.6,
    rate_axis_pad=1.25,            # multiplier of max(rate) for left-axis ylim
    sales_axis_pad=1.25,           # multiplier of max(sales) for right-axis ylim
    sales_label_y_offset_frac=0.04,
    output_path_overall="chart_1.png",
    output_path_ntb="chart_2.png",
    warehouse="DEVELOPER_XL_WH",
):
    """Render the two cumulative brand-repeat charts (Overall + NTB).

    Positional args mirror the stored procedure's parameter list. Kwargs
    are scaling/formatting controls; defaults reproduce the source-case
    PNGs verbatim.

    Returns a 2-tuple of matplotlib Figures: (overall_fig, ntb_fig).
    Both PNGs are written to disk as a side effect.
    """
    # --- Pull data via the stored procedure (fully qualified -- iq.query
    # uses its own session context and won't honor any USE SCHEMA). Use
    # result_type=list because Snowflake returns CALL results in JSON
    # format, which breaks fetch_pandas_all() on recent connector versions.
    sql = (
        "CALL SANDBOX_DB.DANIELHAN.a_20260506_c0eb08("
        f"'{v_promo_campaign_ids}', "
        f"{int(v_entity_brand_id)}, "
        f"'{v_cohort_window_start}'::DATE, "
        f"'{v_cohort_window_end}'::DATE, "
        f"{int(v_forward_window_days)}, "
        f"{int(v_bucket_size_days)}, "
        f"{int(v_country_id)}"
        ")"
    )
    conn = iq.get_conn("snowflake", conn_params={"warehouse": warehouse})
    try:
        cur = conn.cursor()
        cur.execute(sql)
        columns = [d[0].upper() for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=columns)
    finally:
        conn.close()

    # Ensure numeric coercion (CALL/JSON path may return Decimal).
    for col in ("WEEKS_SINCE_CONVERSION", "COHORT_SIZE",
                "N_REPEATERS_THROUGH_BUCKET", "BRAND_REPEAT_RATE_PCT",
                "BRAND_REPEAT_SALES_USD"):
        df[col] = pd.to_numeric(df[col])

    weeks = sorted(df["WEEKS_SINCE_CONVERSION"].unique())

    # Pivot wide on segment so we can sum across segment subsets.
    piv = df.pivot(
        index="WEEKS_SINCE_CONVERSION",
        columns="SEGMENT",
        values=["N_REPEATERS_THROUGH_BUCKET", "BRAND_REPEAT_SALES_USD",
                "COHORT_SIZE"],
    ).reindex(weeks)

    expected_segments = ("NTC", "Prev. Competitor Only", "Existing Users")
    for seg in expected_segments:
        if (("COHORT_SIZE", seg) not in piv.columns):
            # Empty segment -- fill with zeros so summation still works.
            piv[("COHORT_SIZE", seg)] = 0
            piv[("N_REPEATERS_THROUGH_BUCKET", seg)] = 0
            piv[("BRAND_REPEAT_SALES_USD", seg)] = 0

    # --- Overall (all 3 segments) ---
    total_size = int(
        piv[("COHORT_SIZE", "NTC")].iloc[0]
        + piv[("COHORT_SIZE", "Prev. Competitor Only")].iloc[0]
        + piv[("COHORT_SIZE", "Existing Users")].iloc[0]
    )
    overall_n = (
        piv[("N_REPEATERS_THROUGH_BUCKET", "NTC")].values
        + piv[("N_REPEATERS_THROUGH_BUCKET", "Prev. Competitor Only")].values
        + piv[("N_REPEATERS_THROUGH_BUCKET", "Existing Users")].values
    )
    overall_rate = (overall_n / total_size * 100) if total_size else np.zeros_like(overall_n, dtype=float)
    overall_sales = (
        piv[("BRAND_REPEAT_SALES_USD", "NTC")].values
        + piv[("BRAND_REPEAT_SALES_USD", "Prev. Competitor Only")].values
        + piv[("BRAND_REPEAT_SALES_USD", "Existing Users")].values
    ).astype(float)

    # --- NTB (NTC + Prev. Competitor Only) ---
    ntb_size = int(
        piv[("COHORT_SIZE", "NTC")].iloc[0]
        + piv[("COHORT_SIZE", "Prev. Competitor Only")].iloc[0]
    )
    ntb_n = (
        piv[("N_REPEATERS_THROUGH_BUCKET", "NTC")].values
        + piv[("N_REPEATERS_THROUGH_BUCKET", "Prev. Competitor Only")].values
    )
    ntb_rate = (ntb_n / ntb_size * 100) if ntb_size else np.zeros_like(ntb_n, dtype=float)
    ntb_sales = (
        piv[("BRAND_REPEAT_SALES_USD", "NTC")].values
        + piv[("BRAND_REPEAT_SALES_USD", "Prev. Competitor Only")].values
    ).astype(float)

    overall_fig = _render_bar_line_panel(
        weeks=weeks,
        rate_vals=overall_rate.tolist(),
        sales_vals=overall_sales.tolist(),
        output_path=output_path_overall,
        figsize=figsize,
        bar_width=bar_width,
        rate_axis_pad=rate_axis_pad,
        sales_axis_pad=sales_axis_pad,
        sales_label_y_offset_frac=sales_label_y_offset_frac,
    )
    ntb_fig = _render_bar_line_panel(
        weeks=weeks,
        rate_vals=ntb_rate.tolist(),
        sales_vals=ntb_sales.tolist(),
        output_path=output_path_ntb,
        figsize=figsize,
        bar_width=bar_width,
        rate_axis_pad=rate_axis_pad,
        sales_axis_pad=sales_axis_pad,
        sales_label_y_offset_frac=sales_label_y_offset_frac,
    )

    return overall_fig, ntb_fig


# SMOKE-TEST ENTRY ONLY -- pinned SAMPLE CALL args so the promotion-time
# operator can verify chart.py reproduces the source PNGs. Future reuse
# callers MUST `import` render_chart and call it with their own params;
# do NOT run `python chart.py` from a reuse context (it will silently
# render the SOURCE case's chart in your bundle folder).
if __name__ == "__main__":
    here = Path(__file__).parent
    render_chart(
        v_promo_campaign_ids=(
            "5ae514c3-332d-4668-b654-862d95cf755e,"
            "16998f0f-578d-411b-8021-eb278004f772,"
            "381681f9-6c43-4d5a-81d3-13495fec75de"
        ),
        v_entity_brand_id=564770,
        v_cohort_window_start="2025-08-05",
        v_cohort_window_end="2025-10-11",
        v_forward_window_days=84,
        v_bucket_size_days=14,
        v_country_id=840,
        output_path_overall=str(here / "chart_1.png"),
        output_path_ntb=str(here / "chart_2.png"),
    )
    print("wrote chart_1.png and chart_2.png")
