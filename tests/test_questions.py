"""Question types, marks, negative marking, pools, option shuffle and result review."""
import os
import tempfile

import pytest

# Configure an isolated test database BEFORE importing the app.
_DB_PATH = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
os.environ["SEB_ENV"] = "testing"
os.environ["SEB_TEST_DATABASE_URI"] = "sqlite:///" + _DB_PATH

from safeexam import create_app  # noqa: E402
from safeexam.extensions import db  # noqa: E402
from safeexam.models import User, Exam, Question, Submission, ROLE_ADMIN, ROLE_STUDENT  # noqa: E402
from safeexam.services import submission_questions, option_order  # noqa: E402


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


def make_exam(app, admin_id, questions, **exam_kwargs):
    """questions: list of dicts of Question fields; returns (exam_id, [question ids])."""
    with app.app_context():
        exam = Exam(title="T", duration=10, created_by=admin_id, is_published=True, **exam_kwargs)
        db.session.add(exam)
        db.session.commit()
        ids = []
        for i, spec in enumerate(questions):
            fields = dict(text=f"Q{i}", option_a="a", option_b="b", option_c="c",
                          option_d="d", correct_answer="A", order=i)
            fields.update(spec)
            q = Question(exam_id=exam.id, **fields)
            db.session.add(q)
            db.session.flush()
            ids.append(q.id)
        db.session.commit()
        return exam.id, ids


def take(client, app, exam_id, form):
    login(client, "stu", "secret1")
    assert client.get(f"/exam/{exam_id}/start").status_code == 200
    r = client.post(f"/exam/{exam_id}/submit", data=form, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        return Submission.query.filter_by(exam_id=exam_id).first()


def test_all_question_types_and_marks(app, client):
    admin = make_user(app, "adm", "secret1", ROLE_ADMIN)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    exam_id, q = make_exam(app, admin, [
        {},                                                         # single, 1 mark
        {"qtype": "multiple", "correct_answer": "AC", "marks": 2},
        {"qtype": "truefalse", "option_a": "True", "option_b": "False",
         "option_c": "", "option_d": "", "correct_answer": "B"},
        {"qtype": "numeric", "correct_answer": "", "numeric_answer": 3.14,
         "tolerance": 0.01, "marks": 3, "image_url": "https://example.com/x.png"},
    ])
    sub = take(client, app, exam_id, {
        str(q[0]): "A", str(q[1]): ["A", "C"], str(q[2]): "B", str(q[3]): "3.145",
    })
    assert sub.score == 7 and sub.total == 7


def test_multiple_needs_exact_set_and_numeric_tolerance(app, client):
    admin = make_user(app, "adm", "secret1", ROLE_ADMIN)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    exam_id, q = make_exam(app, admin, [
        {"qtype": "multiple", "correct_answer": "AC", "marks": 2},
        {"qtype": "numeric", "correct_answer": "", "numeric_answer": 10, "tolerance": 0.5},
    ])
    sub = take(client, app, exam_id, {str(q[0]): ["A"], str(q[1]): "11"})
    assert sub.score == 0 and sub.total == 3


def test_negative_marking_only_hits_wrong_answers(app, client):
    admin = make_user(app, "adm", "secret1", ROLE_ADMIN)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    exam_id, q = make_exam(app, admin, [{"marks": 2}, {"marks": 2}, {}, {}],
                           negative_marking=0.5)
    # Q0 right (+2), Q1 wrong (-1), Q2 blank (0), Q3 right (+1)
    sub = take(client, app, exam_id, {str(q[0]): "A", str(q[1]): "B", str(q[3]): "A"})
    assert sub.score == 2 and sub.total == 6


def test_pool_draw_is_stable_and_graded_on_drawn_only(app, client):
    admin = make_user(app, "adm", "secret1", ROLE_ADMIN)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    exam_id, q = make_exam(app, admin, [{} for _ in range(6)], pool_size=2)
    login(client, "stu", "secret1")
    page1 = client.get(f"/exam/{exam_id}/start").get_data(as_text=True)
    page2 = client.get(f"/exam/{exam_id}/start").get_data(as_text=True)
    assert page1.count("question-card") == 2
    with app.app_context():
        sub = Submission.query.filter_by(exam_id=exam_id).first()
        drawn = [x.id for x in submission_questions(sub)]
        assert drawn == [x.id for x in submission_questions(sub)] and len(drawn) == 2
        undrawn = next(i for i in q if i not in drawn)
    assert page1.count('name="') == page2.count('name="')
    r = client.post(f"/exam/{exam_id}/submit",
                    data={str(drawn[0]): "A", str(undrawn): "A"}, follow_redirects=True)
    assert r.status_code == 200
    with app.app_context():
        sub = Submission.query.filter_by(exam_id=exam_id).first()
        assert sub.total == 2 and sub.score == 1


def test_option_shuffle_is_deterministic_and_grades_on_original_keys(app, client):
    admin = make_user(app, "adm", "secret1", ROLE_ADMIN)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    exam_id, q = make_exam(app, admin, [{"correct_answer": "B"}], shuffle_options=True)
    with app.app_context():
        question = db.session.get(Question, q[0])
        orders = set()
        for sub_id in range(1, 40):
            sub = Submission(id=sub_id, student_id=1, exam=db.session.get(Exam, exam_id))
            order = option_order(sub, question)
            assert order == option_order(sub, question)
            orders.add(tuple(order))
        assert len(orders) > 1 and all(sorted(o) == list("ABCD") for o in orders)
    sub = take(client, app, exam_id, {str(q[0]): "B"})
    assert sub.score == 1


def test_review_only_after_release(app, client):
    admin = make_user(app, "adm", "secret1", ROLE_ADMIN)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    exam_id, q = make_exam(app, admin, [{"text": "Capital?", "option_b": "Paris",
                                         "correct_answer": "B"}])
    take(client, app, exam_id, {str(q[0]): "A"})
    page = client.get(f"/exam/{exam_id}/result").get_data(as_text=True)
    assert "Your answers" not in page and "Paris" not in page
    with app.app_context():
        db.session.get(Exam, exam_id).results_released = True
        db.session.commit()
    page = client.get(f"/exam/{exam_id}/result").get_data(as_text=True)
    assert "Your answers" in page and "Paris" in page and "Incorrect" in page


def test_save_answer_api_normalizes_by_type(app, client):
    admin = make_user(app, "adm", "secret1", ROLE_ADMIN)
    make_user(app, "stu", "secret1", ROLE_STUDENT)
    exam_id, q = make_exam(app, admin, [
        {"qtype": "multiple", "correct_answer": "AB"},
        {"qtype": "numeric", "correct_answer": "", "numeric_answer": 1},
    ])
    login(client, "stu", "secret1")
    client.get(f"/exam/{exam_id}/start")
    url = f"/api/exam/{exam_id}/answer"
    client.post(url, json={"question_id": q[0], "selected": "CAA"})
    client.post(url, json={"question_id": q[1], "selected": "abc"})
    with app.app_context():
        sub = Submission.query.filter_by(exam_id=exam_id).first()
        saved = {a.question_id: a.selected for a in sub.answers}
    assert saved[q[0]] == "AC" and saved[q[1]] is None


def test_question_form_creates_typed_questions(app, client):
    admin = make_user(app, "adm", "secret1", ROLE_ADMIN)
    login(client, "adm", "secret1")
    exam_id, _ = make_exam(app, admin, [])
    url = f"/admin/exams/{exam_id}/questions"
    client.post(url, data={"qtype": "multiple", "text": "m", "marks": "2",
                           "option_a": "1", "option_b": "2", "option_c": "3", "option_d": "4",
                           "correct_multi": ["A", "D"], "correct_answer": "A"})
    client.post(url, data={"qtype": "truefalse", "text": "t", "correct_answer": "B"})
    client.post(url, data={"qtype": "numeric", "text": "n", "numeric_answer": "9",
                           "tolerance": "0.1", "correct_answer": "A"})
    client.post(url, data={"qtype": "numeric", "text": "bad", "correct_answer": "A"})
    with app.app_context():
        qs = Question.query.filter_by(exam_id=exam_id).order_by(Question.id).all()
        assert [x.kind for x in qs] == ["multiple", "truefalse", "numeric"]
        assert qs[0].correct_answer == "AD" and qs[0].weight == 2
        assert qs[1].correct_answer == "B" and qs[2].numeric_answer == 9
        first_id = qs[0].id
    assert client.get(f"/admin/questions/{first_id}/edit").status_code == 200
