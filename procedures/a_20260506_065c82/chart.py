"""Generated from analyses/q2b/chart.ipynb on promotion.

Cumulative SP ROAS combo chart for a fixed cohort of clickers.

Layout (matches the q2b reference PNG):
  - Cumulative sales bars  → lime, positive
  - Cumulative SP spend bars → pomegranate, negative
  - Cumulative ROAS line   → kale, with `$x.x` value labels at each point
  - Right-side ROAS axis hidden; values are labelled inline on the line.

Data is loaded by CALLing SANDBOX_DB.DANIELHAN.a_20260506_065c82
through instaquery — no CSV dependency.
"""

from pathlib import Path

import instaquery as iq
import matplotlib.colors as mcolors
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter

# --- Brand colors (from chart_styles.md §1) ---
KALE         = "#003d29"
CASHEW       = "#faf1e5"
LIGHT_CASHEW = "#fffbf7"
LIME         = "#0aad0a"
POMEGRANATE  = "#ba0239"
CINNAMON     = "#c22f00"
PLUS_PLUM    = "#750046"
CARROT       = "#ff7009"
GUAVA        = "#ff7a98"
TURMERIC     = "#ecaa01"
PLUS_BERRY   = "#b9017a"

mcolors.get_named_colors_mapping().update({
    "kale": KALE, "cashew": CASHEW, "light_cashew": LIGHT_CASHEW,
    "lime": LIME, "pomegranate": POMEGRANATE, "cinnamon": CINNAMON,
    "plus_plum": PLUS_PLUM, "carrot": CARROT, "guava": GUAVA,
    "turmeric": TURMERIC, "plus_berry": PLUS_BERRY,
})

TYPOGRAPHY_ON = {
    KALE: CASHEW, POMEGRANATE: CASHEW, CINNAMON: CASHEW,
    PLUS_PLUM: CASHEW, CARROT: CASHEW, PLUS_BERRY: CASHEW,
    LIME: KALE, GUAVA: KALE, TURMERIC: KALE,
    CASHEW: KALE, LIGHT_CASHEW: KALE,
}

IC_DARKGREEN   = KALE
IC_GREEN       = LIME
IC_BLACK       = KALE
IC_BG          = LIGHT_CASHEW
IC_GRID        = KALE
ACCENT_ORANGE  = CARROT
COLOR_NEGATIVE = POMEGRANATE
SERIES_COLORS  = [KALE, CARROT, POMEGRANATE, PLUS_BERRY,
                  TURMERIC, CINNAMON, PLUS_PLUM, GUAVA]
PHASE_TINT_1   = KALE
PHASE_TINT_2   = CARROT

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
        ax.grid(True, axis=grid_axis, alpha=0.15, linestyle="-",
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


def render_chart(
    # Required positional args — same names + order as the procedure parameters
    v_campaign_ids,
    v_chart_start,
    v_chart_end,
    v_cohort_month_end,
    *,
    # Scaling / formatting kwargs — defaults reproduce the source case PNG
    figsize=FIGSIZE_WIDE,
    bar_width=0.55,
    spend_axis_pad=2.4,        # multiplier of max(monthly cum spend) for negative ylim
    sales_axis_pad=1.55,       # multiplier of max(monthly cum sales) for positive ylim
    roas_target_lo=0.68,       # ROAS line target band (fraction of secondary axis)
    roas_target_hi=0.84,
    roas_axis_top_frac=1.10,   # extra headroom above the ROAS span
    roas_label_offset_frac=0.07,
    output_path="chart_1.png",
    warehouse="DEVELOPER_XL_WH",
):
    """Render the cumulative-ROAS combo chart for a clicker cohort.

    Required positional args mirror the stored procedure's parameters
    (same names + order). Kwargs are scaling/formatting controls;
    defaults reproduce the source-case PNG.

    Returns the matplotlib Figure so callers can post-process before
    re-saving if needed.
    """
    # --- Pull data via the stored procedure (fully qualified — iq.get_conn
    # uses its own session context and won't honor any USE SCHEMA directive). ---
    sql = (
        "CALL SANDBOX_DB.DANIELHAN.a_20260506_065c82("
        f"'{v_campaign_ids}', "
        f"'{v_chart_start}'::DATE, "
        f"'{v_chart_end}'::DATE, "
        f"'{v_cohort_month_end}'::DATE"
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

    df.columns = [c.lower() for c in df.columns]
    df["month"] = pd.to_datetime(df["month"])
    for col in ("monthly_sales", "monthly_spend",
                "cumulative_sales", "cumulative_spend", "cumulative_roas"):
        df[col] = pd.to_numeric(df[col])
    # x-axis labels matching reference style: m/1/yy
    df["month_label"] = df["month"].dt.strftime("%-m/1/%y")

    # --- Build the chart ---
    x = np.arange(len(df))
    fig, ax1 = plt.subplots(figsize=figsize)
    apply_ic_style(ax1, fig)
    ax2 = ax1.twinx()
    ax2.set_facecolor("none")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)

    # Bars: cumulative sales (positive, lime) + cumulative spend (negative, pomegranate)
    ax1.bar(x,  df["cumulative_sales"], color=IC_GREEN,        width=bar_width, zorder=2)
    ax1.bar(x, -df["cumulative_spend"], color=COLOR_NEGATIVE,  width=bar_width, zorder=2)

    max_sales = df["cumulative_sales"].max()
    max_spend = df["cumulative_spend"].max()
    ax1.set_ylim(-max_spend * spend_axis_pad, max_sales * sales_axis_pad)
    ax1.yaxis.set_major_formatter(FuncFormatter(fmt_money))
    ax1.set_ylabel("Cumulative Sales / Spend ($)", fontsize=AXIS_TITLE,
                   fontweight="bold", color=IC_BLACK)

    # ROAS line on hidden secondary axis
    roas = df["cumulative_roas"].values
    roas_range = max(roas.max() - roas.min(), 0.5)
    span = roas_range / (roas_target_hi - roas_target_lo)
    ax2_min = roas.min() - roas_target_lo * span
    ax2_max = ax2_min + span * roas_axis_top_frac
    ax2.set_ylim(ax2_min, ax2_max)
    ax2.set_axis_off()

    ax2.plot(x, roas, color=IC_BLACK, linewidth=2.0, marker="o", markersize=5,
             markerfacecolor=IC_BLACK, markeredgecolor=IC_BLACK, zorder=5)

    label_offset = roas_range * roas_label_offset_frac
    for i, v in enumerate(roas):
        ax2.text(i, v + label_offset, f"${v:.1f}",
                 ha="center", va="bottom",
                 fontsize=DATA_LABEL, fontweight="bold", color=IC_BLACK, zorder=6)

    # X-axis
    ax1.set_xticks(x)
    ax1.set_xticklabels(df["month_label"], fontsize=TICK_LABEL, color=IC_BLACK)
    ax1.set_xlim(-0.6, len(x) - 0.4)

    # Legend
    legend_handles = [
        plt.Line2D([0], [0], color=IC_BLACK, linewidth=2, marker="o",
                   markersize=5, markerfacecolor=IC_BLACK,
                   label="Cumulative ROAS"),
        mpatches.Patch(color=IC_GREEN,       label="Cumulative Sales"),
        mpatches.Patch(color=COLOR_NEGATIVE, label="Cumulative SP Spend"),
    ]
    leg = ax1.legend(handles=legend_handles, loc="upper left",
                     fontsize=TICK_LABEL, framealpha=0, edgecolor="none")
    for text in leg.get_texts():
        text.set_color(IC_BLACK)

    fig.tight_layout()
    save_chart(fig, output_path)
    return fig


# SMOKE-TEST ENTRY ONLY — pinned SAMPLE CALL args so the promotion-time
# operator can verify chart.py reproduces the source PNG. Future reuse
# callers MUST `import` render_chart and call it with their own params;
# do NOT run `python chart.py` from a reuse context (it will silently
# render the SOURCE case's chart in your bundle folder).
if __name__ == "__main__":
    render_chart(
        v_campaign_ids="266584,297686,311207",
        v_chart_start="2026-01-01",
        v_chart_end="2026-04-30",
        v_cohort_month_end="2026-01-31",
        output_path=str(Path(__file__).parent / "chart_1.png"),
    )
    print("wrote chart_1.png")
