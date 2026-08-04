"""
Vercel serverless entry point.

Vercel's Python runtime imports this module and serves the WSGI callable named
`app`. Everything else about the application is unchanged; only the storage
layer differs, because Vercel gives each invocation a read-only filesystem:

  * the database is Turso (libSQL) via SEB_TURSO_URL / SEB_TURSO_TOKEN
  * live frames and webcam snapshots are stored as blobs in that database

Required environment variables (set in Vercel -> Settings -> Environment
Variables): SEB_SECRET_KEY, SEB_TURSO_URL, SEB_TURSO_TOKEN.
"""
import os
import sys

# Make the project root importable (this file lives in ./api).
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("SEB_ENV", "production")
os.environ.setdefault("SEB_SECURE_COOKIES", "1")
# Frames/snapshots must live in the database, not on the read-only disk.
os.environ.setdefault("SEB_FRAME_STORAGE", "db")
# The schema is created once by scripts/init_db.py; skip the per-cold-start
# round trips to keep responses fast.
os.environ.setdefault("SEB_INIT_DB", "0")
# Writable scratch space, if anything still reaches for the filesystem.
os.environ.setdefault("SEB_SNAPSHOT_DIR", "/tmp/snapshots")
os.environ.setdefault("SEB_LIVE_DIR", "/tmp/live")

from safeexam import create_app  # noqa: E402

app = create_app()
