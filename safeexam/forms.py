"""Flask-WTF forms (provide CSRF protection + server-side validation)."""
from flask_wtf import FlaskForm
from wtforms import (
    StringField,
    PasswordField,
    SelectField,
    IntegerField,
    TextAreaField,
    BooleanField,
    DateTimeLocalField,
    FloatField,
    SelectMultipleField,
)
from wtforms.validators import (
    DataRequired, InputRequired, Length, NumberRange, EqualTo, Optional, ValidationError,
)


def _optional_int(value):
    """SelectField coercion that maps a blank choice to None instead of raising."""
    if value in (None, "", "None"):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=80)])
    password = PasswordField("Password", validators=[DataRequired()])
    remember = BooleanField("Remember me")


class RegisterForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=80)])
    full_name = StringField("Full name", validators=[Optional(), Length(max=120)])
    password = PasswordField("Password", validators=[DataRequired(), Length(min=6)])
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
    # NOTE: public sign-up creates candidates only. Teacher and admin accounts
    # are created by an administrator from Users -> New user.


class ExamForm(FlaskForm):
    title = StringField("Exam title", validators=[DataRequired(), Length(max=200)])
    class_id = SelectField(
        "Class / subject", coerce=_optional_int, validators=[Optional()], choices=[]
    )
    description = TextAreaField("Description", validators=[Optional(), Length(max=2000)])
    duration = IntegerField(
        "Duration (minutes)", validators=[DataRequired(), NumberRange(min=1, max=600)]
    )
    max_violations = IntegerField(
        "Violation limit (blank = system default)",
        validators=[Optional(), NumberRange(min=1, max=50)],
    )
    pass_mark = IntegerField(
        "Pass mark % (blank = system default)",
        validators=[Optional(), NumberRange(min=0, max=100)],
    )
    strictness = SelectField(
        "Proctoring strictness",
        choices=[("relaxed", "Relaxed"), ("standard", "Standard"), ("strict", "Strict")],
        default="standard",
    )
    shuffle_questions = BooleanField("Shuffle question order")
    require_webcam = BooleanField("Require webcam", default=True)
    negative_marking = FloatField(
        "Negative marking (fraction of a question's marks lost per wrong answer)",
        validators=[Optional(), NumberRange(min=0, max=1)],
        default=0,
    )
    pool_size = IntegerField(
        "Questions per student (0 = all)",
        validators=[Optional(), NumberRange(min=0, max=1000)],
        default=0,
    )
    shuffle_options = BooleanField("Shuffle answer options per student")
    results_released = BooleanField("Release results (students see answers and marks)")
    is_published = BooleanField("Published (visible to students)")
    available_from = DateTimeLocalField(
        "Available from", format="%Y-%m-%dT%H:%M", validators=[Optional()]
    )
    available_until = DateTimeLocalField(
        "Available until", format="%Y-%m-%dT%H:%M", validators=[Optional()]
    )

    def populate_obj(self, obj):
        super().populate_obj(obj)
        # Blank numeric inputs must not null out NOT NULL columns.
        obj.negative_marking = obj.negative_marking or 0
        obj.pool_size = obj.pool_size or 0

    def validate_available_until(self, field):
        if field.data and self.available_from.data and field.data <= self.available_from.data:
            raise ValidationError("'Available until' must be after 'Available from'.")


class UserForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(min=3, max=80)])
    full_name = StringField("Full name", validators=[Optional(), Length(max=120)])
    email = StringField("Email", validators=[Optional(), Length(max=160)])
    role = SelectField(
        "Role",
        choices=[
            ("student", "Student"),
            ("teacher", "Teacher"),
            ("admin", "Admin / Proctor"),
        ],
        validators=[DataRequired()],
    )
    active = BooleanField("Active", default=True)
    # On create this is required; on edit, blank means "leave unchanged".
    password = PasswordField("Password", validators=[Optional(), Length(min=6)])


class ClassForm(FlaskForm):
    """Create / edit a teaching class (course section)."""

    name = StringField(
        "Class name", validators=[DataRequired(), Length(max=120)],
        description="e.g. BCom III Year",
    )
    subject_name = StringField(
        "Subject name", validators=[DataRequired(), Length(max=120)],
        description="e.g. Financial Accounting",
    )
    subject_code = StringField(
        "Subject code", validators=[DataRequired(), Length(max=40)],
        description="e.g. FA-301",
    )
    section = StringField("Section", validators=[Optional(), Length(max=40)])
    academic_year = StringField(
        "Academic year", validators=[Optional(), Length(max=20)],
        description="e.g. 2026-27",
    )
    description = TextAreaField("Description", validators=[Optional(), Length(max=2000)])
    active = BooleanField("Active", default=True)
    # Only rendered for admins; teachers always own the classes they create.
    teacher_id = SelectField(
        "Assigned teacher", coerce=_optional_int, validators=[Optional()], choices=[]
    )


class SettingsForm(FlaskForm):
    # InputRequired (not DataRequired) so a legitimate 0 is accepted.
    max_violations = IntegerField("Violation limit (auto-terminate)", validators=[InputRequired(), NumberRange(min=1, max=50)])
    risk_window_seconds = IntegerField("Risk window (seconds)", validators=[InputRequired(), NumberRange(min=5, max=600)])
    risk_window_threshold = IntegerField("Risk window threshold", validators=[InputRequired(), NumberRange(min=1, max=20)])
    snapshot_interval = IntegerField("Webcam snapshot interval (seconds, 0 = off)", validators=[InputRequired(), NumberRange(min=0, max=600)])
    heartbeat_timeout = IntegerField("Heartbeat timeout (seconds)", validators=[InputRequired(), NumberRange(min=5, max=300)])
    pass_mark = IntegerField("Default pass mark (%)", validators=[InputRequired(), NumberRange(min=0, max=100)])


class ImportQuestionsForm(FlaskForm):
    data = TextAreaField(
        "Questions (CSV)",
        validators=[DataRequired()],
        description="One question per line: question, A, B, C, D, correct",
    )


QUESTION_TYPES = [
    ("single", "Single choice"),
    ("multiple", "Multiple correct"),
    ("truefalse", "True / False"),
    ("numeric", "Numeric answer"),
]
_LETTERS = [("A", "A"), ("B", "B"), ("C", "C"), ("D", "D")]


class QuestionForm(FlaskForm):
    qtype = SelectField("Type", choices=QUESTION_TYPES, default="single")
    text = TextAreaField("Question", validators=[DataRequired(), Length(max=2000)])
    image_url = StringField("Image URL (optional)", validators=[Optional(), Length(max=500)])
    marks = FloatField("Marks", validators=[Optional(), NumberRange(min=0.25, max=100)], default=1)
    option_a = StringField("Option A", validators=[Optional(), Length(max=300)])
    option_b = StringField("Option B", validators=[Optional(), Length(max=300)])
    option_c = StringField("Option C", validators=[Optional(), Length(max=300)])
    option_d = StringField("Option D", validators=[Optional(), Length(max=300)])
    correct_answer = SelectField(
        "Correct answer",
        choices=[("A", "Option A"), ("B", "Option B"), ("C", "Option C"), ("D", "Option D")],
        default="A",
    )
    correct_multi = SelectMultipleField("Correct answers (tick all)", choices=_LETTERS)
    numeric_answer = FloatField("Correct number", validators=[Optional()])
    tolerance = FloatField("Tolerance (±)", validators=[Optional(), NumberRange(min=0)])

    def validate_image_url(self, field):
        url = (field.data or "").strip()
        if url and not url.lower().startswith(("http://", "https://", "/")):
            raise ValidationError("Use an http(s) link or a /static path.")

    def validate(self, extra_validators=None):
        ok = super().validate(extra_validators)
        kind = self.qtype.data
        if kind == "truefalse":
            if self.correct_answer.data not in ("A", "B"):
                self.correct_answer.errors = list(self.correct_answer.errors) + ["Pick True or False."]
                ok = False
        elif kind == "numeric":
            if self.numeric_answer.data is None:
                self.numeric_answer.errors = list(self.numeric_answer.errors) + ["Enter the correct number."]
                ok = False
        else:
            for name in ("option_a", "option_b", "option_c", "option_d"):
                field = getattr(self, name)
                if not (field.data or "").strip():
                    field.errors = list(field.errors) + ["Required."]
                    ok = False
            if kind == "multiple" and not self.correct_multi.data:
                self.correct_multi.errors = list(self.correct_multi.errors) + ["Tick at least one."]
                ok = False
        return ok

    def apply_to(self, q):
        """Copy the form onto a Question, normalising by type."""
        kind = self.qtype.data
        q.qtype = kind
        q.text = self.text.data
        q.image_url = (self.image_url.data or "").strip() or None
        q.marks = self.marks.data if self.marks.data is not None else 1.0
        q.numeric_answer = q.tolerance = None
        if kind == "truefalse":
            q.option_a, q.option_b, q.option_c, q.option_d = "True", "False", "", ""
            q.correct_answer = self.correct_answer.data
        elif kind == "numeric":
            q.option_a = q.option_b = q.option_c = q.option_d = ""
            q.correct_answer = ""
            q.numeric_answer = self.numeric_answer.data
            q.tolerance = self.tolerance.data or None
        else:
            q.option_a, q.option_b = self.option_a.data, self.option_b.data
            q.option_c, q.option_d = self.option_c.data, self.option_d.data
            q.correct_answer = (
                "".join(sorted(self.correct_multi.data)) if kind == "multiple"
                else self.correct_answer.data
            )

    def load_from(self, q):
        """Prefill type-specific fields when editing (call on GET only)."""
        self.qtype.data = q.kind
        if q.kind == "multiple":
            self.correct_multi.data = list(q.correct_answer)
