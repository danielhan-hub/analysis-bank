# ── Configuration: edit these to re-run for a different set of campaigns ─────
CAMPAIGN_IDS     = [266584, 297686, 311207]
CHART_START      = '2026-01-01'
CHART_END        = '2026-04-30'
COHORT_MONTH_END = '2026-01-31'   # cohort defined in the first month only
OUTPUT_CSV       = 'q2b_cumulative_roas.csv'
SQL_FILE         = 'q2b_cumulative_roas_chart.sql'
# ─────────────────────────────────────────────────────────────────────────────

import re
from pathlib import Path
import instaquery as iq

sql = Path(SQL_FILE).read_text()
sql = sql[sql.index('WITH campaign_ids'):]

params = {
    'chart_start':      f"'{CHART_START}'",
    'chart_end':        f"'{CHART_END}'",
    'cohort_month_end': f"'{COHORT_MONTH_END}'",
    'campaign_ids':     f"'{','.join(str(c) for c in CAMPAIGN_IDS)}'",
}
sql = re.sub(r'\$([a-z_]+)', lambda m: params[m.group(1)], sql)

result = iq.query(sql)
result.to_csv(OUTPUT_CSV, index=False)
print(result.to_string())
