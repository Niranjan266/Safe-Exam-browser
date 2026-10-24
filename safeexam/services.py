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


# ---------------------------------------------------------------------------
# Proctor interventions
# ---------------------------------------------------------------------------

MAX_EXTRA_MINUTES = 240


def grant_extra_time(submission, minutes):
    """Add `minutes` to one attempt's clock. Returns the minutes applied."""
    minutes = max(1, min(MAX_EXTRA_MINUTES, int(minutes)))
    submission.extra_seconds = (submission.extra_seconds or 0) + minutes * 60
    db.session.commit()
    return minutes


def reenter_submission(submission):
    """
    Reactivate a locked/disqualified attempt so the candidate can resume with
    their saved answers. Completed attempts cannot be reopened. Returns False
    when the attempt is not eligible. Records no Violation.
    """
    if submission.completed or not (submission.locked or submission.disqualified):
        return False
    submission.locked = False
    submission.disqualified = False
    submission.termination_reason = None
    submission.score = None
    submission.last_heartbeat = utcnow()
    submission.reentered_at = utcnow()
    db.session.commit()
    return True


def review_summary(exam):
    """
    Attempts of one exam that need a human look, riskiest first.

    An attempt needs review when it was disqualified or locked, carries a
    flagged still, had a burst of violations inside the risk window, or has
    used at least half of its violation budget.
    """
    from collections import Counter

    from . import proctoring, settings
    from .models import User

    limit = proctoring.max_violations_for(exam)
    window = settings.get_int("risk_window_seconds")
    threshold = settings.get_int("risk_window_threshold")
    rows = []
    for sub in exam.submissions:
        times = sorted(v.timestamp for v in sub.violations if v.timestamp)
        burst = False
        lo = 0
        for hi in range(len(times)):
            while (times[hi] - times[lo]).total_seconds() > window:
                lo += 1
            if hi - lo + 1 >= threshold:
                burst = True
                break
        flagged = [s for s in sub.snapshots if s.flagged]
        severity = proctoring.total_severity(sub)
        counts = Counter(v.violation_type for v in sub.violations)
        needs = bool(
            sub.disqualified or sub.locked or flagged or burst
            or (sub.violations and severity * 2 >= limit)
        )
        if not needs:
            continue
        student = db.session.get(User, sub.student_id)
        rows.append({
            "sub": sub,
            "student": (student.full_name or student.username) if student else "Unknown",
            "severity": severity,
            "limit": limit,
            "counts": sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])),
            "violations": len(sub.violations),
            "high_risk": burst,
            "flagged": flagged,
        })
    rows.sort(key=lambda r: (-r["severity"], -r["violations"], r["sub"].id))
    return rows
