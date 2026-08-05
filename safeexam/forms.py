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
    shuffle_questions = BooleanField("Shuffle question order")
    require_webcam = BooleanField("Require webcam", default=True)
    is_published = BooleanField("Published (visible to students)")
    available_from = DateTimeLocalField(
        "Available from", format="%Y-%m-%dT%H:%M", validators=[Optional()]
    )
    available_until = DateTimeLocalField(
        "Available until", format="%Y-%m-%dT%H:%M", validators=[Optional()]
    )

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


class QuestionForm(FlaskForm):
    text = TextAreaField("Question", validators=[DataRequired(), Length(max=2000)])
    option_a = StringField("Option A", validators=[DataRequired(), Length(max=300)])
    option_b = StringField("Option B", validators=[DataRequired(), Length(max=300)])
    option_c = StringField("Option C", validators=[DataRequired(), Length(max=300)])
    option_d = StringField("Option D", validators=[DataRequired(), Length(max=300)])
    correct_answer = SelectField(
        "Correct answer",
        choices=[("A", "Option A"), ("B", "Option B"), ("C", "Option C"), ("D", "Option D")],
        validators=[DataRequired()],
    )
