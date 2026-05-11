"""Resolve analysis_bank data paths at runtime.

procedures/, candidates/, and analysis_features.csv live at the repository
root, one level above the Python package directory. The package is always
installed editable from the repo root, so __file__ is the cheapest and most
robust anchor: ``analysis_bank/paths.py`` → ``analysis_bank/`` → repo root.
"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent

# Repo-root data
PROCEDURES_DIR = REPO_ROOT / "procedures"
CANDIDATES_DIR = REPO_ROOT / "candidates"
FEATURES_CSV_PATH = REPO_ROOT / "analysis_features.csv"
PROCEDURES_INDEX_PATH = PROCEDURES_DIR / "_index.md"

# Package-bundled assets (ship inside the wheel via package-data)
_PKG_DIR = Path(__file__).parent
PROMPTS_DIR = _PKG_DIR / "prompts"
FEATURES_DIR = _PKG_DIR / "features"

INSPECTOR_PROMPT_PATH = PROMPTS_DIR / "inspector_agent.md"
