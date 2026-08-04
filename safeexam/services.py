"""Exam attempt services shared by the student views and the JSON API."""
from datetime import datetime

from .extensions import db
from .models import Submission, Answer


def get_active_submission(student_id, exam_id):
    return (
        Submission.query.filter_by(student_id=student_id, exam_id=exam_id, completed=False)
        .order_by(Submission.id.desc())
        .first()
    )


def get_completed_submission(student_id, exam_id):
    return (
        Submission.query.filter_by(student_id=student_id, exam_id=exam_id, completed=True)
        .order_by(Submission.completed_at.desc())
        .first()
    )


def start_or_resume(student_id, exam):
    """Return the student's active attempt, creating one if needed."""
    sub = get_active_submission(student_id, exam.id)
    if sub is None:
        sub = Submission(
            student_id=student_id,
            exam_id=exam.id,
            started_at=datetime.utcnow(),
            last_heartbeat=datetime.utcnow(),
        )
        db.session.add(sub)
        db.session.commit()
    return sub


def upsert_answer(submission, question_id, selected):
    """Save (or update) the student's selected option for one question."""
    if selected not in {"A", "B", "C", "D", None}:
        selected = None
    ans = Answer.query.filter_by(submission_id=submission.id, question_id=question_id).first()
    if ans is None:
        ans = Answer(submission_id=submission.id, question_id=question_id, selected=selected)
        db.session.add(ans)
    else:
        ans.selected = selected
    return ans


def grade_submission(submission, reason=None, commit=True):
    """Grade an attempt from its saved answers and mark it complete."""
    questions = submission.exam.questions
    saved = {a.question_id: a.selected for a in submission.answers}
    score = sum(1 for q in questions if saved.get(q.id) == q.correct_answer)

    submission.score = score
    submission.total = len(questions)
    submission.completed = True
    submission.completed_at = datetime.utcnow()
    if reason and not submission.termination_reason:
        submission.termination_reason = reason
    if commit:
        db.session.commit()
    return score
