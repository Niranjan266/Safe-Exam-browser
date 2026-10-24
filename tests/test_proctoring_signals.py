"""Weights for the new proctoring signals and per-exam strictness presets."""
import os
import tempfile

import pytest

_DB_PATH = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["SEB_ENV"] = "testing"
os.environ["SEB_TEST_DATABASE_URI"] = "sqlite:///" + _DB_PATH

from safeexam import create_app, proctoring  # noqa: E402
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


def make_user(app, username, password, role):
    with app.app_context():
        u = User(username=username, role=role)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, username, password):
    client.get("/logout")
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=True)


def test_standard_keeps_base_weights():
    exam = Exam(strictness="standard")
    for vtype, base in proctoring.VIOLATION_SEVERITY.items():
        assert proctoring.severity_for(vtype, exam) == base
    assert proctoring.severity_for("Blocked Key", None) == 1


def test_relaxed_and_strict_presets():
    relaxed, strict = Exam(strictness="relaxed"), Exam(strictness="strict")
    assert proctoring.severity_for("Blocked Key", relaxed) == 0
    assert proctoring.severity_for("Window Resized", relaxed) == 0
    assert proctoring.severity_for("Tab Switch", strict) == 4
    assert proctoring.severity_for("Multiple Faces", strict) == 6
    assert proctoring.severity_for("DevTools Suspected", strict) == 3


def test_new_types_have_weights():
    assert proctoring.severity_for("Looking Away") == 1
    assert proctoring.severity_for("Multiple Monitors") == 3


def test_idle_focus_loss_weighs_more():
    assert proctoring.severity_for("Window Blur", None, "after idle 45s") == 3
    assert proctoring.severity_for("Tab Switch", None, "after idle 45s") == 3
    assert proctoring.severity_for("Window Blur", None, None) == 1


def test_exam_form_saves_strictness_and_api_applies_it(app, client):
    make_user(app, "adm", "secret1", ROLE_ADMIN)
    sid = make_user(app, "stu", "secret1", ROLE_STUDENT)
    login(client, "adm", "secret1")
    client.post("/admin/exams/new", data={
        "title": "Strict one", "duration": "15", "strictness": "strict",
    }, follow_redirects=True)
    with app.app_context():
        exam = Exam.query.filter_by(title="Strict one").first()
        assert exam.strictness == "strict"
        db.session.add(Question(exam_id=exam.id, text="Q", option_a="a", option_b="b",
                                option_c="c", option_d="d", correct_answer="A", order=0))
        exam.is_published = True
        db.session.commit()
        eid = exam.id
    login(client, "stu", "secret1")
    client.get(f"/exam/{eid}/start")
    r = client.post(f"/api/exam/{eid}/violation",
                    json={"type": "Window Blur", "details": "after idle 40s"})
    assert r.status_code == 200
    with app.app_context():
        sub = Submission.query.filter_by(exam_id=eid, student_id=sid).first()
        assert sub is not None
        # blur (1) lifted to 3 by the idle heuristic, doubled by "strict"
        assert proctoring.total_severity(sub) == 6
