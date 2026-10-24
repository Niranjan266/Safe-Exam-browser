"""Proctor operations: extra time, re-entry, CSV safety, stream end, review."""
import os
import tempfile
from datetime import timedelta

import pytest

_DB_PATH = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["SEB_ENV"] = "testing"
os.environ["SEB_TEST_DATABASE_URI"] = "sqlite:///" + _DB_PATH

from safeexam import create_app  # noqa: E402
from safeexam.csvutil import safe_cell  # noqa: E402
from safeexam.extensions import db  # noqa: E402
from safeexam.models import (  # noqa: E402
    User, Exam, Question, Submission, ProctorSnapshot,
    ROLE_ADMIN, ROLE_STUDENT, ROLE_TEACHER,
)
from safeexam.timeutil import utcnow  # noqa: E402


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


def make_user(app, username, password, role):
    with app.app_context():
        u = User(username=username, role=role)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, username, password):
    client.get("/logout", follow_redirects=True)
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=True)


def make_exam(app, owner_id, n=2, duration=10):
    with app.app_context():
        exam = Exam(title="Test Exam", duration=duration, created_by=owner_id, is_published=True)
        db.session.add(exam)
        db.session.commit()
        for i in range(n):
            db.session.add(Question(exam_id=exam.id, text=f"Q{i}", option_a="a", option_b="b",
                                    option_c="c", option_d="d", correct_answer="A", order=i))
        db.session.commit()
        return exam.id


def start(app, client, exam_id):
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    login(client, "stu", "secret1")
    client.get(f"/exam/{exam_id}/start")
    with app.app_context():
        return Submission.query.filter_by(exam_id=exam_id).first().id


def test_safe_cell():
    for bad in ("=1+1", "+1", "-1", "@x", "\tx", "\rx"):
        assert safe_cell(bad) == "'" + bad
    assert safe_cell("ok") == "ok" and safe_cell(5) == 5


def test_csv_exports_escape_formulas(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam(app, admin_id)
    with app.app_context():
        u = User(username="=HYPERLINK(1)", role=ROLE_STUDENT)
        u.set_password("secret1")
        db.session.add(u)
        db.session.commit()
        db.session.add(Submission(student_id=u.id, exam_id=exam_id, started_at=utcnow()))
        db.session.commit()
    login(client, "adm", "secret1")
    body = client.get("/admin/report.csv").data.decode()
    assert "'=HYPERLINK(1)" in body and ",=HYPERLINK" not in body
    assert client.get("/admin/violations.csv").status_code == 200


def test_extra_time_extends_deadline_and_state(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam(app, admin_id, duration=1)
    sub_id = start(app, client, exam_id)
    with app.app_context():
        sub = db.session.get(Submission, sub_id)
        sub.started_at = utcnow() - timedelta(seconds=50)
        db.session.commit()
    login(client, "adm", "secret1")
    r = client.post(f"/api/submission/{sub_id}/extra-time", json={"minutes": 10})
    assert r.get_json()["ok"] is True
    with app.app_context():
        sub = db.session.get(Submission, sub_id)
        assert sub.extra_seconds == 600
        assert sub.seconds_remaining() > 600 and not sub.is_expired()
    login(client, "stu", "secret1")
    assert client.get(f"/api/exam/{exam_id}/state").get_json()["remaining"] > 600


def test_teacher_extra_time_only_for_own_exam(app, client):
    t1 = make_user(app, "t1", "secret1", ROLE_TEACHER)
    make_user(app, "t2", "secret1", ROLE_TEACHER)
    exam_id = make_exam(app, t1)
    sub_id = start(app, client, exam_id)
    login(client, "t2", "secret1")
    assert client.post(f"/teacher/submissions/{sub_id}/extend", data={"minutes": 5}).status_code == 404
    login(client, "t1", "secret1")
    client.post(f"/teacher/submissions/{sub_id}/extend", data={"minutes": 5})
    with app.app_context():
        assert db.session.get(Submission, sub_id).extra_seconds == 300


def test_reenter_resumes_locked_attempt(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam(app, admin_id)
    sub_id = start(app, client, exam_id)
    client.post(f"/api/exam/{exam_id}/answer", json={"question_id": 1, "selected": "A"})
    login(client, "adm", "secret1")
    client.post(f"/admin/submissions/{sub_id}/disqualify")
    login(client, "stu", "secret1")
    assert "/locked" in client.get(f"/exam/{exam_id}/start").headers["Location"]
    login(client, "adm", "secret1")
    client.post(f"/admin/submissions/{sub_id}/reenter")
    with app.app_context():
        sub = db.session.get(Submission, sub_id)
        assert sub.is_active and sub.violation_count == 0
        from safeexam.models import AuditLog
        assert AuditLog.query.filter_by(action="submission.reenter").count() == 1
    login(client, "stu", "secret1")
    assert client.get(f"/exam/{exam_id}/start").status_code == 200


def test_reenter_refuses_completed(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam(app, admin_id)
    sub_id = start(app, client, exam_id)
    with app.app_context():
        sub = db.session.get(Submission, sub_id)
        sub.locked = True
        sub.completed = True
        db.session.commit()
    login(client, "adm", "secret1")
    client.post(f"/admin/submissions/{sub_id}/reenter")
    with app.app_context():
        assert db.session.get(Submission, sub_id).locked is True


def test_live_frame_rejected_when_inactive(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam(app, admin_id)
    sub_id = start(app, client, exam_id)
    with app.app_context():
        db.session.get(Submission, sub_id).locked = True
        db.session.commit()
    r = client.post(f"/api/exam/{exam_id}/live", data={"kind": "screen"})
    assert r.status_code == 409 and r.get_json()["ok"] is False


def test_stream_end_reason(app):
    from safeexam.blueprints.ws import _end_reason
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam(app, admin_id)
    with app.app_context():
        sub = Submission(student_id=admin_id, exam_id=exam_id, started_at=utcnow())
        db.session.add(sub)
        db.session.commit()
        assert _end_reason(sub.id) is None
        sub.locked = True
        db.session.commit()
        assert _end_reason(sub.id) == "attempt locked"
        sub.completed = True
        db.session.commit()
        assert _end_reason(sub.id) == "attempt completed"


def test_replay_feed_has_cumulative_risk_and_review_page(app, client):
    admin_id = make_user(app, "adm", "secret1", ROLE_ADMIN)
    exam_id = make_exam(app, admin_id)
    sub_id = start(app, client, exam_id)
    client.post(f"/api/exam/{exam_id}/violation", json={"type": "Tab Switch"})
    with app.app_context():
        db.session.add(ProctorSnapshot(submission_id=sub_id, student_id=1, exam_id=exam_id,
                                       filename="x.jpg", flagged=True))
        db.session.commit()
    login(client, "adm", "secret1")
    feed = client.get(f"/api/submission/{sub_id}/replay").get_json()
    assert feed["violations"][0]["cum"] == 2
    page = client.get(f"/admin/exams/{exam_id}/review")
    assert page.status_code == 200 and b"1 student need review" in page.data
    assert client.get(f"/admin/exams/{exam_id}/stats").status_code == 200
    assert client.get(f"/admin/submissions/{sub_id}").status_code == 200
    assert client.get(f"/admin/submissions/{sub_id}/replay").status_code == 200
    assert client.get("/admin/live-wall").status_code == 200
