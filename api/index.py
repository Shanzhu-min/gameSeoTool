from __future__ import annotations

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

os.environ.setdefault("GAMESEO_DB_PATH", "/tmp/gameseo.sqlite3")

from gameseotools.web import WebApp, make_handler


handler = make_handler(WebApp("config/sites.example.json"))
