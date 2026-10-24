"""
WSGI entry point for production servers (gunicorn, waitress, uWSGI).

Example:
    gunicorn "wsgi:app"            # Linux/macOS
    waitress-serve --call wsgi:create_app   # Windows
"""
import os
import subprocess
import sys

# On a long-running host (Render sets RENDER=true) bring the schema up to date
# before serving, so an upgrade's new tables/columns exist. It runs in a child
# process on purpose: opening a Turso connection in the worker's main thread at
# import and then using libsql from request threads hung every request. The
# child exits, releasing its connection, before the app is even imported.
if os.environ.get("RENDER") and os.environ.get("SEB_INIT_DB", "1") == "0":
    root = os.path.dirname(os.path.abspath(__file__))
    try:
        subprocess.run(
            [sys.executable, os.path.join(root, "scripts", "init_db.py"), "--no-seed"],
            cwd=root, timeout=180, check=False,
            # init_db only defaults this; the inherited "0" would skip the
            # add-missing-columns step.
            env={**os.environ, "SEB_INIT_DB": "1"},
        )
    except Exception as exc:  # never block start-up on the sync itself
        print(f"SafeExam: schema sync skipped ({exc})", file=sys.stderr)

from safeexam import create_app  # noqa: E402

app = create_app()

if __name__ == "__main__":
    app.run()
