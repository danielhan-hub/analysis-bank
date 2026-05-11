"""Generated from analyses/q5_ntb_opp/chart.ipynb on promotion.

Renders a single 100% stacked-column composition chart from the result
of SANDBOX_DB.DANIELHAN.brand_cohort_opportunity_analysis. Five cohort
segments stacked bottom-to-top, focal cohort (default Competitor
Loyalist — the NTB opportunity pool) highlighted in IC_GREEN.

Reuse:
    from chart import render_chart
    render_chart(
        v_brand_ids='123456',
        v_category_ids='598',
        v_start_date='2026-04-01',
        v_end_date='2026-06-30',
        output_path='my_case_chart.png',
    )

The bottom of this file has a SMOKE-TEST entry point pinned to the
SAMPLE CALL args from procedure.sql; do NOT run it from a reuse
context (it would re-render the SOURCE case's chart).
"""
from pathlib import Path

import instaquery as iq
import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.patches import Patch

# --- Brand colors (Instacart palette — single source of truth) ---
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
    KALE:         CASHEW,
    POMEGRANATE:  CASHEW,
    CINNAMON:     CASHEW,
    PLUS_PLUM:    CASHEW,
    CARROT:       CASHEW,
    PLUS_BERRY:   CASHEW,
    LIME:         KALE,
    GUAVA:        KALE,
    TURMERIC:     KALE,
    CASHEW:       KALE,
    LIGHT_CASHEW: KALE,
}

IC_DARKGREEN = KALE
IC_GREEN     = LIME
IC_BLACK     = KALE
IC_BG        = LIGHT_CASHEW
IC_GRID      = KALE

ACCENT_ORANGE = CARROT

TITLE_SIZE = 14
AXIS_TITLE = 12
TICK_LABEL = 10
DATA_LABEL = 11
ANNOTATION = 11

FIGSIZE_DEFAULT = (5.5, 7)
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


# --- Cohort visual mapping (stable across cases) ---
STACK_ORDER = [
    "Brand Loyalist",
    "Brand-Biased Switcher",
    "Competitor Loyalist",
    "Competitor-Biased Switcher",
    "Switcher, No Brand Bias",
]
COHORT_COLORS = {
    "Brand Loyalist":             IC_DARKGREEN,
    "Brand-Biased Switcher":      TURMERIC,
    "Competitor Loyalist":        IC_GREEN,    # focal — NTB opportunity
    "Competitor-Biased Switcher": CASHEW,
    "Switcher, No Brand Bias":    ACCENT_ORANGE,
}
LEGEND_ORDER = [
    "Switcher, No Brand Bias",
    "Competitor-Biased Switcher",
    "Competitor Loyalist",
    "Brand-Biased Switcher",
    "Brand Loyalist",
]


def render_chart(
    v_brand_ids,
    v_category_ids,
    v_start_date,
    v_end_date,
    v_country_id=840,
    v_min_category_orders=2,
    *,
    figsize=FIGSIZE_DEFAULT,
    output_path="chart_1.png",
    warehouse="DEVELOPER_XL_WH",
    focal_cohort="Competitor Loyalist",
    bar_width=0.55,
):
    """Call the procedure with the given params and render the cohort
    composition stacked-column chart. Returns the matplotlib Figure.
    """
    # 1) Pull data via the stored procedure.
    conn = iq.get_conn("snowflake", conn_params={"warehouse": warehouse})
    try:
        cur = conn.cursor()
        cur.execute(
            "CALL SANDBOX_DB.DANIELHAN.brand_cohort_opportunity_analysis("
            f"'{v_brand_ids}', '{v_category_ids}', "
            f"'{v_start_date}'::DATE, '{v_end_date}'::DATE, "
            f"{int(v_country_id)}, {int(v_min_category_orders)});"
        )
        columns = [d[0].upper() for d in cur.description]
        df = pd.DataFrame(cur.fetchall(), columns=columns)
    finally:
        conn.close()

    # 2) Reorder to stack order, drop missing cohorts gracefully.
    present = [c for c in STACK_ORDER if c in set(df["COHORT"])]
    df = df.set_index("COHORT").loc[present].reset_index()
    df["pct"] = df["PERCENT_CATEGORY_USERS"].astype(float) * 100

    total_users = int(df["USER_COUNT"].astype(float).sum())
    if focal_cohort in set(df["COHORT"]):
        focal_sales = float(
            df.loc[df["COHORT"] == focal_cohort,
                   "SUM_TOTAL_CATEGORY_SALES"].iloc[0]
        )
    else:
        focal_sales = 0.0

    # 3) Render.
    fig, ax = plt.subplots(figsize=figsize)
    bottom = 0.0
    for _, r in df.iterrows():
        cohort = r["COHORT"]
        pct = r["pct"]
        color = COHORT_COLORS.get(cohort, KALE)
        ax.bar([0], [pct], bottom=[bottom], color=color, width=bar_width,
               edgecolor="none", zorder=2)
        label_color = TYPOGRAPHY_ON.get(color, IC_BLACK)
        is_focal = (cohort == focal_cohort)
        ax.text(0, bottom + pct / 2, f"{pct:.1f}%",
                ha="center", va="center",
                color=label_color,
                fontsize=DATA_LABEL + (1 if is_focal else 0),
                fontweight="bold" if is_focal else "normal")
        bottom += pct

    apply_ic_style(ax, fig)
    ax.set_xlim(-0.6, 0.6)
    ax.set_ylim(0, 100)
    ax.set_xticks([])
    ax.set_yticks([])
    ax.spines["left"].set_visible(False)
    ax.spines["bottom"].set_visible(False)

    # Footer: total n + focal cohort sales (the NTB opportunity in $)
    ax.text(
        0, -3,
        f"n = {total_users:,}\n{fmt_money(focal_sales)} {focal_cohort} sales",
        ha="center", va="top",
        fontsize=TICK_LABEL, color=IC_BLACK,
    )

    handles = [Patch(facecolor=COHORT_COLORS[c], label=c) for c in LEGEND_ORDER]
    ax.legend(
        handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.02),
        ncol=2, frameon=False, fontsize=TICK_LABEL,
        handlelength=1.2, handleheight=1.0, columnspacing=1.5,
    )

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
        v_brand_ids='564770',
        v_category_ids='598,869',
        v_start_date='2026-01-01',
        v_end_date='2026-03-31',
        v_country_id=840,
        v_min_category_orders=2,
        output_path=str(Path(__file__).parent / "chart_1.png"),
    )
    print("wrote chart_1.png")
