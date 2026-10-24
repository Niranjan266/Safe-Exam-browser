"""Class enrolment and bulk student import."""
import io
import os
import tempfile

import pytest

_DB_PATH = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["SEB_ENV"] = "testing"
os.environ["SEB_TEST_DATABASE_URI"] = "sqlite:///" + _DB_PATH

from safeexam import create_app  # noqa: E402
from safeexam.extensions import db  # noqa: E402
from safeexam.models import (  # noqa: E402
    User, Exam, Question, SchoolClass, ROLE_ADMIN, ROLE_STUDENT, ROLE_TEACHER,
)


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


def make_user(app, username, role, password="pw123456"):
    with app.app_context():
        u = User(username=username, role=role)
        u.set_password(password)
        db.session.add(u)
        db.session.commit()
        return u.id


def login(client, username, password="pw123456"):
    client.get("/logout", follow_redirects=True)
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=True)


def make_class(app, teacher_id, code="C1"):
    with app.app_context():
        c = SchoolClass(name="Class " + code, subject_name="Sub", subject_code=code,
                        teacher_id=teacher_id)
        db.session.add(c)
        db.session.commit()
        return c.id


def make_exam(app, creator_id, class_id=None, title="E"):
    with app.app_context():
        e = Exam(title=title, duration=10, is_published=True,
                 created_by=creator_id, class_id=class_id)
        db.session.add(e)
        db.session.flush()
        db.session.add(Question(exam_id=e.id, text="q", option_a="a", option_b="b",
                                option_c="c", option_d="d", correct_answer="A"))
        db.session.commit()
        return e.id


def csv_upload(text, class_id=0):
    return {"csv_file": (io.BytesIO(text.encode()), "s.csv"), "class_id": str(class_id)}


def test_teacher_enrols_any_student_and_dashboard_filters(app, client):
    t = make_user(app, "teach", ROLE_TEACHER)
    make_user(app, "other", ROLE_TEACHER)
    s_in = make_user(app, "in", ROLE_STUDENT)
    s_out = make_user(app, "out", ROLE_STUDENT)
    cid = make_class(app, t)
    open_exam = make_exam(app, t, None, "OpenExam")
    class_exam = make_exam(app, t, cid, "ClassExam")

    login(client, "teach")
    r = client.post(f"/teacher/classes/{cid}/students/add", data={"user_ids": [s_in]})
    assert r.status_code == 302
    assert b"in" in client.get(f"/teacher/classes/{cid}/students").data

    login(client, "in")
    page = client.get("/student").data
    assert b"OpenExam" in page and b"ClassExam" in page

    login(client, "out")
    page = client.get("/student").data
    assert b"OpenExam" in page and b"ClassExam" not in page
    r = client.get(f"/exam/{class_exam}/start")
    assert r.status_code == 302 and r.headers["Location"].endswith("/student")
    assert client.get(f"/exam/{open_exam}/start").status_code != 404

    # other teacher cannot touch this class
    login(client, "other")
    assert client.get(f"/teacher/classes/{cid}/students").status_code == 404
    assert client.post(f"/teacher/classes/{cid}/students/add",
                       data={"user_ids": [s_out]}).status_code == 404


def test_remove_and_admin_enrol(app, client):
    t = make_user(app, "teach", ROLE_TEACHER)
    a = make_user(app, "adm", ROLE_ADMIN)
    s = make_user(app, "stu", ROLE_STUDENT)
    cid = make_class(app, t)
    login(client, "adm")
    client.post(f"/admin/classes/{cid}/students/add", data={"user_ids": [s]})
    with app.app_context():
        assert len(db.session.get(SchoolClass, cid).students) == 1
    client.post(f"/admin/classes/{cid}/students/{s}/remove")
    with app.app_context():
        assert db.session.get(SchoolClass, cid).students == []
    assert a


def test_teacher_import_with_class_and_one_time_credentials(app, client):
    t = make_user(app, "teach", ROLE_TEACHER)
    cid = make_class(app, t)
    make_user(app, "exists", ROLE_STUDENT)
    login(client, "teach")
    text = ("username,full_name,email\n"
            "amy,Amy A,amy@x.com\n"
            "bob,Bob B,\n"
            "carl,Carl C,carl@x.com,secret99\n"
            "exists,Dup,\n"
            "amy,Again,\n"
            ",NoName,\n"
            "bad,Bad,notanemail\n")
    r = client.post("/teacher/students/import", data=csv_upload(text, cid),
                    content_type="multipart/form-data")
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert "/teacher/students/credentials/" in loc
    page = client.get(loc).data
    assert b"amy" in page and b"carl" in page and b"secret99" in page
    # shown only once
    again = client.get(loc, follow_redirects=True)
    assert b"secret99" not in again.data
    with app.app_context():
        amy = User.query.filter_by(username="amy").first()
        assert amy.role == ROLE_STUDENT and amy.created_by_id == t
        assert len(amy.password_hash) > 20 and "secret99" not in amy.password_hash
        assert User.query.filter_by(username="carl").first().check_password("secret99")
        assert {s.username for s in db.session.get(SchoolClass, cid).students} == {"amy", "bob", "carl"}
        assert User.query.filter_by(username="bad").first() is None


def test_admin_import_generates_8_char_passwords(app, client):
    make_user(app, "adm", ROLE_ADMIN)
    login(client, "adm")
    r = client.post("/admin/users/import", data=csv_upload("zed,Zed Z,z@x.com\n"),
                    content_type="multipart/form-data")
    page = client.get(r.headers["Location"]).data.decode()
    pw = page.split("<code>")[1].split("</code>")[0]
    assert len(pw) == 8
    assert login(client, "zed", pw).status_code == 200
    with app.app_context():
        assert User.query.filter_by(username="zed").first().check_password(pw)


def test_import_rejects_missing_file(app, client):
    make_user(app, "teach", ROLE_TEACHER)
    login(client, "teach")
    r = client.post("/teacher/students/import", data={}, content_type="multipart/form-data")
    assert r.status_code == 302 and r.headers["Location"].endswith("/teacher/students")
