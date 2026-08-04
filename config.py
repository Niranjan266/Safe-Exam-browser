"""
Configuration for the Safe Exam Browser Flask application.

All sensitive / environment-specific values are read from environment
variables (optionally loaded from a local .env file) so the same code can
run in development and production without edits.
"""
import os
from datetime import timedelta

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")


def _bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    # --- Core ---
    SECRET_KEY = os.environ.get("SEB_SECRET_KEY", "dev-only-change-me-in-production")

    # --- Database ---
    # Override with SEB_DATABASE_URI (e.g. postgresql://... or a custom sqlite path).
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "SEB_DATABASE_URI",
        "sqlite:///" + os.path.join(INSTANCE_DIR, "safeexam.db"),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # --- Sessions / cookies ---
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Set SEB_SECURE_COOKIES=1 behind HTTPS in production.
    SESSION_COOKIE_SECURE = _bool(os.environ.get("SEB_SECURE_COOKIES"), False)
    REMEMBER_COOKIE_HTTPONLY = True

    # --- CSRF (Flask-WTF) ---
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None  # tie CSRF token to the session, not a timer

    # --- Proctoring / Safe-Exam policy ---
    # Number of violations after which an attempt is auto-terminated.
    MAX_VIOLATIONS = int(os.environ.get("SEB_MAX_VIOLATIONS", "3"))
    # Violations within this window (seconds) flag a student as "high risk".
    RISK_WINDOW_SECONDS = int(os.environ.get("SEB_RISK_WINDOW_SECONDS", "30"))
    RISK_WINDOW_THRESHOLD = int(os.environ.get("SEB_RISK_WINDOW_THRESHOLD", "2"))
    # How often (seconds) the client uploads a webcam proctor snapshot. 0 disables.
    SNAPSHOT_INTERVAL_SECONDS = int(os.environ.get("SEB_SNAPSHOT_INTERVAL", "20"))
    # Grace period (seconds) after a heartbeat stops before flagging disconnect.
    HEARTBEAT_TIMEOUT_SECONDS = int(os.environ.get("SEB_HEARTBEAT_TIMEOUT", "20"))

    # --- Proctor snapshot + live-frame storage ---
    SNAPSHOT_DIR = os.environ.get("SEB_SNAPSHOT_DIR", os.path.join(INSTANCE_DIR, "snapshots"))
    # Latest live screen/webcam frames (overwritten continuously during an attempt).
    LIVE_DIR = os.environ.get("SEB_LIVE_DIR", os.path.join(INSTANCE_DIR, "live"))
    MAX_CONTENT_LENGTH = 12 * 1024 * 1024  # cap on uploads (snapshots / live frames)


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = _bool(os.environ.get("SEB_SECURE_COOKIES"), True)


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "SEB_TEST_DATABASE_URI", "sqlite:///" + os.path.join(INSTANCE_DIR, "test.db")
    )


CONFIG_MAP = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}


def get_config(name=None):
    name = name or os.environ.get("SEB_ENV", "default")
    return CONFIG_MAP.get(name, DevelopmentConfig)
