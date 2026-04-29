"""Resolve analysis_bank data paths at runtime.

procedures/, candidates/, and analysis_features.csv live at the repository
root, one level above the Python package directory. We use importlib.resources
to locate the package, then step up to the repo root.
"""

from pathlib import Path
import importlib.resources


def _repo_root() -> Path:
    """Return the analysis_bank repo root.

    Assumes the package is installed in editable mode from the repo root.
    The repo root contains procedures/, candidates/, analysis_features.csv.
    """
    with importlib.resources.as_file(
        importlib.resources.files("analysis_bank")
    ) as pkg:
        return pkg.parent  # analysis_bank/ -> repo root


REPO_ROOT = _repo_root()

# Repo-root data
PROCEDURES_DIR = REPO_ROOT / "procedures"
CANDIDATES_DIR = REPO_ROOT / "candidates"
FEATURES_CSV_PATH = REPO_ROOT / "analysis_features.csv"

# Package-bundled assets (ship inside the wheel via package-data)
_PKG_DIR = Path(__file__).parent
PROMPTS_DIR = _PKG_DIR / "prompts"
FEATURES_DIR = _PKG_DIR / "features"

FEATURE_DICT_PATH = FEATURES_DIR / "feature_dict.md"
SCORING_PROMPT_PATH = PROMPTS_DIR / "scoring_agent.md"
INSPECTOR_PROMPT_PATH = PROMPTS_DIR / "inspector_agent.md"
