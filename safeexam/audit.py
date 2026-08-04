"""Audit-trail helper for proctor/admin actions."""
from flask import request, has_request_context
from flask_login import current_user

from .extensions import db
from .models import AuditLog


def log_action(action, target=None, detail=None):
    """Record an admin action. Never raises (auditing must not break a request)."""
    try:
        entry = AuditLog(
            actor_id=getattr(current_user, "id", None),
            actor_name=getattr(current_user, "username", "system"),
            action=action,
            target=str(target) if target is not None else None,
            detail=detail,
            ip=request.remote_addr if has_request_context() else None,
        )
        db.session.add(entry)
        db.session.commit()
    except Exception:
        db.session.rollback()
