"""Resolve analysis_bank data paths at runtime.

INDEX.md, procedures/, and candidates/ live at the repository root,
one level above the Python package directory.  We use importlib.resources
to locate the package, then step up to the repo root.
"""

from pathlib import Path
import importlib.resources


def _repo_root() -> Path:
    """Return the analysis_bank repo root.

    Assumes the package is installed in editable mode from the repo root.
    The repo root contains INDEX.md and procedures/.
    """
    with importlib.resources.as_file(
        importlib.resources.files("analysis_bank")
    ) as pkg:
        return pkg.parent  # analysis_bank/ -> repo root


REPO_ROOT = _repo_root()
INDEX_PATH = REPO_ROOT / "INDEX.md"
PROCEDURES_DIR = REPO_ROOT / "procedures"
CANDIDATES_DIR = REPO_ROOT / "candidates"
PROMPTS_DIR = Path(__file__).parent / "prompts"
