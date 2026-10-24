"""
Centralised proctoring logic: violation severity, risk scoring and the
server-side rules that lock or disqualify an attempt.

Keeping this in one place means the exam page, the API and the live monitor
all agree on what "High Risk" or "Disqualified" means.
"""
import math
from datetime import timedelta

from .extensions import db
from .models import Violation
from . import settings
from .timeutil import utcnow

# Weight each violation type contributes toward termination.
# Higher = more serious. Unknown types default to 1.
VIOLATION_SEVERITY = {
    "Tab Switch": 2,
    "Window Blur": 1,
    "Exited Fullscreen": 2,
    "Copy Attempt": 1,
    "Paste Attempt": 1,
    "Cut Attempt": 1,
    "Right Click Attempt": 1,
    "Keyboard Shortcut Attempt": 1,
    # A plain key press during a mouse-only exam: recorded and shown to the
    # candidate, but the lightest possible weight so a stray keystroke does not
    # carry the same cost as probing dev tools.
    "Blocked Key": 1,
    "DevTools Suspected": 3,
    "Window Resized": 1,
    "Multiple Faces": 3,
    "No Face Detected": 2,
    "Disconnected": 2,
    "Screen Share Denied": 2,
    "Screen Share Stopped": 2,
    "Looking Away": 1,
    "Multiple Monitors": 3,
}

# A focus loss straight after a long idle spell looks like "copy the question,
# go and search for it", so it costs more than an ordinary blur.
IDLE_FOCUS_SEVERITY = 3
IDLE_DETAIL_PREFIX = "after idle"

# Strictness presets: per-type multipliers applied on top of the base weights.
# "standard" is empty so it keeps the weights above exactly.
FOCUS_TYPES = ("Tab Switch", "Window Blur", "Exited Fullscreen")
FACE_TYPES = ("Multiple Faces", "No Face Detected", "Looking Away")
STRICTNESS_LEVELS = ("relaxed", "standard", "strict")
STRICTNESS_MULTIPLIERS = {
    "relaxed": {"Blocked Key": 0, "Window Resized": 0, "Right Click Attempt": 0,
                **{t: 0.5 for t in FOCUS_TYPES}},
    "standard": {},
    "strict": {t: 2 for t in FOCUS_TYPES + FACE_TYPES},
}


def severity_for(violation_type, exam=None, details=None):
    """Weight of one violation, adjusted by the exam's strictness preset."""
    base = VIOLATION_SEVERITY.get(violation_type, 1)
    if (violation_type in ("Window Blur", "Tab Switch") and details
            and str(details).startswith(IDLE_DETAIL_PREFIX)):
        base = max(base, IDLE_FOCUS_SEVERITY)
    level = getattr(exam, "strictness", None) or "standard"
    mult = STRICTNESS_MULTIPLIERS.get(level, {}).get(violation_type)
    if mult is None:
        return base
    return 0 if mult == 0 else max(1, math.ceil(base * mult))


def max_violations_for(exam):
    """Per-exam override falls back to the global (settings) default."""
    if exam is not None and exam.max_violations:
        return exam.max_violations
    return settings.get_int("max_violations")


def record_violation(submission, violation_type, details=None):
    """Persist a violation and return (violation, total_severity)."""
    v = Violation(
        submission_id=submission.id,
        student_id=submission.student_id,
        exam_id=submission.exam_id,
        violation_type=violation_type,
        severity=severity_for(violation_type, submission.exam, details),
        details=details,
        timestamp=utcnow(),
    )
    db.session.add(v)
    db.session.flush()  # assign id without full commit yet
    return v


def total_severity(submission):
    return sum(severity_for(v.violation_type, submission.exam, v.details)
               for v in submission.violations)


def recent_violation_count(submission, window_seconds=None, now=None):
    window_seconds = window_seconds or settings.get_int("risk_window_seconds")
    now = now or utcnow()
    cutoff = now - timedelta(seconds=window_seconds)
    return (
        Violation.query.filter(
            Violation.submission_id == submission.id,
            Violation.timestamp >= cutoff,
        ).count()
    )


def evaluate(submission):
    """
    Apply server-side rules to an in-progress attempt.

    Returns a dict describing the resulting state and mutates the submission
    (lock / disqualify) when thresholds are crossed. Caller commits.
    """
    limit = max_violations_for(submission.exam)
    severity = total_severity(submission)
    recent = recent_violation_count(submission)
    threshold = settings.get_int("risk_window_threshold")

    result = {
        "severity": severity,
        "limit": limit,
        "recent": recent,
        "status": "active",
        "terminated": False,
    }

    # Hard stop: cumulative severity reached the limit -> disqualify + lock.
    if severity >= limit and not submission.completed:
        submission.disqualified = True
        submission.locked = True
        submission.termination_reason = "Violation limit exceeded"
        result["status"] = "disqualified"
        result["terminated"] = True
        return result

    # Soft signal: burst of activity inside the risk window.
    if recent >= threshold:
        result["status"] = "high_risk"

    return result


def live_status(submission, now=None):
    """Status string used by the admin live monitor."""
    now = now or utcnow()
    if submission.disqualified:
        return "Disqualified"
    if submission.completed:
        return "Completed"
    if submission.locked:
        return "Locked"

    # Disconnected? heartbeat went silent.
    timeout = settings.get_int("heartbeat_timeout")
    if submission.last_heartbeat and (now - submission.last_heartbeat).total_seconds() > timeout * 2:
        return "Disconnected"

    if recent_violation_count(submission, now=now) >= settings.get_int("risk_window_threshold"):
        return "High Risk"
    return "Active"
