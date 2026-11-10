"""Build corpus_local/uae/U01.md from the raw official Arabic print edition.

Kept for compatibility; the recipe lives in src/gapcourt/prepare.py. Same as `gapcourt prepare --rebuild U01`.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gapcourt.prepare import rebuild  # noqa: E402

print(rebuild(Path(__file__).resolve().parents[1], "U01"))
