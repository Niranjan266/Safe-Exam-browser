"""
Teacher portal.

Teachers are exam *authors*. They can:
  - create and maintain their own classes (subject name, code, section, year)
  - create, edit, publish and delete **their own** exams and questions
  - see the results and statistics of exams they created

They deliberately CANNOT:
  - watch the live screen / webcam of any student
  - manage users, edit global settings or read the audit log
  - touch another teacher's exams or classes

Every route below is gated by `teacher_required`, and every object lookup goes
through `_own_exam` / `_own_class`, which 404s anything that is not theirs.
"""
import csv
import io
from collections import Counter
from datetime import datetime

from flask import (
    Blueprint, render_template, redirect, url_for, flash, request, abort, Response,
)
from flask_login import current_user
from sqlalchemy import func

from ..extensions import db
from ..models import (
    Exam, Question, Submission, Answer, Violation, User, SchoolClass, ROLE_STUDENT,
)
from ..forms import ExamForm, QuestionForm, ImportQuestionsForm, ClassForm
from ..decorators import teacher_required
from ..audit import log_action
from .. import settings as settings_store

teacher_bp = Blueprint("teacher", __name__, url_prefix="/teacher")


# ----------------------------------------------------------------------
# Ownership helpers — the single place that enforces "own content only".
# ----------------------------------------------------------------------

def _own_exam(exam_id):
    exam = db.session.get(Exam, exam_id)
    if exam is None or exam.created_by != current_user.id:
        abort(404)
    return exam


def _own_class(class_id):
    cls = db.session.get(SchoolClass, class_id)
    if cls is None or cls.teacher_id != current_user.id:
        abort(404)
    return cls


def _own_question(q_id):
    q = db.session.get(Question, q_id)
    if q is None:
        abort(404)
    _own_exam(q.exam_id)
    return q


def _my_exams_query():
    return Exam.query.filter_by(created_by=current_user.id)


def _my_class_choices():
    rows = (
        SchoolClass.query.filter_by(teacher_id=current_user.id, active=True)
        .order_by(SchoolClass.name)
        .all()
    )
    return [(0, "— No class —")] + [(c.id, c.label) for c in rows]


def _pass_mark_for(exam):
    if exam.pass_mark is not None:
        return exam.pass_mark
    return settings_store.get_int("pass_mark")


def _percent(score, total):
    return round((score or 0) / total * 100) if total else 0


# ======================= Dashboard =======================

@teacher_bp.route("/")
@teacher_required
def dashboard():
    my_exams = _my_exams_query().all()
    exam_ids = [e.id for e in my_exams] or [0]
    subs = Submission.query.filter(Submission.exam_id.in_(exam_ids)).all()
    completed = [s for s in subs if s.completed]

    percents = [
        _percent(s.score, s.total or s.exam.question_count) for s in completed
    ]
    stats = {
        "classes": SchoolClass.query.filter_by(teacher_id=current_user.id).count(),
        "exams": len(my_exams),
        "published": sum(1 for e in my_exams if e.is_published),
        "questions": sum(e.question_count for e in my_exams),
        "submissions": len(subs),
        "completed": len(completed),
        "in_progress": sum(1 for s in subs if s.is_active),
        "avg": round(sum(percents) / len(percents), 1) if percents else 0,
    }
    recent = (
        Submission.query.filter(Submission.exam_id.in_(exam_ids))
        .order_by(Submission.started_at.desc())
        .limit(8)
        .all()
    )
    names = {
        u.id: (u.full_name or u.username)
        for u in User.query.filter(User.id.in_([s.student_id for s in recent] or [0])).all()
    }
    return render_template(
        "teacher/dashboard.html",
        stats=stats,
        recent=recent,
        names=names,
        classes=SchoolClass.query.filter_by(teacher_id=current_user.id)
        .order_by(SchoolClass.name).limit(6).all(),
    )


# ======================= Classes =======================

@teacher_bp.route("/classes")
@teacher_required
def classes():
    rows = (
        SchoolClass.query.filter_by(teacher_id=current_user.id)
        .order_by(SchoolClass.active.desc(), SchoolClass.name)
        .all()
    )
    return render_template("shared/classes.html", classes=rows, teachers=None)


@teacher_bp.route("/classes/new", methods=["GET", "POST"])
@teacher_required
def class_new():
    form = ClassForm()
    del form.teacher_id  # a teacher always owns the classes they create
    if form.validate_on_submit():
        cls = SchoolClass(teacher_id=current_user.id)
        form.populate_obj(cls)
        db.session.add(cls)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash(
                "A class with that subject code, section and academic year already exists.",
                "danger",
            )
            return render_template("shared/class_form.html", form=form, cls=None)
        log_action("class.create", target=cls.id, detail=cls.subject_code)
        flash("Class created.", "success")
        return redirect(url_for("teacher.classes"))
    return render_template("shared/class_form.html", form=form, cls=None)


@teacher_bp.route("/classes/<int:class_id>/edit", methods=["GET", "POST"])
@teacher_required
def class_edit(class_id):
    cls = _own_class(class_id)
    form = ClassForm(obj=cls)
    del form.teacher_id
    if form.validate_on_submit():
        form.populate_obj(cls)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("Another class already uses that code / section / year.", "danger")
            return render_template("shared/class_form.html", form=form, cls=cls)
        log_action("class.update", target=cls.id, detail=cls.subject_code)
        flash("Class updated.", "success")
        return redirect(url_for("teacher.classes"))
    return render_template("shared/class_form.html", form=form, cls=cls)


@teacher_bp.route("/classes/<int:class_id>/toggle", methods=["POST"])
@teacher_required
def class_toggle(class_id):
    cls = _own_class(class_id)
    cls.active = not cls.active
    db.session.commit()
    log_action("class.activate" if cls.active else "class.deactivate", target=cls.id)
    flash(f"Class {'activated' if cls.active else 'archived'}.", "info")
    return redirect(url_for("teacher.classes"))


@teacher_bp.route("/classes/<int:class_id>/delete", methods=["POST"])
@teacher_required
def class_delete(class_id):
    cls = _own_class(class_id)
    if cls.exam_count:
        flash(
            f"This class still has {cls.exam_count} exam(s). Move or delete them first.",
            "warning",
        )
        return redirect(url_for("teacher.classes"))
    code = cls.subject_code
    db.session.delete(cls)
    db.session.commit()
    log_action("class.delete", target=class_id, detail=code)
    flash("Class deleted.", "info")
    return redirect(url_for("teacher.classes"))


# ======================= Exams =======================

@teacher_bp.route("/exams")
@teacher_required
def exams():
    rows = _my_exams_query().order_by(Exam.created_at.desc()).all()
    return render_template("shared/exams.html", exams=rows)


@teacher_bp.route("/exams/new", methods=["GET", "POST"])
@teacher_required
def exam_new():
    form = ExamForm()
    form.class_id.choices = _my_class_choices()
    if form.validate_on_submit():
        exam = Exam(created_by=current_user.id)
        form.populate_obj(exam)
        if not exam.class_id:
            exam.class_id = None
        db.session.add(exam)
        db.session.commit()
        log_action("exam.create", target=exam.id, detail=exam.title)
        flash("Exam created. Now add some questions.", "success")
        return redirect(url_for("teacher.questions", exam_id=exam.id))
    return render_template("shared/exam_form.html", form=form, exam=None)


@teacher_bp.route("/exams/<int:exam_id>/edit", methods=["GET", "POST"])
@teacher_required
def exam_edit(exam_id):
    exam = _own_exam(exam_id)
    form = ExamForm(obj=exam)
    form.class_id.choices = _my_class_choices()
    if form.validate_on_submit():
        form.populate_obj(exam)
        if not exam.class_id:
            exam.class_id = None
        db.session.commit()
        log_action("exam.update", target=exam.id, detail=exam.title)
        flash("Exam updated.", "success")
        return redirect(url_for("teacher.exams"))
    return render_template("shared/exam_form.html", form=form, exam=exam)


@teacher_bp.route("/exams/<int:exam_id>/publish", methods=["POST"])
@teacher_required
def exam_publish(exam_id):
    exam = _own_exam(exam_id)
    if exam.question_count == 0 and not exam.is_published:
        flash("Add at least one question before publishing.", "warning")
        return redirect(url_for("teacher.exams"))
    exam.is_published = not exam.is_published
    db.session.commit()
    log_action("exam.publish" if exam.is_published else "exam.unpublish", target=exam.id)
    flash(f"Exam {'published' if exam.is_published else 'unpublished'}.", "info")
    return redirect(url_for("teacher.exams"))


@teacher_bp.route("/exams/<int:exam_id>/clone", methods=["POST"])
@teacher_required
def exam_clone(exam_id):
    exam = _own_exam(exam_id)
    copy = Exam(
        title=f"{exam.title} (copy)", description=exam.description, duration=exam.duration,
        created_by=current_user.id, class_id=exam.class_id, is_published=False,
        shuffle_questions=exam.shuffle_questions, max_violations=exam.max_violations,
        pass_mark=exam.pass_mark,
    )
    db.session.add(copy)
    db.session.commit()
    for q in exam.questions:
        db.session.add(Question(
            exam_id=copy.id, text=q.text, option_a=q.option_a, option_b=q.option_b,
            option_c=q.option_c, option_d=q.option_d,
            correct_answer=q.correct_answer, order=q.order,
        ))
    db.session.commit()
    log_action("exam.clone", target=copy.id, detail=f"from {exam.id}")
    flash("Exam duplicated as a draft.", "success")
    return redirect(url_for("teacher.questions", exam_id=copy.id))


@teacher_bp.route("/exams/<int:exam_id>/delete", methods=["POST"])
@teacher_required
def exam_delete(exam_id):
    exam = _own_exam(exam_id)
    title = exam.title
    db.session.delete(exam)
    db.session.commit()
    log_action("exam.delete", target=exam_id, detail=title)
    flash("Exam deleted.", "info")
    return redirect(url_for("teacher.exams"))


# ======================= Questions =======================

@teacher_bp.route("/exams/<int:exam_id>/questions", methods=["GET", "POST"])
@teacher_required
def questions(exam_id):
    exam = _own_exam(exam_id)
    form = QuestionForm()
    import_form = ImportQuestionsForm()
    if form.validate_on_submit():
        next_order = (
            db.session.query(func.coalesce(func.max(Question.order), 0))
            .filter_by(exam_id=exam.id).scalar() + 1
        )
        q = Question(exam_id=exam.id, order=next_order)
        form.populate_obj(q)
        db.session.add(q)
        db.session.commit()
        log_action("question.create", target=q.id, detail=f"exam {exam.id}")
        flash("Question added.", "success")
        return redirect(url_for("teacher.questions", exam_id=exam.id))
    return render_template(
        "shared/questions.html", exam=exam, form=form, import_form=import_form
    )


@teacher_bp.route("/exams/<int:exam_id>/questions/import", methods=["POST"])
@teacher_required
def questions_import(exam_id):
    exam = _own_exam(exam_id)
    form = ImportQuestionsForm()
    if not form.validate_on_submit():
        flash("Nothing to import.", "warning")
        return redirect(url_for("teacher.questions", exam_id=exam.id))

    added, errors = 0, 0
    order = db.session.query(
        func.coalesce(func.max(Question.order), 0)
    ).filter_by(exam_id=exam.id).scalar()
    for row in csv.reader(io.StringIO(form.data.data.strip())):
        row = [c.strip() for c in row]
        if len(row) < 6 or not row[0]:
            if any(row):
                errors += 1
            continue
        correct = row[5].upper()[:1]
        if correct not in {"A", "B", "C", "D"}:
            errors += 1
            continue
        order += 1
        db.session.add(Question(
            exam_id=exam.id, text=row[0], option_a=row[1], option_b=row[2],
            option_c=row[3], option_d=row[4], correct_answer=correct, order=order,
        ))
        added += 1
    db.session.commit()
    log_action("question.import", target=exam.id, detail=f"+{added}")
    flash(
        f"Imported {added} question(s)." + (f" {errors} row(s) skipped." if errors else ""),
        "success" if added else "warning",
    )
    return redirect(url_for("teacher.questions", exam_id=exam.id))


@teacher_bp.route("/questions/<int:q_id>/edit", methods=["GET", "POST"])
@teacher_required
def question_edit(q_id):
    q = _own_question(q_id)
    form = QuestionForm(obj=q)
    if form.validate_on_submit():
        form.populate_obj(q)
        db.session.commit()
        log_action("question.update", target=q.id)
        flash("Question updated.", "success")
        return redirect(url_for("teacher.questions", exam_id=q.exam_id))
    return render_template("shared/question_form.html", form=form, question=q)


@teacher_bp.route("/questions/<int:q_id>/delete", methods=["POST"])
@teacher_required
def question_delete(q_id):
    q = _own_question(q_id)
    exam_id = q.exam_id
    db.session.delete(q)
    db.session.commit()
    log_action("question.delete", target=q_id)
    flash("Question deleted.", "info")
    return redirect(url_for("teacher.questions", exam_id=exam_id))


# ======================= Results (own exams only) =======================

@teacher_bp.route("/results")
@teacher_required
def results():
    q = (request.args.get("q") or "").strip()
    exam_id = request.args.get("exam_id", type=int)
    my_ids = [e.id for e in _my_exams_query().all()] or [0]

    query = (
        db.session.query(Submission, User.username, Exam.title)
        .join(User, User.id == Submission.student_id)
        .join(Exam, Exam.id == Submission.exam_id)
        .filter(Submission.exam_id.in_(my_ids))
    )
    if q:
        query = query.filter(
            db.or_(User.username.ilike(f"%{q}%"), Exam.title.ilike(f"%{q}%"))
        )
    if exam_id:
        query = query.filter(Submission.exam_id == exam_id)
    rows = query.order_by(Submission.started_at.desc()).all()
    return render_template(
        "shared/results.html",
        rows=rows,
        exams=_my_exams_query().order_by(Exam.title).all(),
        q=q,
        exam_id=exam_id,
    )


@teacher_bp.route("/exams/<int:exam_id>/stats")
@teacher_required
def exam_stats(exam_id):
    exam = _own_exam(exam_id)
    completed = [s for s in exam.submissions if s.completed]
    pass_mark = _pass_mark_for(exam)

    percents = [_percent(s.score, s.total or exam.question_count) for s in completed]
    avg = round(sum(percents) / len(percents), 1) if percents else 0
    passed = sum(1 for p in percents if p >= pass_mark)
    pass_rate = round(passed / len(percents) * 100) if percents else 0

    bands = [0] * 10
    for p in percents:
        bands[min(9, p // 10)] += 1

    items = []
    sub_ids = [s.id for s in completed]
    for q in exam.questions:
        if sub_ids:
            answered = Answer.query.filter(
                Answer.question_id == q.id, Answer.submission_id.in_(sub_ids)
            ).all()
            picks = Counter(a.selected for a in answered if a.selected)
            attempts = sum(picks.values())
            correct = picks.get(q.correct_answer, 0)
        else:
            picks, attempts, correct = Counter(), 0, 0
        items.append({
            "q": q, "attempts": attempts, "correct": correct,
            "rate": round(correct / attempts * 100) if attempts else 0, "picks": picks,
        })

    return render_template(
        "shared/exam_stats.html",
        exam=exam, completed=len(completed), avg=avg, pass_rate=pass_rate,
        pass_mark=pass_mark,
        dist={"labels": [f"{i*10}-{i*10+9}%" for i in range(10)], "values": bands},
        items=items,
    )


@teacher_bp.route("/submissions/<int:sub_id>")
@teacher_required
def submission_detail(sub_id):
    sub = db.session.get(Submission, sub_id) or abort(404)
    _own_exam(sub.exam_id)  # 404 unless the exam belongs to this teacher
    student = db.session.get(User, sub.student_id)
    saved = {a.question_id: a.selected for a in sub.answers}
    violations = (
        Violation.query.filter_by(submission_id=sub.id)
        .order_by(Violation.timestamp.asc()).all()
    )
    return render_template(
        "shared/submission_detail.html",
        sub=sub, student=student, saved=saved, violations=violations,
        status=sub.status_label(), pass_mark=_pass_mark_for(sub.exam),
        percent=_percent(sub.score, sub.total or sub.exam.question_count),
    )


@teacher_bp.route("/report.csv")
@teacher_required
def report_csv():
    exam_id = request.args.get("exam_id", type=int)
    my_ids = [e.id for e in _my_exams_query().all()] or [0]
    if exam_id and exam_id not in my_ids:
        abort(404)

    query = (
        db.session.query(Submission, User.username, Exam.title)
        .join(User, User.id == Submission.student_id)
        .join(Exam, Exam.id == Submission.exam_id)
        .filter(Submission.exam_id.in_([exam_id] if exam_id else my_ids))
    )
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Submission", "Student", "Exam", "Score", "Total", "Percent",
        "Status", "Violations", "Started", "Completed",
    ])
    for sub, username, exam_title in query.order_by(Submission.started_at.desc()).all():
        writer.writerow([
            sub.id, username, exam_title,
            sub.score if sub.score is not None else "",
            sub.total if sub.total is not None else "",
            _percent(sub.score, sub.total) if sub.total else "",
            sub.status_label(), sub.violation_count,
            sub.started_at.strftime("%Y-%m-%d %H:%M") if sub.started_at else "",
            sub.completed_at.strftime("%Y-%m-%d %H:%M") if sub.completed_at else "",
        ])
    fname = f"exam{exam_id}.csv" if exam_id else "my_exams.csv"
    return Response(
        output.getvalue(), mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=safeexam_{fname}"},
    )
