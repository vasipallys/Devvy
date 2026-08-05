"""Test environment setup.

These assignments MUST happen before anything imports `backend.config`, `backend.db`, or
`backend.api`: settings are cached by `lru_cache`, and the SQLModel engine is created at
import time from `APP_DATA_DIR`.

Isolating the data directory matters more now that jobs are durable. The job worker claims
queued rows from the database, so a test sharing `./data` with a running development server
would hand its jobs to that server's worker — which has no monkeypatched agent and would
try to load the real model.
"""

import os
import shutil
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / ".pytest-data"
shutil.rmtree(DATA_DIR, ignore_errors=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

os.environ["PHOENIX_ENABLED"] = "false"
os.environ["APP_DATA_DIR"] = str(DATA_DIR)
