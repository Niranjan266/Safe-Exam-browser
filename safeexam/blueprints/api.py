"""JSON API used by the exam page (client) and the admin live monitor."""
import os
from datetime import datetime

from flask import Blueprint, request, jsonify, current_app, abort, url_for
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from sqlalchemy import func

from ..extensions import db
from ..models import Exam, Submission, Violation, ProctorSnapshot, User
from ..services import get_active_submission, upsert_answer
from .. import proctoring
from .. import framestore
from .. import settings as settings_store

api_bp = Blueprint("api", __name__, url_prefix="/api")


def _active_or_none(exam_id):
    return get_active_submission(current_user.id, exam_id)


@api_bp.route("/exam/<int:exam_id>/state")
@login_required
def exam_state(exam_id):
    """Server-truth state the client polls to enforce lock/time even on refresh."""
    sub = (
        Submission.query.filter_by(student_id=current_user.id, exam_id=exam_id)
        .order_by(Submission.id.desc())
        .first()
    )
    if sub is None:
        return jsonify({"exists": False})
    return jsonify(
        {
            "exists": True,
            "locked": sub.locked,
            "disqualified": sub.disqualified,
            "completed": sub.completed,
            "remaining": sub.seconds_remaining(),
            "violations": sub.violation_count,
            "reason": sub.termination_reason,
        }
    )


@api_bp.route("/exam/<int:exam_id>/violation", methods=["POST"])
@login_required
def log_violation(exam_id):
    sub = _active_or_none(exam_id)
    if sub is None:
        return jsonify({"status": "no_active_attempt"}), 200
    if sub.locked or sub.disqualified or sub.completed:
        return jsonify({"status": "terminated", "terminated": True})

    data = request.get_json(silent=True) or {}
    vtype = (data.get("type") or "Unknown").strip()[:120]
    details = (data.get("details") or None)

    proctoring.record_violation(sub, vtype, details)
    outcome = proctoring.evaluate(sub)
    db.session.commit()

    return jsonify(
        {
            "status": outcome["status"],
            "terminated": outcome["terminated"],
            "severity": outcome["severity"],
            "limit": outcome["limit"],
            "recent": outcome["recent"],
            "remaining": sub.seconds_remaining(),
        }
    )


@api_bp.route("/exam/<int:exam_id>/answer", methods=["POST"])
@login_required
def save_answer(exam_id):
    sub = _active_or_none(exam_id)
    if sub is None or not sub.is_active:
        return jsonify({"ok": False}), 200
    data = request.get_json(silent=True) or {}
    try:
        question_id = int(data.get("question_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "bad question_id"}), 400
    upsert_answer(sub, question_id, data.get("selected"))
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/exam/<int:exam_id>/heartbeat", methods=["POST"])
@login_required
def heartbeat(exam_id):
    sub = (
        Submission.query.filter_by(student_id=current_user.id, exam_id=exam_id)
        .order_by(Submission.id.desc())
        .first()
    )
    if sub is None:
        return jsonify({"exists": False})
    if sub.is_active:
        sub.last_heartbeat = datetime.utcnow()
        # Auto-grade if the clock ran out while the page was open.
        if sub.is_expired():
            from ..services import grade_submission

            grade_submission(sub, reason="Time expired")
        db.session.commit()
    return jsonify(
        {
            "locked": sub.locked,
            "disqualified": sub.disqualified,
            "completed": sub.completed,
            "remaining": sub.seconds_remaining(),
        }
    )


@api_bp.route("/exam/<int:exam_id>/snapshot", methods=["POST"])
@login_required
def upload_snapshot(exam_id):
    if settings_store.get_int("snapshot_interval") <= 0:
        return jsonify({"ok": False, "disabled": True})
    sub = _active_or_none(exam_id)
    if sub is None or not sub.is_active:
        return jsonify({"ok": False}), 200

    file = request.files.get("snapshot")
    if not file:
        return jsonify({"ok": False, "error": "no file"}), 400

    framestore.save_snapshot(sub, file)
    db.session.commit()
    return jsonify({"ok": True})


@api_bp.route("/exam/<int:exam_id>/live", methods=["POST"])
@login_required
def live_frame(exam_id):
    """Receive the latest live screen / webcam frame for an attempt (overwrite)."""
    sub = _active_or_none(exam_id)
    if sub is None or not sub.is_active:
        return jsonify({"ok": False}), 200
    kind = request.form.get("kind", "screen")
    if kind not in ("screen", "cam"):
        kind = "screen"
    file = request.files.get("frame")
    if not file:
        return jsonify({"ok": False, "error": "no file"}), 400
    framestore.save_live_frame(sub.id, kind, file)
    # A live frame is also a presence signal.
    sub.last_heartbeat = datetime.utcnow()
    db.session.commit()
    return jsonify({"ok": True})


# ---------------- Admin monitoring feeds ----------------

def _admin_only():
    if not current_user.is_admin:
        abort(403)


@api_bp.route("/monitor")
@login_required
def monitor_feed():
    _admin_only()
    rows = []
    submissions = (
        db.session.query(Submission, User.username, Exam.title)
        .join(User, User.id == Submission.student_id)
        .join(Exam, Exam.id == Submission.exam_id)
        .order_by(Submission.started_at.desc())
        .all()
    )
    for sub, username, exam_title in submissions:
        rows.append(
            {
                "submission_id": sub.id,
                "student": username,
                "exam": exam_title,
                "violations": sub.violation_count,
                "severity": proctoring.total_severity(sub),
                "status": proctoring.live_status(sub),
                "remaining": sub.seconds_remaining() if sub.is_active else 0,
                "locked": sub.locked,
                "completed": sub.completed,
                "snapshots": len(sub.snapshots),
            }
        )
    # Sort: most urgent first.
    priority = {"Disqualified": 0, "High Risk": 1, "Disconnected": 2, "Active": 3, "Locked": 4, "Completed": 5}
    rows.sort(key=lambda r: priority.get(r["status"], 9))
    return jsonify(rows)


@api_bp.route("/live-wall")
@login_required
def live_wall_feed():
    """
    Admin-only feed for the multi-student live wall.

    Returns one entry per in-progress attempt, with the URL of that attempt's
    latest screen/webcam frame. Purely a read of frames the exam page is
    already pushing — nothing is sent to the student, so watching is silent.
    """
    _admin_only()
    exam_id = request.args.get("exam_id", type=int)

    query = (
        db.session.query(Submission, User.username, User.full_name, Exam.title)
        .join(User, User.id == Submission.student_id)
        .join(Exam, Exam.id == Submission.exam_id)
        .filter(
            Submission.completed.is_(False),
            Submission.disqualified.is_(False),
        )
    )
    if exam_id:
        query = query.filter(Submission.exam_id == exam_id)

    records = query.order_by(Submission.started_at.desc()).all()
    # One lookup for every attempt rather than two filesystem/DB hits each.
    present = framestore.live_frame_ids([s.id for s, _, _, _ in records])

    rows = []
    for sub, username, full_name, exam_title in records:
        has_screen = (sub.id, "screen") in present
        has_cam = (sub.id, "cam") in present
        rows.append(
            {
                "submission_id": sub.id,
                "student": full_name or username,
                "exam": exam_title,
                "status": proctoring.live_status(sub),
                "violations": sub.violation_count,
                "severity": proctoring.total_severity(sub),
                "remaining": sub.seconds_remaining() if sub.is_active else 0,
                "locked": sub.locked,
                "has_screen": has_screen,
                "has_cam": has_cam,
                "screen_url": url_for("admin.live_frame_image", sub_id=sub.id, kind="screen"),
                "cam_url": url_for("admin.live_frame_image", sub_id=sub.id, kind="cam"),
                "detail_url": url_for("admin.live_view", sub_id=sub.id),
            }
        )

    priority = {"High Risk": 0, "Disconnected": 1, "Active": 2, "Locked": 3}
    rows.sort(key=lambda r: (priority.get(r["status"], 9), r["student"].lower()))
    return jsonify(rows)


@api_bp.route("/violation-stats")
@login_required
def violation_stats():
    _admin_only()
    stats = (
        db.session.query(Violation.violation_type, func.count(Violation.id))
        .group_by(Violation.violation_type)
        .all()
    )
    return jsonify({vtype: count for vtype, count in stats})
