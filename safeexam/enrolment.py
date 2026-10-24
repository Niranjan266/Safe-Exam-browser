"""Class enrolment and bulk student import (shared by the teacher and admin portals)."""
import csv
import io
import secrets
import string
import time

from .extensions import db
from .models import User, ROLE_STUDENT

_ALPHABET = string.ascii_letters.translate(str.maketrans("", "", "lIO")) + "23456789"
MAX_IMPORT_BYTES = 512 * 1024

# One-time credential sheets, held in memory only (never written to the database)
# and handed out exactly once.
_PENDING = {}
_TTL = 15 * 60


def random_password(length=8):
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def stash_credentials(user_id, rows, class_label=""):
    now = time.time()
    for k in [k for k, v in _PENDING.items() if now - v["at"] > _TTL]:
        _PENDING.pop(k, None)
    token = secrets.token_urlsafe(16)
    _PENDING[token] = {"at": now, "user": user_id, "rows": rows, "class": class_label}
    return token


def pop_credentials(token, user_id):
    entry = _PENDING.get(token)
    if entry is None or entry["user"] != user_id or time.time() - entry["at"] > _TTL:
        return None
    return _PENDING.pop(token)


def can_enrol(user):
    return user is not None and user.role == ROLE_STUDENT and user.active


def import_students(raw, creator, school_class=None):
    """
    Create student logins from CSV text `username,full_name,email[,password]`.

    Returns (summary, credentials): summary holds created/duplicates/invalid
    counts, credentials is a list of (username, full_name, password) for the
    accounts created in this run.
    """
    rows = list(csv.reader(io.StringIO(raw.lstrip("﻿"))))
    if rows and [c.strip().lower() for c in rows[0][:1]] == ["username"]:
        rows = rows[1:]
    summary = {"created": 0, "duplicates": 0, "invalid": 0, "enrolled": 0}
    creds = []
    seen = set()
    for row in rows:
        if not row or not any(c.strip() for c in row):
            continue
        cells = [c.strip() for c in row] + ["", "", "", ""]
        username, full_name, email, password = cells[:4]
        if (not username or len(username) > 80 or len(full_name) > 120
                or len(email) > 120 or (email and "@" not in email)
                or (password and len(password) < 6)):
            summary["invalid"] += 1
            continue
        key = username.lower()
        existing = User.query.filter(db.func.lower(User.username) == key).first()
        if key in seen or existing:
            summary["duplicates"] += 1
            continue
        seen.add(key)
        password = password or random_password()
        u = User(username=username, full_name=full_name, email=email,
                 role=ROLE_STUDENT, active=True, created_by_id=creator.id)
        u.set_password(password)
        db.session.add(u)
        if school_class is not None:
            school_class.students.append(u)
            summary["enrolled"] += 1
        creds.append((username, full_name, password))
        summary["created"] += 1
    db.session.commit()
    return summary, creds


def enrol(school_class, user_ids):
    added = 0
    current = {s.id for s in school_class.students}
    for uid in user_ids:
        u = db.session.get(User, uid)
        if can_enrol(u) and u.id not in current:
            school_class.students.append(u)
            current.add(u.id)
            added += 1
    db.session.commit()
    return added


def unenrol(school_class, user_id):
    u = db.session.get(User, user_id)
    if u is not None and u in school_class.students:
        school_class.students.remove(u)
        db.session.commit()
        return True
    return False


def read_upload(file_storage):
    """Decode an uploaded CSV, or return None if missing/oversized/not text."""
    if not file_storage or not file_storage.filename:
        return None
    data = file_storage.read(MAX_IMPORT_BYTES + 1)
    if len(data) > MAX_IMPORT_BYTES:
        return None
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None
