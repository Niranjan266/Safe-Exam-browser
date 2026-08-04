"""
Safe Exam Browser - Flask application factory.

A secure online-examination platform with role-based access, a locked-down
exam environment, server-enforced timing, real-time proctoring/violation
monitoring and exportable reports.
"""
import os
from datetime import datetime

from flask import Flask, render_template, redirect, url_for
from flask_login import current_user
from dotenv import load_dotenv

from .extensions import db, login_manager, csrf
from config import get_config

APP_NAME = "SafeExam"
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))

# Load .env (if present) before config is read.
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def create_app(config_name=None):
    app = Flask(
        __name__,
        template_folder=os.path.join(PROJECT_ROOT, "templates"),
        static_folder=os.path.join(PROJECT_ROOT, "static"),
        instance_path=os.path.join(PROJECT_ROOT, "instance"),
    )
    app.config.from_object(get_config(config_name))

    _ensure_dirs(app)
    _init_extensions(app)
    _register_blueprints(app)
    _register_errorhandlers(app)
    _register_context(app)
    _register_cli(app)

    with app.app_context():
        db.create_all()
        _auto_migrate()

    return app


def _auto_migrate():
    """
    Lightweight self-healing migration for SQLite: add any model columns that
    are missing from existing tables. Avoids 'no such column' errors when the
    schema grows between versions, without dropping data. (For real migrations
    in production, use Alembic / Flask-Migrate.)
    """
    from sqlalchemy import inspect as sa_inspect, text

    if db.engine.dialect.name != "sqlite":
        return
    insp = sa_inspect(db.engine)
    for table in db.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in existing:
                continue
            coltype = col.type.compile(dialect=db.engine.dialect)
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'
            if not col.nullable:
                default = None
                if col.default is not None and not getattr(col.default, "is_callable", False):
                    default = getattr(col.default, "arg", None)
                if isinstance(default, bool):
                    default = 1 if default else 0
                if default is None:
                    default = 0 if any(t in coltype.upper() for t in ("INT", "BOOL")) else "''"
                ddl += f" NOT NULL DEFAULT {default}"
            try:
                db.session.execute(text(ddl))
            except Exception:
                db.session.rollback()
    db.session.commit()


def _ensure_dirs(app):
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["SNAPSHOT_DIR"], exist_ok=True)
    os.makedirs(app.config["LIVE_DIR"], exist_ok=True)
    os.makedirs(os.path.join(PROJECT_ROOT, "logs"), exist_ok=True)


def _init_extensions(app):
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    # Import models so SQLAlchemy + Flask-Login user loader are registered.
    from . import models  # noqa: F401


def _register_blueprints(app):
    from .blueprints.auth import auth_bp
    from .blueprints.student import student_bp
    from .blueprints.admin import admin_bp
    from .blueprints.teacher import teacher_bp
    from .blueprints.api import api_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(api_bp)

    # API endpoints are JSON + token-style; exempt from form CSRF
    # (they are still protected by login + the per-attempt token).
    csrf.exempt(api_bp)


def _register_errorhandlers(app):
    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(500)
    def server_error(e):
        return render_template("errors/500.html"), 500


def _register_context(app):
    @app.context_processor
    def inject_globals():
        from . import settings as settings_store

        try:
            policy = settings_store.get_policy()
        except Exception:
            policy = {
                "max_violations": app.config["MAX_VIOLATIONS"],
                "snapshot_interval": app.config["SNAPSHOT_INTERVAL_SECONDS"],
            }
        # `ns` lets admins and teachers share the same authoring templates:
        # a template writes url_for(ns ~ '.exams') and it resolves to the
        # blueprint the current user actually belongs to.
        ns = "admin"
        if current_user.is_authenticated and getattr(current_user, "is_teacher", False):
            ns = "teacher"

        return {
            "APP_NAME": APP_NAME,
            "APP_TAGLINE": "Secure Examination Platform",
            "current_year": datetime.utcnow().year,
            "policy": policy,
            "ns": ns,
        }


def _register_cli(app):
    @app.cli.command("seed")
    def seed_command():
        """Populate the database with demo accounts and a sample exam."""
        from seed import run_seed

        run_seed(app)
