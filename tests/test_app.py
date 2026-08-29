"""End-to-end tests for the SafeExam Flask app."""
import os
import tempfile
from datetime import datetime, timedelta

import pytest

# Configure an isolated test database BEFORE importing the app.
_DB_PATH = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["SEB_ENV"] = "testing"
os.environ["SEB_TEST_DATABASE_URI"] = "sqlite:///" + _DB_PATH

from safeexam import create_app  # noqa: E402
from safeexam.extensions import db  # noqa: E402
from safeexam.models import User, Exam, Question, Submission, ROLE_ADMIN, ROLE_STUDENT  # noqa: E402


@pytest.fixture()
def app():
    app = create_app("testing")
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app


@pytest.fixture()
def client(app):
    return app.test_client()


# ---------------- helpers ----------------

def make_user(app, username, password, role):
    with app.app_context():
        u = User(username=username, role=role)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        return u.id


def logout(client):
    return client.get("/logout", follow_redirects=True)


def login(client, username, password):
    # /login redirects an already-authenticated client straight to its
    # dashboard without reading the form, so switching users mid-test silently
    # kept the previous session and the next request ran as the wrong role.
    # Clearing the session first is what makes "log in as admin" mean it.
    logout(client)
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=True,
    )


def make_exam_with_questions(app, admin_id, n=3, duration=10, published=True):
    with app.app_context():
        exam = Exam(title="Test Exam", duration=duration, created_by=admin_id, is_published=published)
        db.session.add(exam)
        db.session.commit()
        for i in range(n):
            db.session.add(
                Question(
                    exam_id=exam.id,
                    text=f"Q{i}",
                    option_a="a", option_b="b", option_c="c", option_d="d",
                    correct_answer="A", order=i,
                )
            )
        db.session.commit()
        return exam.id


# ---------------- tests ----------------

def test_register_and_login(client):
    r = client.post(
        "/register",
        data={"username": "stu", "full_name": "Stu", "password": "secret1",
              "confirm": "secret1", "role": "student"},
        follow_redirects=True,
    )
    assert r.status_code == 200
    r = login(client, "stu", "secret1")
    assert b"My Exams" in r.data or b"Available exams" in r.data


def test_wrong_password_rejected(client):
    client.post("/register", data={"username": "x", "password": "secret1",
                "confirm": "secret1", "role": "student"}, follow_redirects=True)
    r = login(client, "x", "WRONG")
    assert b"Invalid username or password" in r.data


def test_student_cannot_access_admin(app, client):
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    login(client, "stu", "secret1")
    r = client.get("/admin/")
    assert r.status_code == 403


def test_admin_creates_exam_and_question(app, client):
    make_user(app, "adm", "secret1", ROLE_ADMIN)
    login(client, "adm", "secret1")
    r = client.post("/admin/exams/new", data={
        "title": "Midterm", "duration": "15", "description": "d",
    }, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        exam = Exam.query.filter_by(title="Midterm").first()
        assert exam is not None
        eid = exam.id
    r = client.post(f"/admin/exams/{eid}/questions", data={
        "text": "1+1?", "option_a": "1", "option_b": "2", "option_c": "3",
        "option_d": "4", "correct_answer": "B",
    }, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        assert Question.query.filter_by(exam_id=eid).count() == 1


def test_full_exam_flow_scores_correctly(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=3)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    login(client, "stu", "secret1")

    # Start the exam (creates submission)
    r = client.get(f"/exam/{exam_id}/start")
    assert r.status_code == 200

    with app.app_context():
        qs = Question.query.filter_by(exam_id=exam_id).order_by(Question.order).all()
        # Answer 2 of 3 correctly (correct is 'A')
        form = {str(qs[0].id): "A", str(qs[1].id): "A", str(qs[2].id): "B"}

    r = client.post(f"/exam/{exam_id}/submit", data=form, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        sub = Submission.query.filter_by(exam_id=exam_id).first()
        assert sub.completed is True
        assert sub.score == 2
        assert sub.total == 3


def test_violation_limit_disqualifies(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=2)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    login(client, "stu", "secret1")
    client.get(f"/exam/{exam_id}/start")

    last = None
    for _ in range(3):  # 3 x severity-1 "Copy Attempt" reaches default limit of 3
        last = client.post(f"/api/exam/{exam_id}/violation", json={"type": "Copy Attempt"})
    assert last.get_json()["terminated"] is True
    with app.app_context():
        sub = Submission.query.filter_by(exam_id=exam_id).first()
        assert sub.disqualified is True
        assert sub.locked is True


def test_expired_attempt_autocompletes(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=2, duration=1)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    login(client, "stu", "secret1")
    client.get(f"/exam/{exam_id}/start")

    # Force the start time into the past so the attempt is expired.
    with app.app_context():
        sub = Submission.query.filter_by(exam_id=exam_id).first()
        sub.started_at = datetime.utcnow() - timedelta(minutes=5)
        db.session.commit()

    # Hitting start again should auto-grade and send to the result page.
    r = client.get(f"/exam/{exam_id}/start", follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        sub = Submission.query.filter_by(exam_id=exam_id).first()
        assert sub.completed is True


def test_admin_reports_available(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    make_exam_with_questions(app, admin_id, n=1)
    login(client, "adm", "secret1")
    assert client.get("/admin/report.csv").status_code == 200
    pdf = client.get("/admin/report.pdf")
    assert pdf.status_code == 200
    assert pdf.data[:4] == b"%PDF"


def test_exam_state_api_reports_remaining(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=1, duration=10)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    login(client, "stu", "secret1")
    client.get(f"/exam/{exam_id}/start")
    state = client.get(f"/api/exam/{exam_id}/state").get_json()
    assert state["exists"] is True
    assert state["remaining"] > 0
    assert state["completed"] is False


# ---------------- admin feature tests ----------------

def test_deactivated_user_cannot_login(app, client):
    from safeexam.models import User
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    with app.app_context():
        u = User.query.filter_by(username="stu").first()
        u.active = False
        db.session.commit()
    r = login(client, "stu", "secret1")
    assert b"deactivated" in r.data.lower()


def test_admin_user_management(app, client):
    make_user(app, "adm", "secret1", ROLE_ADMIN)
    login(client, "adm", "secret1")
    assert client.get("/admin/users").status_code == 200
    r = client.post("/admin/users/new", data={
        "username": "newbie", "full_name": "New Bie", "email": "n@x.io",
        "role": "student", "active": "y", "password": "secret1",
    }, follow_redirects=True)
    assert r.status_code == 200
    from safeexam.models import User
    with app.app_context():
        assert User.query.filter_by(username="newbie").first() is not None


def test_settings_update_changes_policy(app, client):
    make_user(app, "adm", "secret1", ROLE_ADMIN)
    login(client, "adm", "secret1")
    assert client.get("/admin/settings").status_code == 200
    client.post("/admin/settings", data={
        "max_violations": "7", "risk_window_seconds": "30", "risk_window_threshold": "2",
        "snapshot_interval": "0", "heartbeat_timeout": "20", "pass_mark": "60",
    }, follow_redirects=True)
    from safeexam import settings as settings_store
    with app.app_context():
        assert settings_store.get_int("max_violations") == 7
        assert settings_store.get_int("pass_mark") == 60


def test_exam_clone_and_import(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=2)
    login(client, "adm", "secret1")
    # clone
    client.post(f"/admin/exams/{exam_id}/clone", follow_redirects=True)
    from safeexam.models import Exam, Question
    with app.app_context():
        assert Exam.query.count() == 2
    # import 2 questions via CSV
    client.post(f"/admin/exams/{exam_id}/questions/import", data={
        "data": "What is 2+2?,1,2,3,4,D\nCapital of France?,London,Paris,Rome,Berlin,B",
    }, follow_redirects=True)
    with app.app_context():
        assert Question.query.filter_by(exam_id=exam_id).count() == 4


def test_score_override_and_audit(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=3)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    login(client, "stu", "secret1")
    client.get(f"/exam/{exam_id}/start")
    client.post(f"/exam/{exam_id}/submit", data={}, follow_redirects=True)
    from safeexam.models import Submission, AuditLog
    with app.app_context():
        sub = Submission.query.filter_by(exam_id=exam_id).first()
        sid = sub.id
    # admin overrides the score
    login(client, "adm", "secret1")
    client.post(f"/admin/submissions/{sid}/score", data={"score": "3"}, follow_redirects=True)
    with app.app_context():
        assert db.session.get(Submission, sid).score == 3
        assert AuditLog.query.filter_by(action="submission.score_override").count() == 1


def test_admin_new_pages_render(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=2)
    login(client, "adm", "secret1")
    for path in ["/admin/", "/admin/users", "/admin/users/new", "/admin/settings",
                 "/admin/audit", f"/admin/exams/{exam_id}/stats"]:
        assert client.get(path).status_code == 200, path


# ---------------- monitoring & evidence reporting ----------------

def _attempt_with_evidence(app, client, admin_id, exam_id, n_snaps=3):
    """Run an attempt, record a violation, and attach webcam stills."""
    import io as _io
    from PIL import Image as _Img
    from safeexam.models import Violation, ProctorSnapshot

    make_user(app, "stu", "secret1", ROLE_STUDENT)
    login(client, "stu", "secret1")
    client.get(f"/exam/{exam_id}/start")
    client.post(f"/exam/{exam_id}/submit", data={}, follow_redirects=True)

    with app.app_context():
        sub = Submission.query.filter_by(exam_id=exam_id).first()
        sid, stu_id = sub.id, sub.student_id
        start = sub.started_at or datetime.utcnow()
        db.session.add(Violation(
            submission_id=sid, student_id=stu_id, exam_id=exam_id,
            violation_type="DevTools Suspected", details="window grew by 214px",
            timestamp=start + timedelta(seconds=30)))
        for i in range(n_snaps):
            buf = _io.BytesIO()
            _Img.new("RGB", (64, 48), (10 * i, 60, 120)).save(buf, "JPEG")
            snap = ProctorSnapshot(
                submission_id=sid, student_id=stu_id, exam_id=exam_id,
                filename=f"t{i}.jpg", captured_at=start + timedelta(seconds=20 * i),
                flagged=(i == 1))
            snap.image = buf.getvalue()
            db.session.add(snap)
        db.session.commit()
    return sid


def test_evidence_pdf_is_generated(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=2)
    sid = _attempt_with_evidence(app, client, admin_id, exam_id)

    login(client, "adm", "secret1")
    r = client.get(f"/admin/submissions/{sid}/report.pdf")
    assert r.status_code == 200
    assert r.mimetype == "application/pdf"
    assert r.data[:5] == b"%PDF-"
    # The stills must be embedded, not linked, or the report is not evidence.
    assert len(r.data) > 4000


def test_evidence_pdf_never_prints_pass_for_a_disqualified_attempt(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=2)
    sid = _attempt_with_evidence(app, client, admin_id, exam_id, n_snaps=0)

    from safeexam import reports
    with app.app_context():
        sub = db.session.get(Submission, sid)
        sub.disqualified = True
        sub.score, sub.total = 2, 2          # a perfect score...
        db.session.commit()
        text = reports.build_evidence_pdf(
            sub, db.session.get(User, sub.student_id),
            db.session.get(Exam, exam_id), pass_mark=40).getvalue()
    # ...must still not be reported as a pass.
    assert b"PASS" not in text


def test_replay_feed_merges_violations_and_snapshots(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=2)
    sid = _attempt_with_evidence(app, client, admin_id, exam_id)

    login(client, "adm", "secret1")
    r = client.get(f"/api/submission/{sid}/replay")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["violations"]) == 1
    assert len(data["snapshots"]) == 3
    assert data["duration"] >= 1
    # Offsets, not wall-clock, are what the scrubber positions against.
    assert data["violations"][0]["t"] == 30
    assert [s["t"] for s in data["snapshots"]] == [0, 20, 40]
    assert any(s["flagged"] for s in data["snapshots"])
    assert client.get(f"/admin/submissions/{sid}/replay").status_code == 200


def test_violations_csv_exports_rows_and_filters(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=2)
    _attempt_with_evidence(app, client, admin_id, exam_id, n_snaps=0)

    login(client, "adm", "secret1")
    r = client.get("/admin/violations.csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    body = r.data.decode()
    assert "DevTools Suspected" in body and "window grew by 214px" in body
    # Severity must be the weight that actually counts toward termination.
    assert ",3," in body
    # A date window that excludes the attempt returns only the header row.
    empty = client.get("/admin/violations.csv?from=2000-01-01&to=2000-01-02")
    assert len([ln for ln in empty.data.decode().splitlines() if ln.strip()]) == 1
    # A malformed date must be ignored, not raise.
    assert client.get("/admin/violations.csv?from=not-a-date").status_code == 200


def test_flagging_copies_the_live_frame_into_evidence(app, client):
    import io as _io
    from PIL import Image as _Img
    from werkzeug.datastructures import FileStorage
    from safeexam import framestore
    from safeexam.models import ProctorSnapshot

    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=2)
    sid = _attempt_with_evidence(app, client, admin_id, exam_id, n_snaps=0)

    with app.app_context():
        buf = _io.BytesIO()
        _Img.new("RGB", (64, 48), (200, 30, 30)).save(buf, "JPEG")
        buf.seek(0)
        framestore.save_live_frame(sid, "cam", FileStorage(buf))
        db.session.commit()

    login(client, "adm", "secret1")
    r = client.post(f"/api/submission/{sid}/flag")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True and r.get_json()["kind"] == "cam"
    with app.app_context():
        snaps = ProctorSnapshot.query.filter_by(submission_id=sid, flagged=True).all()
        assert len(snaps) == 1
        # The bytes must be copied, or the flag points at nothing once the
        # attempt is graded and live frames are cleared. Read it back through
        # framestore rather than the `image` column, because the disk backend
        # stores the JPEG as a file and leaves that column null.
        assert framestore.load_snapshot(snaps[0])


def test_monitoring_endpoints_are_admin_only(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam_with_questions(app, admin_id, n=2)
    sid = _attempt_with_evidence(app, client, admin_id, exam_id, n_snaps=0)

    login(client, "stu", "secret1")          # the candidate, not an admin
    assert client.get(f"/api/submission/{sid}/replay").status_code == 403
    assert client.post(f"/api/submission/{sid}/flag").status_code == 403
    for path in [f"/admin/submissions/{sid}/report.pdf",
                 f"/admin/submissions/{sid}/replay",
                 "/admin/violations.csv"]:
        assert client.get(path).status_code in (302, 403), path
