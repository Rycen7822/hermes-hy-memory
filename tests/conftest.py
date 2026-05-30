from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HERMES_AGENT = Path('/home/xu/.hermes/hermes-agent')
for path in (PROJECT_ROOT, HERMES_AGENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
