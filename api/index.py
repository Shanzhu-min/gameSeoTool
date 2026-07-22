from __future__ import annotations

import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Vercel's filesystem is not persistent. Configure SUPABASE_DB_URL in
# Vercel Environment Variables for durable storage. This fallback only keeps
# the MVP bootable when no Postgres connection is configured.
os.environ.setdefault("GAMESEO_DB_PATH", "/tmp/gameseo.sqlite3")

from gameseotools.web import WebApp, make_handler


_GeneratedHandler = make_handler(
    WebApp(
        "config/sites.example.json",
        run_startup_migration=False,
        enable_scheduler_thread=False,
    )
)


class handler(_GeneratedHandler):
    pass
