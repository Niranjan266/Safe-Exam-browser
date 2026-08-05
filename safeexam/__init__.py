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

    _configure_database(app)
    _ensure_dirs(app)
    _register_host_guard(app)
    _init_extensions(app)
    _register_health(app)
    _register_blueprints(app)
    _register_errorhandlers(app)
    _register_context(app)
    _register_cli(app)

    # Creating tables costs a round trip per table. Against a remote database
    # (Turso) that is wasted work on every serverless cold start, so it can be
    # turned off with SEB_INIT_DB=0 once the schema is in place.
    if _bool_env("SEB_INIT_DB", default=True):
        with app.app_context():
            db.create_all()
            _auto_migrate()

    return app


def _bool_env(name, default=False):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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


def _configure_database(app):
    """Point SQLAlchemy at Turso (libSQL) when credentials are present."""
    from . import turso

    if not turso.is_enabled():
        return
    turso.register()
    app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite+libsql://"
    app.config["SQLALCHEMY_ENGINE_OPTIONS"] = turso.engine_options()


def _register_health(app):
    """
    GET /healthz — says which database the app is actually talking to.

    Worth having because the failure it catches is otherwise silent: with no
    Turso credentials the app falls back to a local SQLite file, which on a
    read-only serverless host fails with a bare "unable to open database file"
    on the first query — long after startup, and only on pages that touch the
    database. Deliberately reports no host, no token and no exception text.
    """
    from flask import jsonify
    from sqlalchemy import text

    @app.route("/healthz")
    def healthz():
        from . import turso

        if turso.is_enabled():
            backend = "turso"
        elif os.environ.get("SEB_DATABASE_URI"):
            backend = "external"
        else:
            backend = "sqlite-local"

        info = {
            "status": "ok",
            "database": backend,
            "frame_storage": app.config.get("FRAME_STORAGE"),
        }
        try:
            db.session.execute(text("select 1"))
            info["db_connected"] = True
        except Exception:
            db.session.rollback()
            info["status"] = "error"
            info["db_connected"] = False
            if backend == "sqlite-local":
                info["hint"] = (
                    "No database is configured. Set SEB_TURSO_URL and SEB_TURSO_TOKEN "
                    "(or SEB_DATABASE_URI). A local SQLite file cannot be used on a "
                    "read-only or serverless host."
                )
            else:
                info["hint"] = "The configured database could not be reached."
        return jsonify(info), (200 if info["status"] == "ok" else 503)

    # Make the misconfiguration obvious in the build/runtime logs too.
    if not app.debug and backend_is_local_sqlite():
        app.logger.warning(
            "SafeExam: no Turso/external database configured — falling back to a local "
            "SQLite file. On a serverless host this fails on the first query. "
            "Set SEB_TURSO_URL and SEB_TURSO_TOKEN."
        )


def backend_is_local_sqlite():
    from . import turso

    return not turso.is_enabled() and not os.environ.get("SEB_DATABASE_URI")


def _register_host_guard(app):
    """
    Reject requests whose Host header is not in ALLOWED_HOSTS.

    This is the Flask counterpart to Django's ALLOWED_HOSTS: it blocks
    Host-header spoofing, which otherwise lets an attacker poison absolute
    URLs the app generates (password-reset links, redirects, cached pages).
    An empty allowlist disables the check, which is the default in development.
    """
    from flask import request, abort

    @app.before_request
    def _check_host():
        allowed = app.config.get("ALLOWED_HOSTS") or ()
        if not allowed:
            return None
        # Strip any port before comparing: "example.com:443" -> "example.com".
        host = (request.host or "").split(":")[0].lower()
        if host in allowed:
            return None
        # Support one level of wildcard, e.g. ".vercel.app" or "*.vercel.app".
        for entry in allowed:
            suffix = entry[1:] if entry.startswith("*") else entry
            if suffix.startswith(".") and host.endswith(suffix):
                return None
        abort(400, description=f"Host '{host}' is not an allowed host.")


def _ensure_dirs(app):
    """
    Create the writable directories the app expects.

    On a read-only/serverless filesystem this is a no-op: frames and snapshots
    are stored in the database instead (FRAME_STORAGE = "db").
    """
    try:
        os.makedirs(app.instance_path, exist_ok=True)
        os.makedirs(app.config["SNAPSHOT_DIR"], exist_ok=True)
        os.makedirs(app.config["LIVE_DIR"], exist_ok=True)
        os.makedirs(os.path.join(PROJECT_ROOT, "logs"), exist_ok=True)
    except OSError:
        if app.config.get("FRAME_STORAGE") != "db":
            raise


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
    @app.errorhandler(400)
    def bad_request(e):
        description = getattr(e, "description", "") or ""
        return (
            render_template(
                "errors/400.html",
                description=description,
                host_blocked="not an allowed host" in description,
            ),
            400,
        )

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
