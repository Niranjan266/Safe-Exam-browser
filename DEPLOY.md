# Deploying SafeExam

The app runs anywhere that can serve a WSGI application. Two setups are
documented here: **Vercel** (serverless) and a **normal server** (VPS, Render,
Railway — anything with a persistent disk).

---

## Storage: why the database matters

SafeExam continuously writes two kinds of image data during an exam:

| Data | Written by | Read by |
| --- | --- | --- |
| Live screen + webcam frames | the candidate's browser, ~every 1.5s | the proctor's live wall |
| Webcam snapshots | the candidate's browser, every N seconds | the attempt review page |

On a normal server these are JPEG files under `instance/`. On a serverless
host that does **not** work: the filesystem is read-only apart from `/tmp`, and
the function instance that receives a frame is not the one that later serves it
to the proctor, so the file is invisible.

`SEB_FRAME_STORAGE` selects the backend:

* `disk` — files under `instance/` (default locally; fastest)
* `db` — blobs in the database (required on Vercel)

The same applies to the database itself: a SQLite file cannot persist on
serverless. Use **Turso** (hosted libSQL, SQLite-compatible) instead.

---

## Option A — Vercel + Turso

### 1. Create the Turso database

```bash
turso db create safeexam
turso db show safeexam --url          # -> libsql://safeexam-<org>.turso.io
turso db tokens create safeexam       # -> the auth token
```

### 2. Create the schema and demo accounts

Run this **once**, from your machine, against the Turso database:

```powershell
$env:SEB_TURSO_URL   = "libsql://<your-db>.turso.io"
$env:SEB_TURSO_TOKEN = "<token>"
python scripts/init_db.py
```

```bash
export SEB_TURSO_URL="libsql://<your-db>.turso.io"
export SEB_TURSO_TOKEN="<token>"
python scripts/init_db.py
```

Add `--no-seed` to create the tables without the demo accounts.

### 3. Import the repository into Vercel

Vercel → **Add New → Project** → import this GitHub repo.
Framework preset: **Other**. Leave the build and output settings empty —
`vercel.json` and `api/index.py` already describe everything.

Name the project so the domain you want is generated, e.g. a project named
`safe-exam-browser` is served at `safe-exam-browser.vercel.app`.

### 4. Set the environment variables

Add these under **Settings → Environment Variables** (Production, and Preview
if you want preview deploys to work):

| Name | Value |
| --- | --- |
| `SEB_SECRET_KEY` | a long random string — **not** the development default |
| `SEB_TURSO_URL` | `libsql://<your-db>.turso.io` |
| `SEB_TURSO_TOKEN` | the Turso auth token |
| `SEB_ALLOWED_HOSTS` | *(optional)* comma-separated hostnames |

`api/index.py` already sets `SEB_ENV`, `SEB_FRAME_STORAGE=db`,
`SEB_SECURE_COOKIES=1` and `SEB_INIT_DB=0`, so you do not need those.

Generate a secret key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

> Never commit these values. `.env` is git-ignored; `.env.example` documents
> the names only.

### 5. Add a custom domain

**Settings → Domains → Add** → `exam.example.com`, then create the DNS record
Vercel shows you (usually a `CNAME` to `cname.vercel-dns.com`).

Make sure the hostname is in the allowlist — either add it to
`SEB_ALLOWED_HOSTS`, or to the defaults in `ProductionConfig.ALLOWED_HOSTS`.
A host that is not listed gets a `400`.

### Serverless caveats

* **Cold starts.** The first request after idle pays for a new container plus
  the round trip to Turso. `SEB_INIT_DB=0` keeps this as small as possible.
* **Write volume.** Each candidate pushes ~1.3 frames/second across screen and
  webcam. Frames overwrite one row per (attempt, kind), but every write still
  counts against your Turso plan. For a large cohort, prefer Option B or move
  frames to object storage.
* **Function duration.** Long uploads are bounded by your plan's limit.

---

## Option B — a normal server (recommended for real exams)

Anywhere with a persistent disk, the app runs as shipped: SQLite (or Postgres)
on disk, frames on disk, no serverless limits.

```bash
pip install -r requirements.txt gunicorn
export SEB_ENV=production
export SEB_SECRET_KEY="<long random string>"
export SEB_SECURE_COOKIES=1
export SEB_ALLOWED_HOSTS="exam.example.com"
python seed.py                      # first run only
gunicorn "wsgi:app" -b 0.0.0.0:8000 -w 4
```

On Windows use waitress instead:

```powershell
waitress-serve --port=8000 --call wsgi:create_app
```

Put nginx or Caddy in front for TLS. HTTPS is not optional: the exam runtime
needs `getUserMedia` and `getDisplayMedia`, which browsers only expose on
secure origins (`localhost` is treated as secure for development).

For Postgres instead of SQLite:

```bash
export SEB_DATABASE_URI="postgresql+psycopg://user:pass@host:5432/safeexam"
```

---

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `SEB_ENV` | `development` | `development` \| `production` \| `testing` |
| `SEB_SECRET_KEY` | dev placeholder | Session signing key. **Change in production.** |
| `SEB_DATABASE_URI` | local SQLite | Any SQLAlchemy URL. Ignored when Turso is set. |
| `SEB_TURSO_URL` | — | Turso database URL. Set with the token to enable Turso. |
| `SEB_TURSO_TOKEN` | — | Turso auth token. |
| `SEB_FRAME_STORAGE` | `db` if Turso, else `disk` | Where frames/snapshots live. |
| `SEB_ALLOWED_HOSTS` | empty in dev | Comma-separated Host allowlist. Empty = allow all. |
| `SEB_INIT_DB` | `1` | Create tables on startup. Set `0` in serverless production. |
| `SEB_SECURE_COOKIES` | `0` dev / `1` prod | Send cookies only over HTTPS. |
| `SEB_MAX_VIOLATIONS` | `3` | Violations before an attempt is terminated. |
| `SEB_SNAPSHOT_INTERVAL` | `20` | Seconds between webcam snapshots. `0` disables. |
| `SEB_HEARTBEAT_TIMEOUT` | `20` | Seconds before a silent candidate is flagged. |

---

## After deploying

1. Sign in with the seeded admin account and **change every demo password**
   immediately (Users → edit → set a new password).
2. Delete or deactivate the demo `teacher` and `student` accounts if this is a
   real deployment.
3. Confirm the allowlist: request the site with an unexpected `Host` header and
   check you get a `400`.
