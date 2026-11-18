import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

REFERENCE_ARTICLE = Path(__file__).resolve().parent / "fixtures" / "reference_article.md"
