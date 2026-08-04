"""Authorization decorators."""
from functools import wraps

from flask import abort, current_app
from flask_login import current_user


def _require(predicate):
    """Build a decorator that allows the view only when predicate(user) is true."""

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                # Defer to Flask-Login's configured unauthorized handler (redirect to login).
                return current_app.login_manager.unauthorized()
            if not predicate(current_user):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def role_required(role):
    return _require(lambda u: u.role == role)


# Single-role gates
admin_required = role_required("admin")
teacher_required = role_required("teacher")
student_required = role_required("student")

# Admins and teachers share the authoring side of the app. Anything sensitive
# (user management, settings, audit log, live screen monitoring) stays behind
# `admin_required`.
staff_required = _require(lambda u: u.role in ("admin", "teacher"))


def owns_or_admin(obj, owner_attr="created_by"):
    """
    Abort 403 unless the current user is an admin or owns `obj`.

    Teachers may only touch the exams and classes they created; admins may
    touch anything.
    """
    if obj is None:
        abort(404)
    if current_user.is_admin:
        return obj
    if getattr(obj, owner_attr, None) != current_user.id:
        abort(403)
    return obj
