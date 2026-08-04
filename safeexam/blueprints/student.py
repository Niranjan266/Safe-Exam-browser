"""Student-facing views: dashboard and the locked-down exam flow."""
import random
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, flash, request, abort
from flask_login import login_required, current_user

from ..extensions import db
from ..models import Exam
from ..decorators import student_required
from ..services import (
    start_or_resume,
    get_active_submission,
    get_completed_submission,
    upsert_answer,
    grade_submission,
)

student_bp = Blueprint("student", __name__)


@student_bp.route("/student")
@student_required
def dashboard():
    exams = Exam.query.filter_by(is_published=True).order_by(Exam.created_at.desc()).all()
    now = datetime.utcnow()

    cards = []
    for exam in exams:
        completed = get_completed_submission(current_user.id, exam.id)
        active = get_active_submission(current_user.id, exam.id)
        cards.append(
            {
                "exam": exam,
                "available": exam.is_available_now(now),
                "availability": exam.availability_label(now),
                "completed": completed,
                "in_progress": active is not None,
            }
        )
    return render_template("student/dashboard.html", cards=cards)


@student_bp.route("/exam/<int:exam_id>/start")
@student_required
def start_exam(exam_id):
    exam = db.session.get(Exam, exam_id) or abort(404)

    # Already finished? show the result.
    if get_completed_submission(current_user.id, exam_id):
        return redirect(url_for("student.result", exam_id=exam_id))

    if not exam.is_available_now():
        flash("This exam is not currently available.", "warning")
        return redirect(url_for("student.dashboard"))

    if exam.question_count == 0:
        flash("This exam has no questions yet. Please check back later.", "warning")
        return redirect(url_for("student.dashboard"))

    submission = start_or_resume(current_user.id, exam)

    # Locked or disqualified by a proctor -> locked screen.
    if submission.locked or submission.disqualified:
        return redirect(url_for("student.locked", exam_id=exam_id))

    # Time already up -> auto-grade and show result.
    if submission.is_expired():
        grade_submission(submission, reason="Time expired")
        flash("Your time for this exam has ended.", "info")
        return redirect(url_for("student.result", exam_id=exam_id))

    questions = list(exam.questions)
    if exam.shuffle_questions:
        random.seed(submission.id)  # stable shuffle across refreshes for this attempt
        random.shuffle(questions)

    # Resume any previously saved answers.
    saved = {a.question_id: a.selected for a in submission.answers}

    return render_template(
        "student/exam.html",
        exam=exam,
        questions=questions,
        submission=submission,
        saved=saved,
        seconds_remaining=submission.seconds_remaining(),
    )


@student_bp.route("/exam/<int:exam_id>/submit", methods=["POST"])
@student_required
def submit_exam(exam_id):
    exam = db.session.get(Exam, exam_id) or abort(404)
    submission = get_active_submission(current_user.id, exam_id)

    if submission is None:
        # Nothing active (already submitted or never started).
        return redirect(url_for("student.result", exam_id=exam_id))

    # Capture answers from the posted form (belt-and-braces over JS autosave).
    for question in exam.questions:
        selected = request.form.get(str(question.id))
        if selected in {"A", "B", "C", "D"}:
            upsert_answer(submission, question.id, selected)
    db.session.commit()

    reason = None
    if submission.disqualified:
        reason = submission.termination_reason or "Disqualified"
    elif submission.is_expired():
        reason = "Time expired"

    grade_submission(submission, reason=reason)
    return redirect(url_for("student.result", exam_id=exam_id))


@student_bp.route("/exam/<int:exam_id>/result")
@student_required
def result(exam_id):
    exam = db.session.get(Exam, exam_id) or abort(404)
    submission = get_completed_submission(current_user.id, exam_id)
    if submission is None:
        flash("No completed attempt found for this exam.", "info")
        return redirect(url_for("student.dashboard"))

    total = submission.total or exam.question_count or 1
    percentage = round((submission.score or 0) / total * 100) if total else 0
    return render_template(
        "student/result.html",
        exam=exam,
        submission=submission,
        score=submission.score or 0,
        total=total,
        percentage=percentage,
    )


@student_bp.route("/exam/<int:exam_id>/locked")
@student_required
def locked(exam_id):
    exam = db.session.get(Exam, exam_id) or abort(404)
    submission = (
        get_active_submission(current_user.id, exam_id)
        or get_completed_submission(current_user.id, exam_id)
    )
    reason = submission.termination_reason if submission else "This attempt has been locked."
    return render_template("student/locked.html", exam=exam, reason=reason)
