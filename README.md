# 🛡️ SafeExam — Secure Online Examination Platform (Flask)

SafeExam is an end-to-end **Safe Exam Browser** web application. It provides a
locked-down, monitored environment for online multiple-choice exams: role-based
access for admins/proctors and students, a full-screen exam runtime with
real-time integrity monitoring, server-enforced timing, automatic violation
handling, webcam-snapshot proctoring and exportable reports.

> This project was rebuilt from a single-file Flask script into a clean,
> testable application-factory + blueprints structure. The old files are kept
> untouched in `legacy_flask_backup/`.

---

## ✨ Features

**Authentication & roles**
- Register / login / logout with hashed passwords (Werkzeug) and Flask-Login.
- Two roles: **admin/proctor** and **student**, enforced by decorators.
- CSRF protection on every form (Flask-WTF).

**For admins / proctors**
- **Dashboard** with KPIs and charts (submissions trend, violations by type).
- **Exam authoring** — create, edit, publish/unpublish, **duplicate**, and delete exams;
  optional **availability window**, per-exam **violation limit** and **pass mark**.
- **Question management** — add / edit / delete MCQs, optional shuffle, and **bulk CSV import**.
- **User management** — create users, reset passwords, change roles, activate/deactivate,
  delete (with safeguards against locking yourself out or removing the last admin); search & filter.
- **Settings** — globally tune the proctoring policy (violation limit, risk window,
  snapshot interval, heartbeat timeout, default pass mark) from the UI.
- **Live monitor** — real-time candidate status, violations, time remaining, snapshot
  counts; lock / unlock / disqualify with one click.
- **Live screen monitoring** — open any in-progress attempt and watch the candidate's
  **shared screen and webcam in near real time** from the proctor panel (frames stream
  every ~1.5s). Reachable from the monitor table ("Live") and the attempt detail page.
- **Results & exam statistics** — average score, pass rate, score distribution and
  per-question **item analysis** (correct-rate); search/filter; per-exam CSV export.
- Per-attempt **review page** — answers vs. correct key, violation timeline, webcam
  snapshots, and **manual score override**.
- **Audit log** of proctor/admin actions.
- Export **PDF** and **CSV** integrity reports.

The interface uses a clean, professional design with an inline SVG icon set (no emoji)
and renders fully offline — no external icon fonts or image requests.

**For students**
- Dashboard of available exams with status (open / scheduled / completed).
- A "secure-mode" gate that requests full-screen + camera before starting.
- Auto-saved answers, a live countdown, and a clear result screen.

**Safe-Exam lock-down (exam runtime)**
- Full-screen enforcement; flags exit-fullscreen, tab switches and window blur.
- Blocks copy / cut / paste, right-click, text selection and risky keyboard
  shortcuts (Ctrl+C/V/X/S/P, F12, Ctrl+Shift+I/J/C, Ctrl+U).
- Heuristic dev-tools detection.
- **Server-enforced timing** — the clock is computed from the server; refreshing,
  closing the tab or clock-tampering can't extend it.
- **Server-side violation scoring** — weighted severities; the attempt is
  auto-terminated when the limit is reached (not just client-side).
- Heartbeat + state polling so a proctor's lock/disqualify takes effect live, and
  disconnects are detected.
- Periodic **webcam snapshots** uploaded for proctor review (configurable / optional).

---

## 🧱 Project structure

```
Safe-Exam-browser/
├── app.py                  # dev entry point  (python app.py)
├── wsgi.py                 # production entry  (gunicorn wsgi:app)
├── config.py               # env-driven configuration
├── seed.py                 # demo data (accounts + sample exam)
├── requirements.txt
├── .env.example            # copy to .env and edit
├── safeexam/               # application package
│   ├── __init__.py         # application factory
│   ├── extensions.py       # db, login_manager, csrf
│   ├── models.py           # User, Exam, Question, Submission, Answer, Violation, ProctorSnapshot
│   ├── forms.py            # Flask-WTF forms
│   ├── decorators.py       # admin_required / student_required
│   ├── proctoring.py       # violation severity + risk/termination rules
│   ├── services.py         # attempt lifecycle + grading
│   └── blueprints/         # auth · student · admin · api
├── templates/              # Jinja2 templates (base, auth, student, admin, errors, partials)
├── static/                 # style.css + js (timer, security, monitoring, exam, live_monitor)
├── tests/                  # pytest suite
├── instance/               # created at runtime: SQLite DB + uploaded snapshots
└── legacy_flask_backup/    # the original single-file app, preserved
```

---

## 🚀 Getting started

```bash
# 1. (recommended) create a virtual environment
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate       # macOS/Linux

# 2. install dependencies
pip install -r requirements.txt

# 3. (optional) configure environment
copy .env.example .env           # Windows  (cp on macOS/Linux)
#   then edit SEB_SECRET_KEY etc.

# 4. seed demo accounts + a sample exam
python seed.py

# 5. run
python app.py
```

Open **http://127.0.0.1:5000/**.

> **Upgrading from an earlier build?** The schema has grown (user activation,
> pass marks, settings, audit log). On startup the app **auto-adds any missing
> columns** to an existing SQLite database — just restart it, no data loss.
> (If you'd rather start fresh, delete the `instance/` folder and re-run
> `python seed.py`.)

### Demo accounts (after `python seed.py`)

| Role    | Username  | Password    |
|---------|-----------|-------------|
| Admin   | `admin`   | `admin123`  |
| Student | `student` | `student123`|

A published **"Sample Aptitude Test"** (5 questions) is created so you can try the
full flow immediately: sign in as the student, take the exam, then sign in as the
admin to watch the live monitor and export a report.

---

## ⚙️ Configuration (environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `SEB_SECRET_KEY` | dev key | **Set a long random value in production.** |
| `SEB_ENV` | `development` | `development` · `production` · `testing` |
| `SEB_DATABASE_URI` | local SQLite | Any SQLAlchemy URL (e.g. PostgreSQL). |
| `SEB_MAX_VIOLATIONS` | `3` | Cumulative severity that terminates an attempt. |
| `SEB_RISK_WINDOW_SECONDS` / `SEB_RISK_WINDOW_THRESHOLD` | `30` / `2` | "High-risk" burst detection. |
| `SEB_SNAPSHOT_INTERVAL` | `20` | Seconds between webcam snapshots (`0` disables). |
| `SEB_SECURE_COOKIES` | `0` | Set `1` when served over HTTPS. |

---

## ✅ Testing

```bash
pip install pytest
pytest
```

The suite covers registration/login, role protection, the full exam flow and
scoring, violation-limit disqualification, server-side time expiry, the state
API and report generation.

---

## 🚢 Production notes

- Run behind a real WSGI server, e.g. `gunicorn "wsgi:app"` (Linux) or
  `waitress-serve --call wsgi:create_app` (Windows), behind HTTPS.
- Set `SEB_ENV=production`, a strong `SEB_SECRET_KEY`, and `SEB_SECURE_COOKIES=1`.
- For multi-user/scale, point `SEB_DATABASE_URI` at PostgreSQL.
- **Webcam + screen monitoring require a secure context.** Browsers only allow
  camera and screen capture over **HTTPS** (or on `http://localhost` during
  development). Serve the app over HTTPS in production so live monitoring works.
- Live frames are stored under `instance/live/` (latest frame per attempt, overwritten);
  recorded snapshots are kept under `instance/snapshots/`.

---

## ⚠️ Security scope (important)

This is a **web-based** lock-down. It raises the cost of cheating and records a
strong audit trail, but a determined user with OS-level control can still bypass
browser restrictions. For high-stakes exams, pair it with a kiosk/native
lock-down client and live human proctoring. Treat violation data as signals for
review, not absolute proof.
