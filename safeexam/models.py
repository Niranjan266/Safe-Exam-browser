"""Database models for the Safe Exam Browser platform."""
from datetime import datetime, timedelta
import secrets

from werkzeug.security import generate_password_hash, check_password_hash
from flask_login import UserMixin

from .extensions import db, login_manager

# Roles
ROLE_ADMIN = "admin"
ROLE_TEACHER = "teacher"
ROLE_STUDENT = "student"

ROLE_CHOICES = [
    (ROLE_STUDENT, "Student"),
    (ROLE_TEACHER, "Teacher"),
    (ROLE_ADMIN, "Admin / Proctor"),
]
ALL_ROLES = {ROLE_ADMIN, ROLE_TEACHER, ROLE_STUDENT}


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), nullable=False, default=ROLE_STUDENT)
    full_name = db.Column(db.String(120))
    email = db.Column(db.String(160))
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    last_login_at = db.Column(db.DateTime, nullable=True)

    exams_created = db.relationship("Exam", backref="creator", lazy=True)
    submissions = db.relationship(
        "Submission", backref="student", lazy=True, cascade="all, delete-orphan"
    )
    classes = db.relationship(
        "SchoolClass",
        backref="teacher",
        lazy=True,
        foreign_keys="SchoolClass.teacher_id",
    )

    # --- password helpers ---
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    # --- role helpers ---
    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    @property
    def is_teacher(self):
        return self.role == ROLE_TEACHER

    @property
    def is_student(self):
        return self.role == ROLE_STUDENT

    @property
    def is_staff(self):
        """Admins and teachers both reach the authoring side of the app."""
        return self.role in (ROLE_ADMIN, ROLE_TEACHER)

    @property
    def role_label(self):
        return dict(ROLE_CHOICES).get(self.role, self.role.capitalize())

    @property
    def is_active(self):
        # Flask-Login consults this to allow/deny login.
        return bool(self.active)

    def __repr__(self):
        return f"<User {self.username} ({self.role})>"


class SchoolClass(db.Model):
    """
    A teaching class / course section that exams can be attached to.

    Created and maintained by teachers (for their own classes) and by admins
    (for any class).
    """

    __tablename__ = "classes"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)          # e.g. "BCom III Year"
    subject_name = db.Column(db.String(120), nullable=False)   # e.g. "Financial Accounting"
    subject_code = db.Column(db.String(40), nullable=False)    # e.g. "FA-301"
    section = db.Column(db.String(40), default="")            # e.g. "A"
    academic_year = db.Column(db.String(20), default="")      # e.g. "2026-27"
    description = db.Column(db.Text, default="")
    active = db.Column(db.Boolean, default=True, nullable=False)

    teacher_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    exams = db.relationship("Exam", backref="school_class", lazy=True)

    __table_args__ = (
        db.UniqueConstraint(
            "subject_code", "section", "academic_year", name="uq_class_code_section_year"
        ),
    )

    @property
    def label(self):
        bits = [self.name]
        if self.section:
            bits.append(f"Sec {self.section}")
        return " — ".join([" ".join(bits), f"{self.subject_name} ({self.subject_code})"])

    @property
    def exam_count(self):
        return len(self.exams)

    def __repr__(self):
        return f"<SchoolClass {self.subject_code} {self.name}>"


class Exam(db.Model):
    __tablename__ = "exams"

    id = db.Column(db.Integer, primary_key=True)
    class_id = db.Column(db.Integer, db.ForeignKey("classes.id"), nullable=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, default="")
    duration = db.Column(db.Integer, nullable=False)  # minutes
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    is_published = db.Column(db.Boolean, default=False, nullable=False)
    shuffle_questions = db.Column(db.Boolean, default=False, nullable=False)
    # Optional availability window
    available_from = db.Column(db.DateTime, nullable=True)
    available_until = db.Column(db.DateTime, nullable=True)
    # Per-exam override of the global violation limit (0/None = use global default)
    max_violations = db.Column(db.Integer, nullable=True)
    # Pass mark as a percentage (None = use global default pass mark)
    pass_mark = db.Column(db.Integer, nullable=True)

    questions = db.relationship(
        "Question",
        backref="exam",
        lazy=True,
        cascade="all, delete-orphan",
        order_by="Question.order",
    )
    submissions = db.relationship(
        "Submission", backref="exam", lazy=True, cascade="all, delete-orphan"
    )

    @property
    def question_count(self):
        return len(self.questions)

    def is_available_now(self, now=None):
        """Published and within the optional availability window."""
        if not self.is_published:
            return False
        now = now or datetime.utcnow()
        if self.available_from and now < self.available_from:
            return False
        if self.available_until and now > self.available_until:
            return False
        return True

    def availability_label(self, now=None):
        now = now or datetime.utcnow()
        if not self.is_published:
            return "Draft"
        if self.available_from and now < self.available_from:
            return "Scheduled"
        if self.available_until and now > self.available_until:
            return "Closed"
        return "Open"

    def __repr__(self):
        return f"<Exam {self.title}>"


class Question(db.Model):
    __tablename__ = "questions"

    id = db.Column(db.Integer, primary_key=True)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)
    text = db.Column(db.Text, nullable=False)
    option_a = db.Column(db.String(300), nullable=False)
    option_b = db.Column(db.String(300), nullable=False)
    option_c = db.Column(db.String(300), nullable=False)
    option_d = db.Column(db.String(300), nullable=False)
    correct_answer = db.Column(db.String(1), nullable=False)  # 'A'..'D'
    order = db.Column(db.Integer, default=0)

    answers = db.relationship(
        "Answer", backref="question", lazy=True, cascade="all, delete-orphan"
    )

    def options(self):
        return [
            ("A", self.option_a),
            ("B", self.option_b),
            ("C", self.option_c),
            ("D", self.option_d),
        ]

    def __repr__(self):
        return f"<Question {self.id} exam={self.exam_id}>"


class Submission(db.Model):
    """A single student attempt at an exam."""

    __tablename__ = "submissions"

    id = db.Column(db.Integer, primary_key=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)

    score = db.Column(db.Integer, nullable=True)
    total = db.Column(db.Integer, nullable=True)

    started_at = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)
    completed = db.Column(db.Boolean, default=False, nullable=False)

    locked = db.Column(db.Boolean, default=False, nullable=False)
    disqualified = db.Column(db.Boolean, default=False, nullable=False)
    termination_reason = db.Column(db.String(200), nullable=True)

    last_heartbeat = db.Column(db.DateTime, nullable=True)
    attempt_token = db.Column(db.String(64), default=lambda: secrets.token_hex(16))

    violations = db.relationship(
        "Violation", backref="submission", lazy=True, cascade="all, delete-orphan"
    )
    answers = db.relationship(
        "Answer", backref="submission", lazy=True, cascade="all, delete-orphan"
    )
    snapshots = db.relationship(
        "ProctorSnapshot", backref="submission", lazy=True, cascade="all, delete-orphan"
    )
    live_frames = db.relationship(
        "LiveFrame", backref="submission", lazy=True, cascade="all, delete-orphan"
    )

    # --- timing ---
    def deadline(self):
        return self.started_at + timedelta(minutes=self.exam.duration)

    def seconds_remaining(self, now=None):
        now = now or datetime.utcnow()
        return max(0, int((self.deadline() - now).total_seconds()))

    def is_expired(self, now=None):
        return self.seconds_remaining(now) <= 0

    @property
    def is_active(self):
        return not (self.completed or self.locked or self.disqualified)

    @property
    def violation_count(self):
        return len(self.violations)

    def status_label(self):
        if self.disqualified:
            return "Disqualified"
        if self.completed:
            return "Completed"
        if self.locked:
            return "Locked"
        if self.is_expired():
            return "Expired"
        return "Active"

    def __repr__(self):
        return f"<Submission s={self.student_id} e={self.exam_id} {self.status_label()}>"


class Answer(db.Model):
    """A student's selected option for one question in an attempt."""

    __tablename__ = "answers"

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submissions.id"), nullable=False)
    question_id = db.Column(db.Integer, db.ForeignKey("questions.id"), nullable=False)
    selected = db.Column(db.String(1), nullable=True)  # 'A'..'D' or None

    __table_args__ = (
        db.UniqueConstraint("submission_id", "question_id", name="uq_answer_per_question"),
    )


class Violation(db.Model):
    __tablename__ = "violations"

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submissions.id"), nullable=True)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)
    violation_type = db.Column(db.String(120), nullable=False)
    severity = db.Column(db.Integer, default=1)  # weight toward termination
    details = db.Column(db.String(300), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<Violation {self.violation_type} s={self.student_id}>"


class ProctorSnapshot(db.Model):
    """A webcam still captured during an attempt for later review."""

    __tablename__ = "proctor_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submissions.id"), nullable=False)
    student_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    exam_id = db.Column(db.Integer, db.ForeignKey("exams.id"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    captured_at = db.Column(db.DateTime, default=datetime.utcnow)
    flagged = db.Column(db.Boolean, default=False)
    # When the host has no writable disk (e.g. Vercel), the JPEG is stored here
    # instead of on the filesystem. Exactly one of `filename` / `image` is used.
    image = db.Column(db.LargeBinary, nullable=True)


class LiveFrame(db.Model):
    """
    The most recent live screen / webcam frame for an in-progress attempt.

    One row per (submission, kind), overwritten continuously while the
    candidate is writing. Holding frames in the database rather than on disk
    lets the proctor's live wall work on serverless hosts, where the process
    that receives a frame is not the process that later serves it.
    """

    __tablename__ = "live_frames"

    id = db.Column(db.Integer, primary_key=True)
    submission_id = db.Column(db.Integer, db.ForeignKey("submissions.id"), nullable=False)
    kind = db.Column(db.String(10), nullable=False)  # 'screen' or 'cam'
    image = db.Column(db.LargeBinary, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.UniqueConstraint("submission_id", "kind", name="uq_live_frame_kind"),
    )

    def __repr__(self):
        return f"<LiveFrame sub={self.submission_id} {self.kind}>"


class AppSetting(db.Model):
    """Simple key/value store for admin-editable global settings."""

    __tablename__ = "app_settings"

    key = db.Column(db.String(60), primary_key=True)
    value = db.Column(db.String(255), nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class AuditLog(db.Model):
    """Audit trail of proctor/admin actions."""

    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    actor_name = db.Column(db.String(120))
    action = db.Column(db.String(80), nullable=False, index=True)
    target = db.Column(db.String(160))
    detail = db.Column(db.String(300))
    ip = db.Column(db.String(45))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def __repr__(self):
        return f"<AuditLog {self.action} by {self.actor_name}>"
