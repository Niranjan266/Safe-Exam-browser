"""
Turso / libSQL support.

Turso is a hosted libSQL (SQLite-compatible) database. We use it so the app can
run on a serverless host such as Vercel, where the local filesystem is
read-only and ephemeral and a plain SQLite file would not survive.

The `libsql` driver is *almost* a drop-in for the stdlib `sqlite3` module, but
its Connection object does not implement `create_function`, which SQLAlchemy's
pysqlite dialect calls on every connect to register a REGEXP helper. The
dialect below subclasses pysqlite and skips the hooks libsql cannot provide.

Enable it by setting both:
    SEB_TURSO_URL    = libsql://<your-db>.turso.io
    SEB_TURSO_TOKEN  = <auth token>

When those are unset the app falls back to a normal local SQLite file, so
development is unaffected.
"""
import os

from sqlalchemy.dialects.sqlite.pysqlite import SQLiteDialect_pysqlite
from sqlalchemy.pool import NullPool


class _LibsqlDBAPI:
    """
    A DBAPI-2.0 facade over the `libsql` module.

    `libsql` exposes only Connection, Cursor, Error, connect and paramstyle.
    SQLAlchemy expects the rest of the PEP 249 surface — notably `Binary`,
    which it calls for every LargeBinary parameter (our live-frame JPEGs), and
    the exception hierarchy it uses to classify errors. libsql raises a single
    `Error` type, so the specific exception classes are aliased to it.
    """

    # PEP 249 module globals
    apilevel = "2.0"
    threadsafety = 1
    paramstyle = "qmark"

    # Bytes, not memoryview: libsql's parameter binding expects real bytes.
    Binary = bytes

    # pysqlite's dialect reads these during initialisation.
    sqlite_version = "3.45.0"
    sqlite_version_info = (3, 45, 0)
    version = "2.0"
    PARSE_DECLTYPES = 1
    PARSE_COLNAMES = 2

    def __init__(self, mod):
        self._mod = mod
        self.Connection = mod.Connection
        self.Cursor = mod.Cursor

        base = mod.Error
        self.Error = base
        for name in (
            "Warning", "InterfaceError", "DatabaseError", "DataError",
            "OperationalError", "IntegrityError", "InternalError",
            "ProgrammingError", "NotSupportedError",
        ):
            setattr(self, name, getattr(mod, name, base))

        if hasattr(mod, "sqlite_version_info"):
            self.sqlite_version_info = tuple(mod.sqlite_version_info)
            self.sqlite_version = ".".join(str(p) for p in self.sqlite_version_info)

    def connect(self, *args, **kwargs):
        return self._mod.connect(*args, **kwargs)

    # Converters/adapters are a pysqlite feature libsql lacks; the dialect
    # never needs them because we connect through an explicit creator.
    def register_converter(self, *args, **kwargs):
        return None

    def register_adapter(self, *args, **kwargs):
        return None


_DBAPI = None


def _dbapi():
    global _DBAPI
    if _DBAPI is None:
        import libsql

        _DBAPI = _LibsqlDBAPI(libsql)
    return _DBAPI


class SQLiteDialect_libsql(SQLiteDialect_pysqlite):
    """SQLAlchemy dialect that talks to Turso through the `libsql` driver."""

    name = "sqlite"
    driver = "libsql"
    supports_statement_cache = True

    @classmethod
    def import_dbapi(cls):
        return _dbapi()

    # Backwards-compatible alias (SQLAlchemy < 2.0 calls `dbapi()`).
    @classmethod
    def dbapi(cls):
        return cls.import_dbapi()

    def on_connect(self):
        # pysqlite registers a REGEXP function and JSON serializers here via
        # Connection.create_function(), which libsql does not implement.
        return None

    def _get_server_version_info(self, connection):
        # libsql does not expose sqlite_version_info; report a modern SQLite.
        return (3, 45, 0)

    # Turso manages transactions server-side; the PRAGMA-based isolation-level
    # probing in the SQLite dialect is not supported.
    def get_isolation_level(self, dbapi_connection):
        return "SERIALIZABLE"

    def set_isolation_level(self, dbapi_connection, level):
        pass

    def get_isolation_level_values(self, dbapi_conn):
        return ["SERIALIZABLE"]


def turso_settings():
    """Return (url, token) from the environment, or (None, None) if unset."""
    url = os.environ.get("SEB_TURSO_URL") or os.environ.get("TURSO_DATABASE_URL")
    token = os.environ.get("SEB_TURSO_TOKEN") or os.environ.get("TURSO_AUTH_TOKEN")
    if url and token:
        return url.strip(), token.strip()
    return None, None


def is_enabled():
    return all(turso_settings())


def make_creator():
    """Build the DBAPI connection factory SQLAlchemy will call."""
    url, token = turso_settings()

    def _connect():
        import libsql

        return libsql.connect(database=url, auth_token=token)

    return _connect


def engine_options():
    """
    SQLALCHEMY_ENGINE_OPTIONS for a Turso-backed engine.

    NullPool is deliberate: on serverless each invocation is short-lived, and a
    pooled remote connection would usually be dead by the time it is reused.
    """
    return {
        "creator": make_creator(),
        "poolclass": NullPool,
        "connect_args": {},
    }


def register():
    """Register the dialect so 'sqlite+libsql://' URLs resolve to it."""
    from sqlalchemy.dialects import registry

    registry.register("sqlite.libsql", "safeexam.turso", "SQLiteDialect_libsql")
