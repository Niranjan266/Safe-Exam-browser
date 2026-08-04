"""
Admin-editable global settings, backed by the AppSetting key/value table and
falling back to values from config.py when a key has not been customised.
"""
from flask import current_app

from .extensions import db
from .models import AppSetting

# key -> (config fallback key or None, hard default)
DEFAULTS = {
    "max_violations": ("MAX_VIOLATIONS", 3),
    "risk_window_seconds": ("RISK_WINDOW_SECONDS", 30),
    "risk_window_threshold": ("RISK_WINDOW_THRESHOLD", 2),
    "snapshot_interval": ("SNAPSHOT_INTERVAL_SECONDS", 20),
    "heartbeat_timeout": ("HEARTBEAT_TIMEOUT_SECONDS", 20),
    "pass_mark": (None, 50),
}

LABELS = {
    "max_violations": "Violation limit (auto-terminate)",
    "risk_window_seconds": "Risk window (seconds)",
    "risk_window_threshold": "Risk window threshold",
    "snapshot_interval": "Webcam snapshot interval (seconds, 0 = off)",
    "heartbeat_timeout": "Heartbeat timeout (seconds)",
    "pass_mark": "Default pass mark (%)",
}


def _default(key):
    cfg_key, fallback = DEFAULTS[key]
    if cfg_key:
        try:
            return int(current_app.config.get(cfg_key, fallback))
        except Exception:
            return fallback
    return fallback


def get_int(key):
    if key not in DEFAULTS:
        raise KeyError(key)
    row = db.session.get(AppSetting, key)
    if row is not None:
        try:
            return int(row.value)
        except (TypeError, ValueError):
            pass
    return _default(key)


def get_policy():
    return {k: get_int(k) for k in DEFAULTS}


def set_policy(values):
    """values: dict of {key: int}. Unknown keys ignored."""
    for k, v in values.items():
        if k not in DEFAULTS or v is None:
            continue
        row = db.session.get(AppSetting, k)
        if row is None:
            db.session.add(AppSetting(key=k, value=str(int(v))))
        else:
            row.value = str(int(v))
    db.session.commit()
