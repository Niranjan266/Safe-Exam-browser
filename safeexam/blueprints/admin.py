"""Admin / proctor views: exam authoring, users, settings, monitoring,
analytics, results and reports."""
import csv
import io
import os
from collections import defaultdict, Counter
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    render_template,
    redirect,
    url_for,
    flash,
    request,
    abort,
    send_file,
    send_from_directory,
    current_app,
    Response,
)
from flask_login import login_required, current_user
from sqlalchemy import func

from ..extensions import db
from ..models import (
    Exam, Question, Submission, Answer, Violation, ProctorSnapshot, User, AuditLog,
    SchoolClass, ROLE_STUDENT, ROLE_TEACHER, ROLE_ADMIN,
)
from ..forms import (
    ExamForm, QuestionForm, UserForm, SettingsForm, ImportQuestionsForm, ClassForm,
)
from ..decorators import admin_required
from ..audit import log_action
from .. import proctoring
from .. import framestore
from .. import settings as settings_store

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _pass_mark_for(exam):
    if exam.pass_mark is not None:
        return exam.pass_mark
    return settings_store.get_int("pass_mark")


def _percent(score, total):
    return round((score or 0) / total * 100) if total else 0


def _all_class_choices():
    rows = SchoolClass.query.order_by(SchoolClass.name).all()
    return [(0, "— No class —")] + [(c.id, c.label) for c in rows]


def _teacher_choices():
    rows = (
        User.query.filter_by(role=ROLE_TEACHER, active=True)
        .order_by(User.username).all()
    )
    return [(0, "— Unassigned —")] + [
        (t.id, t.full_name or t.username) for t in rows
    ]


# ======================= Dashboard =======================

@admin_bp.route("/")
@admin_required
def dashboard():
    stats = {
        "exams": Exam.query.count(),
        "published": Exam.query.filter_by(is_published=True).count(),
        "students": User.query.filter_by(role=ROLE_STUDENT).count(),
        "active": Submission.query.filter_by(completed=False, locked=False, disqualified=False).count(),
        "violations": Violation.query.count(),
        "disqualified": Submission.query.filter_by(disqualified=True).count(),
        "submissions": Submission.query.count(),
    }

    # Submissions trend (last 14 days)
    since = datetime.utcnow() - timedelta(days=13)
    per_day = defaultdict(int)
    for s in Submission.query.filter(Submission.started_at >= since).all():
        per_day[s.started_at.strftime("%Y-%m-%d")] += 1
    trend_labels, trend_values = [], []
    for i in range(14):
        d = (since + timedelta(days=i)).strftime("%Y-%m-%d")
        trend_labels.append((since + timedelta(days=i)).strftime("%b %d"))
        trend_values.append(per_day.get(d, 0))

    # Violation breakdown
    vstats = (
        db.session.query(Violation.violation_type, func.count(Violation.id))
        .group_by(Violation.violation_type).all()
    )

    recent_violations = Violation.query.order_by(Violation.timestamp.desc()).limit(8).all()
    recent_names = {
        u.id: (u.full_name or u.username)
        for u in User.query.filter(User.id.in_([v.student_id for v in recent_violations] or [0])).all()
    }
    return render_template(
        "admin/dashboard.html",
        stats=stats,
        trend={"labels": trend_labels, "values": trend_values},
        vstats={"labels": [t for t, _ in vstats], "values": [c for _, c in vstats]},
        recent_violations=recent_violations,
        recent_names=recent_names,
    )


# ======================= Exams =======================

@admin_bp.route("/exams")
@admin_required
def exams():
    all_exams = Exam.query.order_by(Exam.created_at.desc()).all()
    return render_template("shared/exams.html", exams=all_exams)


@admin_bp.route("/exams/new", methods=["GET", "POST"])
@admin_required
def exam_new():
    form = ExamForm()
    form.class_id.choices = _all_class_choices()
    if form.validate_on_submit():
        exam = Exam(created_by=current_user.id)
        form.populate_obj(exam)
        if not exam.class_id:
            exam.class_id = None
        db.session.add(exam)
        db.session.commit()
        log_action("exam.create", target=exam.id, detail=exam.title)
        flash("Exam created. Now add some questions.", "success")
        return redirect(url_for("admin.questions", exam_id=exam.id))
    return render_template("shared/exam_form.html", form=form, exam=None)


@admin_bp.route("/exams/<int:exam_id>/edit", methods=["GET", "POST"])
@admin_required
def exam_edit(exam_id):
    exam = db.session.get(Exam, exam_id) or abort(404)
    form = ExamForm(obj=exam)
    form.class_id.choices = _all_class_choices()
    if form.validate_on_submit():
        form.populate_obj(exam)
        if not exam.class_id:
            exam.class_id = None
        db.session.commit()
        log_action("exam.update", target=exam.id, detail=exam.title)
        flash("Exam updated.", "success")
        return redirect(url_for("admin.exams"))
    return render_template("shared/exam_form.html", form=form, exam=exam)


@admin_bp.route("/exams/<int:exam_id>/publish", methods=["POST"])
@admin_required
def exam_publish(exam_id):
    exam = db.session.get(Exam, exam_id) or abort(404)
    if exam.question_count == 0 and not exam.is_published:
        flash("Add at least one question before publishing.", "warning")
        return redirect(url_for("admin.exams"))
    exam.is_published = not exam.is_published
    db.session.commit()
    log_action("exam.publish" if exam.is_published else "exam.unpublish", target=exam.id)
    flash(f"Exam {'published' if exam.is_published else 'unpublished'}.", "info")
    return redirect(url_for("admin.exams"))


@admin_bp.route("/exams/<int:exam_id>/clone", methods=["POST"])
@admin_required
def exam_clone(exam_id):
    exam = db.session.get(Exam, exam_id) or abort(404)
    copy = Exam(
        title=f"{exam.title} (copy)",
        description=exam.description,
        duration=exam.duration,
        created_by=current_user.id,
        is_published=False,
        shuffle_questions=exam.shuffle_questions,
        require_webcam=exam.require_webcam,
        max_violations=exam.max_violations,
        pass_mark=exam.pass_mark,
    )
    db.session.add(copy)
    db.session.commit()
    for q in exam.questions:
        db.session.add(Question(
            exam_id=copy.id, text=q.text, option_a=q.option_a, option_b=q.option_b,
            option_c=q.option_c, option_d=q.option_d, correct_answer=q.correct_answer, order=q.order,
        ))
    db.session.commit()
    log_action("exam.clone", target=copy.id, detail=f"from {exam.id}")
    flash("Exam duplicated as a draft.", "success")
    return redirect(url_for("admin.questions", exam_id=copy.id))


@admin_bp.route("/exams/<int:exam_id>/delete", methods=["POST"])
@admin_required
def exam_delete(exam_id):
    exam = db.session.get(Exam, exam_id) or abort(404)
    title = exam.title
    db.session.delete(exam)
    db.session.commit()
    log_action("exam.delete", target=exam_id, detail=title)
    flash("Exam deleted.", "info")
    return redirect(url_for("admin.exams"))


# ======================= Questions =======================

@admin_bp.route("/exams/<int:exam_id>/questions", methods=["GET", "POST"])
@admin_required
def questions(exam_id):
    exam = db.session.get(Exam, exam_id) or abort(404)
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
        return redirect(url_for("admin.questions", exam_id=exam.id))
    return render_template("shared/questions.html", exam=exam, form=form, import_form=import_form)


@admin_bp.route("/exams/<int:exam_id>/questions/import", methods=["POST"])
@admin_required
def questions_import(exam_id):
    exam = db.session.get(Exam, exam_id) or abort(404)
    form = ImportQuestionsForm()
    if not form.validate_on_submit():
        flash("Nothing to import.", "warning")
        return redirect(url_for("admin.questions", exam_id=exam.id))

    added, errors = 0, 0
    order = (db.session.query(func.coalesce(func.max(Question.order), 0)).filter_by(exam_id=exam.id).scalar())
    reader = csv.reader(io.StringIO(form.data.data.strip()))
    for row in reader:
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
    flash(f"Imported {added} question(s)." + (f" {errors} row(s) skipped." if errors else ""), "success" if added else "warning")
    return redirect(url_for("admin.questions", exam_id=exam.id))


@admin_bp.route("/questions/<int:q_id>/edit", methods=["GET", "POST"])
@admin_required
def question_edit(q_id):
    q = db.session.get(Question, q_id) or abort(404)
    form = QuestionForm(obj=q)
    if form.validate_on_submit():
        form.populate_obj(q)
        db.session.commit()
        log_action("question.update", target=q.id)
        flash("Question updated.", "success")
        return redirect(url_for("admin.questions", exam_id=q.exam_id))
    return render_template("shared/question_form.html", form=form, question=q)


@admin_bp.route("/questions/<int:q_id>/delete", methods=["POST"])
@admin_required
def question_delete(q_id):
    q = db.session.get(Question, q_id) or abort(404)
    exam_id = q.exam_id
    db.session.delete(q)
    db.session.commit()
    log_action("question.delete", target=q_id)
    flash("Question deleted.", "info")
    return redirect(url_for("admin.questions", exam_id=exam_id))


# ======================= Classes =======================

@admin_bp.route("/classes")
@admin_required
def classes():
    rows = (
        SchoolClass.query.order_by(SchoolClass.active.desc(), SchoolClass.name).all()
    )
    return render_template("shared/classes.html", classes=rows)


@admin_bp.route("/classes/new", methods=["GET", "POST"])
@admin_required
def class_new():
    form = ClassForm()
    form.teacher_id.choices = _teacher_choices()
    if form.validate_on_submit():
        cls = SchoolClass()
        form.populate_obj(cls)
        if not cls.teacher_id:
            cls.teacher_id = None
        db.session.add(cls)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("A class with that subject code, section and year already exists.", "danger")
            return render_template("shared/class_form.html", form=form, cls=None)
        log_action("class.create", target=cls.id, detail=cls.subject_code)
        flash("Class created.", "success")
        return redirect(url_for("admin.classes"))
    return render_template("shared/class_form.html", form=form, cls=None)


@admin_bp.route("/classes/<int:class_id>/edit", methods=["GET", "POST"])
@admin_required
def class_edit(class_id):
    cls = db.session.get(SchoolClass, class_id) or abort(404)
    form = ClassForm(obj=cls)
    form.teacher_id.choices = _teacher_choices()
    if form.validate_on_submit():
        form.populate_obj(cls)
        if not cls.teacher_id:
            cls.teacher_id = None
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash("Another class already uses that code / section / year.", "danger")
            return render_template("shared/class_form.html", form=form, cls=cls)
        log_action("class.update", target=cls.id, detail=cls.subject_code)
        flash("Class updated.", "success")
        return redirect(url_for("admin.classes"))
    return render_template("shared/class_form.html", form=form, cls=cls)


@admin_bp.route("/classes/<int:class_id>/toggle", methods=["POST"])
@admin_required
def class_toggle(class_id):
    cls = db.session.get(SchoolClass, class_id) or abort(404)
    cls.active = not cls.active
    db.session.commit()
    log_action("class.activate" if cls.active else "class.deactivate", target=cls.id)
    flash(f"Class {'activated' if cls.active else 'archived'}.", "info")
    return redirect(url_for("admin.classes"))


@admin_bp.route("/classes/<int:class_id>/delete", methods=["POST"])
@admin_required
def class_delete(class_id):
    cls = db.session.get(SchoolClass, class_id) or abort(404)
    if cls.exam_count:
        flash(
            f"This class still has {cls.exam_count} exam(s). Move or delete them first.",
            "warning",
        )
        return redirect(url_for("admin.classes"))
    code = cls.subject_code
    db.session.delete(cls)
    db.session.commit()
    log_action("class.delete", target=class_id, detail=code)
    flash("Class deleted.", "info")
    return redirect(url_for("admin.classes"))


# ======================= Users =======================

@admin_bp.route("/users")
@admin_required
def users():
    q = (request.args.get("q") or "").strip()
    role = request.args.get("role") or ""
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(User.username.ilike(like), User.full_name.ilike(like), User.email.ilike(like)))
    if role in (ROLE_ADMIN, ROLE_TEACHER, ROLE_STUDENT):
        query = query.filter_by(role=role)
    people = query.order_by(User.created_at.desc()).all()
    counts = {
        "total": User.query.count(),
        "admins": User.query.filter_by(role=ROLE_ADMIN).count(),
        "teachers": User.query.filter_by(role=ROLE_TEACHER).count(),
        "students": User.query.filter_by(role=ROLE_STUDENT).count(),
        "inactive": User.query.filter_by(active=False).count(),
    }
    return render_template("admin/users.html", people=people, counts=counts, q=q, role=role)


@admin_bp.route("/users/new", methods=["GET", "POST"])
@admin_required
def user_new():
    form = UserForm()
    if form.validate_on_submit():
        if User.query.filter_by(username=form.username.data.strip()).first():
            flash("That username is already taken.", "danger")
            return render_template("admin/user_form.html", form=form, user=None)
        if not form.password.data:
            flash("A password is required for new users.", "danger")
            return render_template("admin/user_form.html", form=form, user=None)
        u = User(
            username=form.username.data.strip(),
            full_name=form.full_name.data or "",
            email=form.email.data or "",
            role=form.role.data,
            active=form.active.data,
        )
        u.set_password(form.password.data)
        db.session.add(u)
        db.session.commit()
        log_action("user.create", target=u.id, detail=u.username)
        flash("User created.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", form=form, user=None)


@admin_bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def user_edit(user_id):
    u = db.session.get(User, user_id) or abort(404)
    form = UserForm(obj=u)
    if form.validate_on_submit():
        existing = User.query.filter_by(username=form.username.data.strip()).first()
        if existing and existing.id != u.id:
            flash("That username is already taken.", "danger")
            return render_template("admin/user_form.html", form=form, user=u)
        # Guard: don't let an admin lock themselves out.
        if u.id == current_user.id and (not form.active.data or form.role.data != ROLE_ADMIN):
            flash("You cannot deactivate or demote your own account.", "warning")
            return render_template("admin/user_form.html", form=form, user=u)
        u.username = form.username.data.strip()
        u.full_name = form.full_name.data or ""
        u.email = form.email.data or ""
        u.role = form.role.data
        u.active = form.active.data
        if form.password.data:
            u.set_password(form.password.data)
            log_action("user.reset_password", target=u.id, detail=u.username)
        db.session.commit()
        log_action("user.update", target=u.id, detail=u.username)
        flash("User updated.", "success")
        return redirect(url_for("admin.users"))
    return render_template("admin/user_form.html", form=form, user=u)


@admin_bp.route("/users/<int:user_id>/toggle", methods=["POST"])
@admin_required
def user_toggle(user_id):
    u = db.session.get(User, user_id) or abort(404)
    if u.id == current_user.id:
        flash("You cannot deactivate your own account.", "warning")
        return redirect(url_for("admin.users"))
    u.active = not u.active
    db.session.commit()
    log_action("user.activate" if u.active else "user.deactivate", target=u.id, detail=u.username)
    flash(f"User {'activated' if u.active else 'deactivated'}.", "info")
    return redirect(url_for("admin.users"))


@admin_bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def user_delete(user_id):
    u = db.session.get(User, user_id) or abort(404)
    if u.id == current_user.id:
        flash("You cannot delete your own account.", "warning")
        return redirect(url_for("admin.users"))
    if u.role == ROLE_ADMIN and User.query.filter_by(role=ROLE_ADMIN).count() <= 1:
        flash("Cannot delete the last administrator.", "warning")
        return redirect(url_for("admin.users"))
    name = u.username
    db.session.delete(u)
    db.session.commit()
    log_action("user.delete", target=user_id, detail=name)
    flash("User deleted.", "info")
    return redirect(url_for("admin.users"))


# ======================= Settings =======================

@admin_bp.route("/settings", methods=["GET", "POST"])
@admin_required
def settings():
    form = SettingsForm()
    if form.validate_on_submit():
        settings_store.set_policy({
            "max_violations": form.max_violations.data,
            "risk_window_seconds": form.risk_window_seconds.data,
            "risk_window_threshold": form.risk_window_threshold.data,
            "snapshot_interval": form.snapshot_interval.data,
            "heartbeat_timeout": form.heartbeat_timeout.data,
            "pass_mark": form.pass_mark.data,
        })
        log_action("settings.update")
        flash("Settings saved.", "success")
        return redirect(url_for("admin.settings"))
    if request.method == "GET":
        for k, v in settings_store.get_policy().items():
            if hasattr(form, k):
                getattr(form, k).data = v
    return render_template("admin/settings.html", form=form)


# ======================= Audit log =======================

@admin_bp.route("/audit")
@admin_required
def audit():
    entries = AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(300).all()
    return render_template("admin/audit.html", entries=entries)


# ======================= Live monitor =======================

@admin_bp.route("/monitor")
@admin_required
def monitor():
    violations = Violation.query.order_by(Violation.timestamp.desc()).limit(100).all()
    names = {u.id: (u.full_name or u.username) for u in User.query.all()}
    return render_template("admin/monitor.html", violations=violations, names=names)


# ======================= Results =======================

@admin_bp.route("/results")
@admin_required
def results():
    q = (request.args.get("q") or "").strip()
    exam_id = request.args.get("exam_id", type=int)
    query = (
        db.session.query(Submission, User.username, Exam.title)
        .join(User, User.id == Submission.student_id)
        .join(Exam, Exam.id == Submission.exam_id)
    )
    if q:
        query = query.filter(db.or_(User.username.ilike(f"%{q}%"), Exam.title.ilike(f"%{q}%")))
    if exam_id:
        query = query.filter(Submission.exam_id == exam_id)
    rows = query.order_by(Submission.started_at.desc()).all()
    exams = Exam.query.order_by(Exam.title).all()
    return render_template("shared/results.html", rows=rows, exams=exams, q=q, exam_id=exam_id)


@admin_bp.route("/exams/<int:exam_id>/stats")
@admin_required
def exam_stats(exam_id):
    exam = db.session.get(Exam, exam_id) or abort(404)
    completed = [s for s in exam.submissions if s.completed]
    pass_mark = _pass_mark_for(exam)

    percents = [_percent(s.score, s.total or exam.question_count) for s in completed]
    avg = round(sum(percents) / len(percents), 1) if percents else 0
    passed = sum(1 for p in percents if p >= pass_mark)
    pass_rate = round(passed / len(percents) * 100) if percents else 0

    # Score distribution (bands of 10%)
    bands = [0] * 10
    for p in percents:
        bands[min(9, p // 10)] += 1
    band_labels = [f"{i*10}-{i*10+9}%" for i in range(10)]

    # Item analysis: correct-rate per question
    items = []
    sub_ids = [s.id for s in completed]
    for q in exam.questions:
        if sub_ids:
            answered = Answer.query.filter(Answer.question_id == q.id, Answer.submission_id.in_(sub_ids)).all()
            picks = Counter(a.selected for a in answered if a.selected)
            attempts = sum(picks.values())
            correct = picks.get(q.correct_answer, 0)
        else:
            picks, attempts, correct = Counter(), 0, 0
        items.append({
            "q": q,
            "attempts": attempts,
            "correct": correct,
            "rate": round(correct / attempts * 100) if attempts else 0,
            "picks": picks,
        })

    return render_template(
        "shared/exam_stats.html",
        exam=exam, completed=len(completed), avg=avg, pass_rate=pass_rate,
        pass_mark=pass_mark, dist={"labels": band_labels, "values": bands}, items=items,
    )


@admin_bp.route("/submissions/<int:sub_id>")
@admin_required
def submission_detail(sub_id):
    sub = db.session.get(Submission, sub_id) or abort(404)
    student = db.session.get(User, sub.student_id)
    saved = {a.question_id: a.selected for a in sub.answers}
    violations = Violation.query.filter_by(submission_id=sub.id).order_by(Violation.timestamp.asc()).all()
    return render_template(
        "shared/submission_detail.html",
        sub=sub, student=student, saved=saved, violations=violations,
        status=proctoring.live_status(sub), pass_mark=_pass_mark_for(sub.exam),
        percent=_percent(sub.score, sub.total or sub.exam.question_count),
    )


def _set_submission_state(sub_id, **changes):
    sub = db.session.get(Submission, sub_id) or abort(404)
    for k, v in changes.items():
        setattr(sub, k, v)
    db.session.commit()
    return sub


@admin_bp.route("/submissions/<int:sub_id>/lock", methods=["POST"])
@admin_required
def submission_lock(sub_id):
    _set_submission_state(sub_id, locked=True, termination_reason="Locked by proctor")
    log_action("submission.lock", target=sub_id)
    flash("Attempt locked.", "info")
    return _redirect_back()


@admin_bp.route("/submissions/<int:sub_id>/unlock", methods=["POST"])
@admin_required
def submission_unlock(sub_id):
    sub = db.session.get(Submission, sub_id) or abort(404)
    sub.locked = False
    sub.disqualified = False
    sub.termination_reason = None
    db.session.commit()
    log_action("submission.unlock", target=sub_id)
    flash("Attempt unlocked.", "info")
    return _redirect_back()


@admin_bp.route("/submissions/<int:sub_id>/disqualify", methods=["POST"])
@admin_required
def submission_disqualify(sub_id):
    sub = _set_submission_state(
        sub_id, disqualified=True, locked=True, score=0, termination_reason="Disqualified by proctor"
    )
    log_action("submission.disqualify", target=sub.id)
    flash(f"Submission #{sub.id} disqualified.", "warning")
    return _redirect_back()


@admin_bp.route("/submissions/<int:sub_id>/score", methods=["POST"])
@admin_required
def submission_score(sub_id):
    sub = db.session.get(Submission, sub_id) or abort(404)
    total = sub.total or sub.exam.question_count
    try:
        new_score = int(request.form.get("score", ""))
    except ValueError:
        flash("Invalid score.", "danger")
        return _redirect_back()
    new_score = max(0, min(total, new_score))
    sub.score = new_score
    if not sub.completed:
        sub.completed = True
        sub.completed_at = datetime.utcnow()
    db.session.commit()
    log_action("submission.score_override", target=sub.id, detail=f"{new_score}/{total}")
    flash("Score updated.", "success")
    return _redirect_back()


def _redirect_back():
    target = request.form.get("next") or request.referrer or url_for("admin.monitor")
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(url_for("admin.monitor"))


# ======================= Proctor snapshots =======================

@admin_bp.route("/snapshots/<int:snap_id>")
@admin_required
def snapshot_image(snap_id):
    snap = db.session.get(ProctorSnapshot, snap_id) or abort(404)
    data = framestore.load_snapshot(snap)
    if data is None:
        abort(404)
    return Response(data, mimetype="image/jpeg")


# ======================= Live screen monitoring =======================

@admin_bp.route("/live-wall")
@admin_required
def live_wall():
    """
    Watch every in-progress candidate's screen at once.

    This is a passive view: it only reads the frames the exam page is already
    uploading, so opening it sends nothing to the student and does not
    interrupt their attempt in any way.
    """
    active_exam_ids = {
        s.exam_id
        for s in Submission.query.filter_by(completed=False, disqualified=False).all()
    }
    exams_live = (
        Exam.query.filter(Exam.id.in_(active_exam_ids or {0}))
        .order_by(Exam.title).all()
    )
    return render_template(
        "admin/live_wall.html",
        exams=exams_live,
        exam_id=request.args.get("exam_id", type=int),
    )


@admin_bp.route("/live/<int:sub_id>")
@admin_required
def live_view(sub_id):
    sub = db.session.get(Submission, sub_id) or abort(404)
    student = db.session.get(User, sub.student_id)
    return render_template("admin/live.html", sub=sub, student=student, status=proctoring.live_status(sub))


@admin_bp.route("/live-frame/<int:sub_id>/<kind>")
@admin_required
def live_frame_image(sub_id, kind):
    if kind not in ("screen", "cam"):
        abort(404)
    data = framestore.load_live_frame(sub_id, kind)
    if data is None:
        abort(404)
    resp = Response(data, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# ======================= Reports =======================

def _submission_rows(exam_id=None):
    query = (
        db.session.query(Submission, User.username, Exam.title)
        .join(User, User.id == Submission.student_id)
        .join(Exam, Exam.id == Submission.exam_id)
    )
    if exam_id:
        query = query.filter(Submission.exam_id == exam_id)
    return query.order_by(Submission.started_at.desc()).all()


@admin_bp.route("/report.csv")
@admin_required
def report_csv():
    exam_id = request.args.get("exam_id", type=int)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Submission", "Student", "Exam", "Score", "Total", "Percent", "Status", "Violations", "Started", "Completed"])
    for sub, username, exam_title in _submission_rows(exam_id):
        writer.writerow([
            sub.id, username, exam_title,
            sub.score if sub.score is not None else "",
            sub.total if sub.total is not None else "",
            _percent(sub.score, sub.total) if sub.total else "",
            sub.status_label(), sub.violation_count,
            sub.started_at.strftime("%Y-%m-%d %H:%M") if sub.started_at else "",
            sub.completed_at.strftime("%Y-%m-%d %H:%M") if sub.completed_at else "",
        ])
    fname = f"safeexam_exam{exam_id}.csv" if exam_id else "safeexam_report.csv"
    return Response(output.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={fname}"})


@admin_bp.route("/report.pdf")
@admin_required
def report_pdf():
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import cm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title="SafeExam Report")
    styles = getSampleStyleSheet()
    elements = [
        Paragraph("SafeExam — Examination Integrity Report", styles["Title"]),
        Paragraph(datetime.utcnow().strftime("Generated %Y-%m-%d %H:%M UTC"), styles["Normal"]),
        Spacer(1, 16),
    ]
    summary = [
        ["Exams", Exam.query.count()],
        ["Submissions", Submission.query.count()],
        ["Disqualified", Submission.query.filter_by(disqualified=True).count()],
        ["Total violations", Violation.query.count()],
    ]
    t = Table(summary, colWidths=[6 * cm, 4 * cm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#1e293b")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("PADDING", (0, 0), (-1, -1), 6),
    ]))
    elements += [Paragraph("Summary", styles["Heading2"]), t, Spacer(1, 18)]

    data = [["#", "Student", "Exam", "Score", "Status", "Viol."]]
    for sub, username, exam_title in _submission_rows():
        score = f"{sub.score}/{sub.total}" if sub.score is not None else "-"
        data.append([str(sub.id), username, exam_title[:28], score, sub.status_label(), str(sub.violation_count)])
    table = Table(data, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cbd5e1")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
        ("PADDING", (0, 0), (-1, -1), 5),
    ]))
    elements += [Paragraph("Submissions", styles["Heading2"]), table]
    doc.build(elements)
    buffer.seek(0)
    return send_file(buffer, as_attachment=True, download_name="safeexam_report.pdf", mimetype="application/pdf")
