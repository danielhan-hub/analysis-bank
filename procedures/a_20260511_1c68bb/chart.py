"""Generated from analyses/q1_by_quarter/chart.ipynb on promotion.

Cumulative SP ROAS combo chart for a clicker cohort across a chart window.
- Bars: cumulative sales (lime, positive) and cumulative SP click spend
        (pomegranate, negative-axis mirror).
- Line: cumulative ROAS overlaid via a hidden twin axis with inline value
        labels.

Required positional args mirror the procedure's parameters in the same
order. Kwargs control scaling/formatting and default to the values used
in the source notebook so calling render_chart(<positional args>) with
no kwargs reproduces the source PNG.
"""
from pathlib import Path

import instaquery as iq
import matplotlib.dates as mdates
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle  # noqa: F401  (re-exported helper)
from matplotlib.ticker import FuncFormatter

# --- Brand palette ---
IC_KALE        = "#003D29"
IC_CASHEW      = "#FAF1E5"
IC_LT_CASHEW   = "#fffbf7"
IC_POMEGRANATE = "#BA0239"
IC_CINNAMON    = "#C22F00"
IC_PLUM        = "#750046"
IC_CARROT      = "#FF7009"
IC_LIME        = "#0AAD0A"
IC_GUAVA       = "#FF7A98"
IC_TURMERIC    = "#ECAA01"
IC_BERRY       = "#B9017A"

# Aliases (chart_styles.md compatibility)
IC_DARKGREEN   = IC_KALE
IC_GREEN       = IC_LIME
IC_BLACK       = IC_KALE
IC_BG          = IC_CASHEW
IC_GRID        = "#598174"
COLOR_NEGATIVE = IC_POMEGRANATE
COLOR_NEUTRAL  = IC_CARROT
ACCENT_ORANGE  = IC_CINNAMON

SERIES_COLORS = [IC_KALE, IC_LIME, IC_CINNAMON, IC_CARROT,
                 IC_POMEGRANATE, IC_TURMERIC]

PHASE_TINT_1 = "#e8efe5"
PHASE_TINT_2 = "#fce8d8"

# --- Type sizes ---
TITLE_SIZE = 14
AXIS_TITLE = 12
TICK_LABEL = 10
DATA_LABEL = 11
ANNOTATION = 11

# --- Defaults ---
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
        spine.set_color(IC_KALE)
        spine.set_linewidth(1.0)
    ax.tick_params(axis="both", colors=IC_KALE, labelsize=TICK_LABEL)


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
        return f"${x/1_000_000:.1f}M"
    if abs(x) >= 1_000:
        return f"${x/1_000:.0f}K"
    return f"${x:,.0f}"


def fmt_pct(x, _=None):
    return f"{x:.0f}%"


def save_chart(fig, path, *, transparent=True):
    if transparent:
        fig.savefig(path, dpi=DPI, bbox_inches="tight", transparent=True)
    else:
        fig.savefig(path, dpi=DPI, bbox_inches="tight",
                    facecolor=fig.get_facecolor())


def _bucket_label(d, bucket):
    """Render an x-axis tick label appropriate to the bucket cadence."""
    if bucket == "quarter":
        return f"Q{(d.month - 1) // 3 + 1} '{d.year % 100:02d}"
    if bucket == "month":
        return d.strftime("%b '%y")
    if bucket == "week":
        return d.strftime("%-m/%-d/%y")
    return d.strftime("%Y-%m-%d")


def render_chart(
    v_account_id,
    v_chart_start,
    v_chart_end,
    v_cohort_start,
    v_cohort_end,
    v_bucket="quarter",
    v_campaign_type="featured_product",
    *,
    figsize=FIGSIZE_WIDE,
    bar_width=0.55,
    sales_headroom=1.55,
    spend_headroom=2.4,
    roas_target_lo=0.68,
    roas_target_hi=0.84,
    roas_label_offset_frac=0.07,
    output_path="chart_1.png",
    warehouse="DEVELOPER_XL_WH",
):
    """Render the cumulative SP ROAS combo chart.

    Required positional args mirror procedure parameters (same names + order).
    Kwargs control chart scaling/formatting; defaults reproduce the source PNG.
    Returns the matplotlib Figure for post-processing.
    """
    sql = f"""
        CALL SANDBOX_DB.DANIELHAN.a_20260511_1c68bb(
            {int(v_account_id)},
            '{v_chart_start}'::DATE,
            '{v_chart_end}'::DATE,
            '{v_cohort_start}'::DATE,
            '{v_cohort_end}'::DATE,
            '{v_bucket}',
            '{v_campaign_type}'
        )
    """

    conn = iq.get_conn("snowflake", conn_params={"warehouse": warehouse})
    try:
        cur = conn.cursor()
        cur.execute(sql)
        columns = [d[0].upper() for d in cur.description]
        rows = cur.fetchall()
    finally:
        conn.close()

    df = pd.DataFrame(rows, columns=columns)
    df.columns = [c.lower() for c in df.columns]
    df["bucket"] = pd.to_datetime(df["bucket"])
    df = df.sort_values("bucket").reset_index(drop=True)
    df["bucket_label"] = df["bucket"].apply(lambda d: _bucket_label(d, v_bucket))

    x = np.arange(len(df))
    fig, ax1 = plt.subplots(figsize=figsize)
    apply_ic_style(ax1, fig)
    ax2 = ax1.twinx()
    ax2.set_facecolor("none")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)

    # ── Bars: cumulative sales (positive) + cumulative spend (negative mirror)
    ax1.bar(x, df["cumulative_sales"].astype(float),
            color=IC_LIME, width=bar_width, zorder=2)
    ax1.bar(x, -df["cumulative_spend"].astype(float),
            color=IC_POMEGRANATE, width=bar_width, zorder=2)

    max_sales = float(df["cumulative_sales"].max())
    max_spend = float(df["cumulative_spend"].max())
    ax1.set_ylim(-max_spend * spend_headroom, max_sales * sales_headroom)
    ax1.yaxis.set_major_formatter(FuncFormatter(fmt_money))
    ax1.set_ylabel("Cumulative Sales / Spend (USD)", fontsize=AXIS_TITLE,
                   fontweight="bold", color=IC_KALE)

    # ── ROAS line on hidden twin axis with inline labels
    roas = df["cumulative_roas"].astype(float).values
    roas_range = max(roas.max() - roas.min(), 0.5)
    span = roas_range / (roas_target_hi - roas_target_lo)
    ax2_min = roas.min() - roas_target_lo * span
    ax2_max = ax2_min + span * 1.10
    ax2.set_ylim(ax2_min, ax2_max)
    ax2.set_axis_off()

    ax2.plot(x, roas, color=IC_KALE, linewidth=2.0, marker="o", markersize=5,
             markerfacecolor=IC_KALE, markeredgecolor=IC_KALE, zorder=5)

    label_offset = roas_range * roas_label_offset_frac
    for i, v in enumerate(roas):
        ax2.text(i, v + label_offset, f"${v:.1f}",
                 ha="center", va="bottom",
                 fontsize=DATA_LABEL, fontweight="bold", color=IC_KALE, zorder=6)

    # ── X-axis
    ax1.set_xticks(x)
    ax1.set_xticklabels(df["bucket_label"], fontsize=TICK_LABEL, color=IC_KALE)
    ax1.set_xlim(-0.6, len(x) - 0.4)

    # ── Legend
    legend_handles = [
        plt.Line2D([0], [0], color=IC_KALE, linewidth=2, marker="o",
                   markersize=5, markerfacecolor=IC_KALE, label="Cumulative ROAS"),
        mpatches.Patch(color=IC_LIME,        label="Cumulative Sales"),
        mpatches.Patch(color=IC_POMEGRANATE, label="Cumulative SP Spend"),
    ]
    leg = ax1.legend(handles=legend_handles, loc="upper left",
                     fontsize=TICK_LABEL, framealpha=0, edgecolor="none")
    for text in leg.get_texts():
        text.set_color(IC_KALE)

    fig.tight_layout()
    save_chart(fig, output_path)
    return fig


# SMOKE-TEST ENTRY ONLY — reproduces the source case (Vital Farms Q3 2025
# clicker cohort, Jul-Dec 2025, quarterly cumulative ROAS) for promotion-time
# verification. Future reuse callers MUST `from chart import render_chart`
# and pass their own params; do NOT run `python chart.py` from a reuse
# context (it will silently render the SOURCE case's chart in your folder).
if __name__ == "__main__":
    out = Path(__file__).parent / "chart_1.png"
    render_chart(
        v_account_id=45,
        v_chart_start="2025-07-01",
        v_chart_end="2025-12-31",
        v_cohort_start="2025-07-01",
        v_cohort_end="2025-09-30",
        v_bucket="quarter",
        v_campaign_type="featured_product",
        output_path=str(out),
    )
    print(f"wrote {out}")
