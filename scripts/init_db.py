"""
Create the schema and seed demo data in the configured database.

Run this once against Turso before deploying (the serverless app itself skips
schema creation to keep cold starts fast):

    # PowerShell
    $env:SEB_TURSO_URL   = "libsql://<your-db>.turso.io"
    $env:SEB_TURSO_TOKEN = "<token>"
    python scripts/init_db.py

Pass --no-seed to create the tables without the demo accounts.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("SEB_INIT_DB", "1")
os.environ.setdefault("SEB_FRAME_STORAGE", "db")

from safeexam import create_app  # noqa: E402
from safeexam.extensions import db  # noqa: E402
from safeexam import turso  # noqa: E402


def main():
    app = create_app()
    with app.app_context():
        db.create_all()
        target = "Turso (libSQL)" if turso.is_enabled() else app.config["SQLALCHEMY_DATABASE_URI"]
        print(f"Schema created on: {target}")
        tables = sorted(t.name for t in db.metadata.sorted_tables)
        print(f"Tables ({len(tables)}): {', '.join(tables)}")

    if "--no-seed" not in sys.argv:
        from seed import run_seed

        run_seed(app)


if __name__ == "__main__":
    main()
