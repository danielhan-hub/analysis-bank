"""Generated from analyses/q1/chart.ipynb on promotion.

Renders the per-combo NTB conversion rate chart produced by the
`multi_tactic_combo_ntb_conversion` stored procedure. Loads data via
`iq.query("CALL ...")` so any future case can call this without ever
opening a notebook or staging a CSV.
"""

from pathlib import Path

import instaquery as iq
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter

# --- Brand colors ---
IC_DARKGREEN = "#003d29"   # Default fills, headlines, primary lines
IC_GREEN     = "#0aad0a"   # The ONE highlight color — use sparingly
IC_BLACK     = "#343538"   # Text, axis labels, ticks
IC_BG        = "#f6f5f0"   # Cream background — applied to figure + axes
IC_GRID      = "#598174"   # Grid lines (muted dark-green)

# --- Accents (semantic) ---
ACCENT_ORANGE  = "#e07c3e"  # $-value lines / endpoint dollar labels
COLOR_NEGATIVE = "#d62728"  # Declines, gaps, risks
COLOR_NEUTRAL  = "#5b8def"  # Peers / control / informational

# --- Series cycle (multi-series, in order) ---
SERIES_COLORS = [IC_DARKGREEN, IC_GREEN, ACCENT_ORANGE, COLOR_NEUTRAL,
                 "#598174", "#8c564b"]

# --- Phase shading (used by line/area charts with phase blocks) ---
PHASE_TINT_1 = "#e8efe5"  # Soft green tint — phase 1
PHASE_TINT_2 = "#fce8d8"  # Soft orange tint — phase 2

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


# ---------------------------------------------------------------------------
# Procedure invocation helpers
# ---------------------------------------------------------------------------

def _sql_quote(s: str) -> str:
    """Snowflake single-quote a string, escaping embedded quotes."""
    return "'" + str(s).replace("'", "''") + "'"


def _sql_array(values) -> str:
    """Build an ARRAY_CONSTRUCT(...) literal from a Python iterable of strings."""
    if values is None or len(values) == 0:
        return "ARRAY_CONSTRUCT()"
    inner = ", ".join(_sql_quote(v) for v in values)
    return f"ARRAY_CONSTRUCT({inner})"


def _build_call(
    v_account_id,
    v_account_uuid,
    v_entity_brand_id,
    v_country_id,
    v_window_start,
    v_window_end,
    v_suas_promotion_group_ids,
    v_min_combo_users,
):
    return (
        "CALL SANDBOX_DB.DANIELHAN.multi_tactic_combo_ntb_conversion("
        f"{int(v_account_id)}, "
        f"{_sql_quote(v_account_uuid)}, "
        f"{int(v_entity_brand_id)}, "
        f"{int(v_country_id)}, "
        f"{_sql_quote(v_window_start)}::DATE, "
        f"{_sql_quote(v_window_end)}::DATE, "
        f"{_sql_array(v_suas_promotion_group_ids)}, "
        f"{int(v_min_combo_users)}"
        ");"
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def render_chart(
    # Required positional args — same names + order as procedure.sql parameters.
    v_account_id,
    v_account_uuid,
    v_entity_brand_id,
    v_country_id,
    v_window_start,
    v_window_end,
    v_suas_promotion_group_ids,
    v_min_combo_users=100,
    *,
    # Scaling / formatting controls (defaults reproduce the source notebook).
    top_n=10,
    figsize=(10, 6),
    xlim_max_factor=1.22,
    bar_height=0.65,
    label_pad=0.15,
    font_size=DATA_LABEL,
    output_path="chart_1.png",
    warehouse="DEVELOPER_XL_WH",
):
    """Render the per-combo NTB conversion rate chart for the given case.

    Parameters
    ----------
    v_account_id, v_account_uuid, v_entity_brand_id, v_country_id,
    v_window_start, v_window_end, v_suas_promotion_group_ids,
    v_min_combo_users
        Forwarded directly to the `multi_tactic_combo_ntb_conversion`
        stored procedure. Dates may be passed as strings ('YYYY-MM-DD')
        or `datetime.date`. `v_suas_promotion_group_ids` is an iterable
        of UUID strings (pass `[]` to disable the SUAS dimension).

    top_n : int
        Show only the top N combos by NTB conversion rate.
    figsize : tuple[float, float]
        Matplotlib figure size in inches.
    xlim_max_factor : float
        Right-edge multiplier on the max value, to leave room for labels.
    bar_height : float
        barh `height` parameter — controls bar thickness/gap.
    label_pad : float
        Horizontal padding (in % units) between bar end and value label.
    font_size : int
        Base font size for non-focal value labels (focal gets +1).
    output_path : str
        PNG output path. Defaults to "chart_1.png" in the working directory.

    Returns
    -------
    matplotlib.figure.Figure
        The rendered figure (already saved to `output_path`).
    """
    sql = _build_call(
        v_account_id,
        v_account_uuid,
        v_entity_brand_id,
        v_country_id,
        v_window_start,
        v_window_end,
        v_suas_promotion_group_ids,
        v_min_combo_users,
    )
    conn = iq.get_conn("snowflake", conn_params={"warehouse": warehouse})
    try:
        cur = conn.cursor()
        cur.execute(sql)
        columns = [d[0].upper() for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=columns)
    finally:
        conn.close()

    if df.empty:
        raise ValueError(
            "multi_tactic_combo_ntb_conversion returned no rows — check "
            "that the account / brand / window / SUAS group ids resolve "
            "to actual exposure, and that v_min_combo_users isn't set "
            "above every combo's exposed_user_count."
        )

    # --- Framing: top N, sorted ascending so highest lands at chart top ---
    df_top = df.nlargest(top_n, "NTB_CONVERSION_RATE").copy()
    df_top["rate_pct"] = df_top["NTB_CONVERSION_RATE"] * 100
    df_top = df_top.sort_values("rate_pct", ascending=True).reset_index(drop=True)

    focal_pos = len(df_top) - 1  # highest value, plotted at top
    bar_colors = [IC_GREEN if i == focal_pos else IC_DARKGREEN for i in range(len(df_top))]

    # --- Chart ---
    fig, ax = plt.subplots(figsize=figsize)
    ax.barh(df_top["COMBO_LABEL"], df_top["rate_pct"], color=bar_colors, height=bar_height)
    apply_ic_style(ax, fig)  # bar charts: no grid

    # Category-axis breathing room (mirrors vertical bar xlim rule)
    ax.set_ylim(-0.7, len(df_top) - 0.3)
    # Value-axis: headroom so labels clear the last bar
    ax.set_xlim(0, df_top["rate_pct"].max() * xlim_max_factor)

    # Value labels to the right of each bar; focal is bold + slightly larger.
    for i, (lbl, v) in enumerate(zip(df_top["COMBO_LABEL"], df_top["rate_pct"])):
        is_focal = (i == focal_pos)
        ax.text(
            v + label_pad, i, f"{v:.1f}%",
            ha="left", va="center",
            fontsize=font_size + (1 if is_focal else 0),
            fontweight="bold" if is_focal else "normal",
            color=IC_GREEN if is_focal else IC_BLACK,
        )

    # Color the focal y-axis tick label to match the bar.
    for i, tick in enumerate(ax.get_yticklabels()):
        if i == focal_pos:
            tick.set_color(IC_GREEN)
            tick.set_fontweight("bold")

    ax.set_xlabel("NTB Conversion Rate (%)", fontsize=AXIS_TITLE, fontweight="bold")
    ax.set_ylabel("Ad Tactic Combination", fontsize=AXIS_TITLE, fontweight="bold")
    ax.xaxis.set_major_formatter(FuncFormatter(fmt_pct))
    # No ax.set_title(...) — headline lives in the surrounding doc.
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
        v_account_id=45,
        v_account_uuid="717500bd-e82b-4438-a8a0-aa42dec1183b",
        v_entity_brand_id=564770,
        v_country_id=840,
        v_window_start="2025-07-01",
        v_window_end="2026-05-03",
        v_suas_promotion_group_ids=[
            "5ae514c3-332d-4668-b654-862d95cf755e",
            "16998f0f-578d-411b-8021-eb278004f772",
            "381681f9-6c43-4d5a-81d3-13495fec75de",
        ],
        v_min_combo_users=100,
        output_path=str(Path(__file__).parent / "chart_1.png"),
    )
    print("wrote chart_1.png")
