"""
Development entry point.

Run with:
    python app.py

For production use a real WSGI server with wsgi.py (see README).
"""
import os

from safeexam import create_app

app = create_app()

if __name__ == "__main__":
    # Override with SEB_HOST / SEB_PORT if the defaults are already in use
    # (e.g. aaPanel, AirPlay or another dev server occupying port 5000).
    host = os.getenv("SEB_HOST", "127.0.0.1")
    port = int(os.getenv("SEB_PORT", "5001"))
    print(f" * SafeExam starting on http://{host}:{port}/")
    app.run(host=host, port=port, debug=True)
