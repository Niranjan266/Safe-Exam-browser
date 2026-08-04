"""Authentication: register, login, logout."""
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user

from ..extensions import db
from ..models import User, ROLE_ADMIN, ROLE_STUDENT
from ..forms import LoginForm, RegisterForm

auth_bp = Blueprint("auth", __name__)


def _dashboard_for(user):
    if user.is_admin:
        return url_for("admin.dashboard")
    if user.is_teacher:
        return url_for("teacher.dashboard")
    return url_for("student.dashboard")


@auth_bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(_dashboard_for(current_user))
    return redirect(url_for("auth.login"))


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(_dashboard_for(current_user))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.strip()).first()
        if user and not user.active:
            flash("This account has been deactivated. Contact an administrator.", "danger")
            return render_template("auth/login.html", form=form)
        if user and user.check_password(form.password.data):
            from datetime import datetime as _dt
            from ..audit import log_action
            user.last_login_at = _dt.utcnow()
            db.session.commit()
            login_user(user, remember=form.remember.data)
            log_action("login", target=user.username)
            flash(f"Welcome back, {user.full_name or user.username}.", "success")
            # Honour ?next= but only for safe, local paths.
            next_url = request.args.get("next")
            if next_url and next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(_dashboard_for(user))
        flash("Invalid username or password.", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(_dashboard_for(current_user))

    form = RegisterForm()
    if form.validate_on_submit():
        username = form.username.data.strip()
        if User.query.filter_by(username=username).first():
            flash("That username is already taken.", "danger")
            return render_template("auth/register.html", form=form)

        # Public sign-up always creates a candidate. Teacher/admin accounts are
        # created by an administrator so privileges can never be self-granted.
        user = User(
            username=username,
            full_name=form.full_name.data or "",
            role=ROLE_STUDENT,
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()
        flash("Account created. You can now sign in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/register.html", form=form)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))
