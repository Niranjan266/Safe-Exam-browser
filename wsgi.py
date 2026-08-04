"""
WSGI entry point for production servers (gunicorn, waitress, uWSGI).

Example:
    gunicorn "wsgi:app"            # Linux/macOS
    waitress-serve --call wsgi:create_app   # Windows
"""
from safeexam import create_app

app = create_app()

if __name__ == "__main__":
    app.run()
