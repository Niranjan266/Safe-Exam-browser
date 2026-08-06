"""
Admin-editable global settings, backed by the AppSetting key/value table and
falling back to values from config.py when a key has not been customised.
"""
from flask import current_app, g

from .extensions import db
from .models import AppSetting

# Request-scoped cache key. The settings table is tiny and changes only when an
# admin saves the form, but it used to be read one key at a time on every single
# template render — six separate round trips per page. Against a remote database
# that alone cost over a second per request.
_CACHE_KEY = "_seb_policy_cache"

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


def _load_policy():
    """Read every setting in ONE query and cache it for this request."""
    try:
        cached = g.get(_CACHE_KEY)
    except RuntimeError:          # outside a request context
        cached = None
    if cached is not None:
        return cached

    stored = {}
    try:
        for row in AppSetting.query.all():          # single round trip
            try:
                stored[row.key] = int(row.value)
            except (TypeError, ValueError):
                pass
    except Exception:
        db.session.rollback()                       # fall back to config defaults

    policy = {k: stored.get(k, _default(k)) for k in DEFAULTS}
    try:
        setattr(g, _CACHE_KEY, policy)
    except RuntimeError:
        pass
    return policy


def get_int(key):
    if key not in DEFAULTS:
        raise KeyError(key)
    return _load_policy()[key]


def get_policy():
    return _load_policy()


def invalidate_cache():
    """Drop the per-request cache after an admin saves new values."""
    try:
        if g.get(_CACHE_KEY) is not None:
            setattr(g, _CACHE_KEY, None)
    except RuntimeError:
        pass


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
    invalidate_cache()
