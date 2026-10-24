"""Exam attempt services shared by the student views and the JSON API."""

import random

from .extensions import db
from .models import Submission, Answer, Question
from .timeutil import utcnow


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
            started_at=utcnow(),
            last_heartbeat=utcnow(),
        )
        db.session.add(sub)
        db.session.commit()
    return sub


def fmt_marks(value):
    """Marks for display: 3.0 -> '3', 2.5 -> '2.5'."""
    if value is None:
        return "0"
    return f"{round(float(value), 2):g}"


def submission_questions(submission):
    """The questions this attempt covers, in display order.

    A pool (``exam.pool_size`` > 0) draws a random subset, then the optional
    shuffle orders it. Both are seeded by the submission id, so a reload shows
    the same set in the same order and grading sees exactly what was shown.
    """
    exam = submission.exam
    questions = list(exam.questions)
    pool = exam.pool_size or 0
    if 0 < pool < len(questions):
        picked = {q.id for q in random.Random(f"pool:{submission.id}").sample(questions, pool)}
        questions = [q for q in questions if q.id in picked]
    if exam.shuffle_questions:
        random.Random(submission.id).shuffle(questions)
    return questions


def option_order(submission, question):
    """Option keys in the order this student sees them (grading uses the keys)."""
    keys = [k for k, _ in question.options()]
    if submission.exam.shuffle_options and question.kind in ("single", "multiple"):
        random.Random(f"opt:{submission.id}:{question.id}").shuffle(keys)
    return keys


def _parse_number(value):
    try:
        number = float(str(value).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def normalize_answer(question, selected):
    """Clean a raw posted answer for this question type; None means blank."""
    kind = question.kind
    if kind == "numeric":
        number = _parse_number(selected)
        return None if number is None else f"{number:g}"
    if kind == "multiple":
        if isinstance(selected, str):
            selected = list(selected)
        if not isinstance(selected, (list, tuple)):
            return None
        letters = sorted({s for s in selected if s in ("A", "B", "C", "D")})
        return "".join(letters) or None
    allowed = {"A", "B"} if kind == "truefalse" else {"A", "B", "C", "D"}
    if not isinstance(selected, str) or selected not in allowed:
        return None
    return selected


def is_correct(question, selected):
    if not selected:
        return False
    if question.kind == "numeric":
        number, target = _parse_number(selected), question.numeric_answer
        if number is None or target is None:
            return False
        return abs(number - target) <= (question.tolerance or 0) + 1e-9
    if question.kind == "multiple":
        return set(selected) == set(question.correct_answer)
    return selected == question.correct_answer


def marks_for(question, selected, negative=0.0):
    """Marks earned for one answer: full, zero for blank, or a deduction."""
    if not selected:
        return 0.0
    if is_correct(question, selected):
        return question.weight
    return -question.weight * (negative or 0.0)


def upsert_answer(submission, question_id, selected):
    """Save (or update) the student's answer for one question."""
    question = db.session.get(Question, question_id)
    selected = normalize_answer(question, selected) if question else None
    ans = Answer.query.filter_by(submission_id=submission.id, question_id=question_id).first()
    if ans is None:
        ans = Answer(submission_id=submission.id, question_id=question_id, selected=selected)
        db.session.add(ans)
    else:
        ans.selected = selected
    return ans


def grade_submission(submission, reason=None, commit=True):
    """Grade an attempt from its saved answers and mark it complete."""
    questions = submission_questions(submission)
    saved = {a.question_id: a.selected for a in submission.answers}
    negative = submission.exam.negative_marking or 0.0
    score = sum(marks_for(q, saved.get(q.id), negative) for q in questions)
    score = round(max(0.0, score), 2)

    submission.score = score
    submission.total = round(sum(q.weight for q in questions), 2)
    submission.completed = True
    submission.completed_at = utcnow()
    if reason and not submission.termination_reason:
        submission.termination_reason = reason

    # The attempt is over, so the "latest live frame" is no longer live. Drop it:
    # it would otherwise sit on disk indefinitely and — because submission ids can
    # be reused — could surface under a later candidate's tile.
    # Webcam snapshots are deliberately kept; they are the reviewable evidence.
    try:
        from . import framestore

        framestore.clear_live_frames(submission.id)
    except Exception:  # never let cleanup block grading
        pass

    if commit:
        db.session.commit()
    return score
