"""
Storage for live proctoring frames and webcam snapshots.

Two backends, chosen by config["FRAME_STORAGE"]:

  "disk" - JPEGs under instance/live and instance/snapshots. Fast, and the
           default for local development.
  "db"   - blobs in the database. Required on serverless hosts (Vercel), where
           the filesystem is read-only and, crucially, the process that
           receives a frame is not the process that later serves it to the
           proctor, so a written file would be invisible.

Both backends present the same API so the views do not care which is active.
"""
import os
from datetime import datetime

from flask import current_app
from werkzeug.utils import secure_filename

from .extensions import db
from .models import LiveFrame, ProctorSnapshot


def _mode():
    return (current_app.config.get("FRAME_STORAGE") or "disk").lower()


def use_db():
    return _mode() == "db"


# ----------------------------- live frames -----------------------------

def save_live_frame(submission_id, kind, file_storage):
    """Store the newest screen/webcam frame for an attempt, replacing the old one."""
    if use_db():
        data = file_storage.read()
        if not data:
            return False
        row = LiveFrame.query.filter_by(submission_id=submission_id, kind=kind).first()
        if row is None:
            row = LiveFrame(submission_id=submission_id, kind=kind)
            db.session.add(row)
        row.image = data
        row.updated_at = datetime.utcnow()
        return True

    fname = secure_filename(f"sub{submission_id}_{kind}.jpg")
    file_storage.save(os.path.join(current_app.config["LIVE_DIR"], fname))
    return True


def load_live_frame(submission_id, kind):
    """Return the latest frame as bytes, or None if there isn't one yet."""
    if use_db():
        row = LiveFrame.query.filter_by(submission_id=submission_id, kind=kind).first()
        return row.image if row else None

    path = os.path.join(current_app.config["LIVE_DIR"], f"sub{submission_id}_{kind}.jpg")
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def has_live_frame(submission_id, kind):
    """Cheap existence check used by the live wall feed (avoids loading blobs)."""
    if use_db():
        return (
            db.session.query(LiveFrame.id)
            .filter_by(submission_id=submission_id, kind=kind)
            .first()
            is not None
        )
    return os.path.exists(
        os.path.join(current_app.config["LIVE_DIR"], f"sub{submission_id}_{kind}.jpg")
    )


def live_frame_ids(submission_ids):
    """Return {(submission_id, kind)} present, in one query instead of N."""
    if not submission_ids:
        return set()
    if use_db():
        rows = (
            db.session.query(LiveFrame.submission_id, LiveFrame.kind)
            .filter(LiveFrame.submission_id.in_(submission_ids))
            .all()
        )
        return {(sid, kind) for sid, kind in rows}
    found = set()
    live_dir = current_app.config["LIVE_DIR"]
    for sid in submission_ids:
        for kind in ("screen", "cam"):
            if os.path.exists(os.path.join(live_dir, f"sub{sid}_{kind}.jpg")):
                found.add((sid, kind))
    return found


def clear_live_frames(submission_id):
    """Drop an attempt's frames once it ends, so blobs don't pile up."""
    if use_db():
        LiveFrame.query.filter_by(submission_id=submission_id).delete()
        return
    live_dir = current_app.config["LIVE_DIR"]
    for kind in ("screen", "cam"):
        path = os.path.join(live_dir, f"sub{submission_id}_{kind}.jpg")
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


# ----------------------------- snapshots -----------------------------

def save_snapshot(submission, file_storage):
    """Persist a webcam still for later review and return the ProctorSnapshot."""
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    fname = secure_filename(f"sub{submission.id}_{ts}.jpg")

    snap = ProctorSnapshot(
        submission_id=submission.id,
        student_id=submission.student_id,
        exam_id=submission.exam_id,
        filename=fname,
    )
    if use_db():
        data = file_storage.read()
        if not data:
            return None
        snap.image = data
    else:
        file_storage.save(os.path.join(current_app.config["SNAPSHOT_DIR"], fname))

    db.session.add(snap)
    return snap


def load_snapshot(snap):
    """Return a snapshot's JPEG bytes, whichever backend wrote it."""
    if snap.image:
        return snap.image
    path = os.path.join(current_app.config["SNAPSHOT_DIR"], snap.filename)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()
