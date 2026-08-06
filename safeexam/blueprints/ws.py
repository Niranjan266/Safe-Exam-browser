"""
WebSocket transport for live proctoring.

Two sockets:

  /ws/exam/<exam_id>/stream   candidate  -> server   (binary JPEG frames)
  /ws/watch/<sub_id>/<kind>   server     -> proctor  (binary JPEG frames)

A frame arriving on the first is handed to every socket of the second at once,
so the proctor sees it as soon as it is captured instead of waiting for the
next poll.

This needs a persistent server. On a serverless host the routes simply are not
registered and both sides fall back to HTTP polling — see `ws_available()`.
"""
from __future__ import annotations

import os
import time

from flask import Blueprint
from flask_login import current_user

from ..extensions import db
from ..livehub import hub
from ..models import Submission, Exam
from .. import framestore

ws_bp = Blueprint("ws", __name__)

# How often the newest frame is also persisted. The hub already has it in
# memory for live viewing; the database copy only needs to be fresh enough for
# the single-attempt view and the polling fallback.
DB_PERSIST_INTERVAL = 2.0

# Guard against a client flooding us. 8 MB is far above a sane JPEG frame.
MAX_FRAME_BYTES = 8 * 1024 * 1024


def ws_available() -> bool:
    """
    False on hosts that cannot hold a socket open (Vercel and friends), so the
    front-end knows to poll instead of retrying a connection that cannot work.
    """
    if os.environ.get("SEB_DISABLE_WS") == "1":
        return False
    # Vercel sets this on every serverless invocation.
    return not os.environ.get("VERCEL")


def register(sock):
    """Attach the routes to a flask_sock.Sock instance."""

    # ------------------------------------------------------------------
    # Candidate -> server
    # ------------------------------------------------------------------
    @sock.route("/ws/exam/<int:exam_id>/stream")
    def exam_stream(ws, exam_id):
        if not current_user.is_authenticated:
            return
        user_id = current_user.id

        exam = db.session.get(Exam, exam_id)
        if exam is None:
            return

        sub = (
            Submission.query.filter_by(student_id=user_id, exam_id=exam_id)
            .order_by(Submission.id.desc())
            .first()
        )
        if sub is None or not sub.is_active:
            return

        submission_id = sub.id
        require_webcam = bool(exam.require_webcam)
        last_persist = {"screen": 0.0, "cam": 0.0}

        while True:
            data = ws.receive()
            if data is None:
                break

            # Each message is  "<kind>|" followed by the raw JPEG bytes, which
            # avoids base64 (a third bigger) and a second round trip.
            if isinstance(data, str):
                continue
            sep = data.find(b"|")
            if sep < 0 or sep > 10:
                continue
            kind = data[:sep].decode("ascii", "ignore")
            if kind not in ("screen", "cam"):
                continue
            frame = data[sep + 1:]
            if not frame or len(frame) > MAX_FRAME_BYTES:
                continue
            # The exam says no camera: refuse the frame rather than trusting
            # the client to have stopped sending it.
            if kind == "cam" and not require_webcam:
                continue

            hub.publish(submission_id, kind, frame)

            now = time.time()
            if now - last_persist[kind] >= DB_PERSIST_INTERVAL:
                last_persist[kind] = now
                try:
                    from io import BytesIO
                    from werkzeug.datastructures import FileStorage

                    framestore.save_live_frame(
                        submission_id, kind, FileStorage(BytesIO(frame))
                    )
                    sub_row = db.session.get(Submission, submission_id)
                    if sub_row is not None:
                        from datetime import datetime

                        sub_row.last_heartbeat = datetime.utcnow()
                    db.session.commit()
                except Exception:
                    db.session.rollback()   # never let persistence kill the stream

    # ------------------------------------------------------------------
    # Server -> proctor
    # ------------------------------------------------------------------
    @sock.route("/ws/watch/<int:sub_id>/<kind>")
    def watch(ws, sub_id, kind):
        # Live screen and camera are admin-only; teachers never see them.
        if not current_user.is_authenticated or not current_user.is_admin:
            return
        if kind not in ("screen", "cam"):
            return

        subscriber = hub.subscribe(sub_id, kind)
        try:
            while True:
                frame = subscriber.wait(timeout=25.0)
                if subscriber.closed:
                    break
                if frame is None:
                    # Idle. Ping so proxies do not drop an otherwise fine socket.
                    try:
                        ws.send("ping")
                    except Exception:
                        break
                    continue
                try:
                    ws.send(frame)
                except Exception:
                    break
        finally:
            hub.unsubscribe(sub_id, kind, subscriber)
