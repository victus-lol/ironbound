"""
IRONBOUND
Train. Level up. Dominate.

A fitness stat tracker that turns real gym data (lifts, runs, body
measurements, field tests) into RPG-style stats (STR / END / AGI / VIT /
POW / FLX), ranks you against published fitness standards, and shows exactly
what you need to reach the next tier.

Run locally:
    pip install -r requirements.txt
    python app.py
    -> http://localhost:5000

Production (Waitress):
    $env:IRONBOUND_SERVER="waitress"
    python app.py

Run tests:
    python -m unittest discover tests -v
"""

import os
import re
import csv
import sqlite3
import hashlib as hashlib_mod
import secrets
import math
import json
import time
from contextlib import contextmanager
from datetime import datetime, date, timedelta
from functools import wraps
from io import StringIO

from flask import (
    Flask, render_template, request, redirect, url_for, session, Response,
    flash, abort, g, has_app_context,
)
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_env_file(path):
    """Minimal .env loader (KEY=VALUE, # comments, no override of real env)."""
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key, value = key.strip(), value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass


_load_env_file(os.path.join(BASE_DIR, ".env"))

SECRET_KEY = os.environ.get("IRONBOUND_SECRET_KEY", "").strip()
SECRET_PATH = os.path.join(BASE_DIR, ".secret_key")
if SECRET_KEY:
    app.secret_key = SECRET_KEY
elif os.path.exists(SECRET_PATH):
    with open(SECRET_PATH) as f:
        app.secret_key = f.read().strip()
else:
    key = secrets.token_hex(32)
    with open(SECRET_PATH, "w") as f:
        f.write(key)
    app.secret_key = key

DB_PATH = os.environ.get("IRONBOUND_DB") or os.path.join(BASE_DIR, "ironbound.db")

# --- session hardening (production defaults; secure cookie opt-in via env) ---
COOKIE_SECURE = os.environ.get("IRONBOUND_COOKIE_SECURE", "0") == "1"
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    SESSION_COOKIE_NAME="ironbound_session",
    MAX_CONTENT_LENGTH=4 * 1024 * 1024,  # 4 MB upload cap (JSON import)
)

STATS = ["STR", "END", "AGI", "VIT", "POW", "FLX"]
STAT_META = {
    "STR": {"icon": "💪", "label": "Strength"},
    "END": {"icon": "🏃", "label": "Endurance"},
    "AGI": {"icon": "⚡", "label": "Agility"},
    "VIT": {"icon": "⚖️", "label": "Vitality"},
    "POW": {"icon": "🔥", "label": "Power"},
    "FLX": {"icon": "🤸", "label": "Flexibility"},
}

# Hover "cue cards": what a stat means, what feeds it, how to raise it.
# Shown as a bubble tooltip next to stat labels so the UI stays uncluttered.
STAT_GLOSSARY = {
    "STR": {
        "label": "Strength", "short": "Raw lifting power.",
        "what": "Raw lifting power — how much you can push, pull, squat, and carry relative to your own bodyweight.",
        "feeds": "Your best estimated 1-rep max for Bench, Squat, and Deadlift, divided by bodyweight.",
        "raise": "Log strength sets and accessories — heavier weights or more reps raise it.",
    },
    "END": {
        "label": "Endurance", "short": "How well your engine handles sustained effort.",
        "what": "Cardiovascular endurance — how efficiently your heart and lungs deliver oxygen over time.",
        "feeds": "An estimated VO₂max computed from your best run (distance ÷ time, Cooper-test style).",
        "raise": "Log runs, cycling, swimming, jump rope, or HIIT — regular minutes and pace build it.",
    },
    "AGI": {
        "label": "Agility", "short": "Quickness — how fast you accelerate and change direction.",
        "what": "Agility — your ability to accelerate, plant, and change direction at speed.",
        "feeds": "Your best 40 m sprint time (lower is better).",
        "raise": "Log a faster sprint, or agility drills like shuttles, ladders, and footwork.",
    },
    "VIT": {
        "label": "Vitality", "short": "Body composition and overall conditioning.",
        "what": "Vitality — body composition, core conditioning, and overall metabolic health.",
        "feeds": "Body-fat % estimated from waist and neck measurements (US Navy formula).",
        "raise": "Log body measurements and core / full-body circuits; dropping body fat lifts it fastest.",
    },
    "POW": {
        "label": "Power", "short": "Explosiveness — force produced quickly.",
        "what": "Power — how much force you can produce in a split second (the explosiveness component).",
        "feeds": "Your best vertical jump.",
        "raise": "Log a higher vertical jump, or explosive work like box jumps, jump squats, and power throws.",
    },
    "FLX": {
        "label": "Flexibility", "short": "Usable range of motion.",
        "what": "Flexibility and mobility — the healthy range of motion your joints can actually use.",
        "feeds": "Your best sit-and-reach score.",
        "raise": "Log a deeper sit & reach, or stretching / mobility sessions — daily short holds win.",
    },
}

# Smaller cue cards for the raw metrics that drive the stats.
METRIC_GLOSSARY = {
    "1rm": {"label": "Estimated 1RM",
            "what": "weight × (1 + reps ÷ 30). Epley's estimate of the most you could lift for one rep.",
            "feeds": "Feeds STR via 1RM ÷ bodyweight (relative strength)."},
    "bench": {"label": "Bench Press",
              "what": "Your best estimated 1-rep max on the bench press.",
              "feeds": "Feeds STR at 1RM ÷ bodyweight."},
    "squat": {"label": "Squat",
              "what": "Your best estimated 1-rep max on the squat.",
              "feeds": "Feeds STR at 1RM ÷ bodyweight."},
    "deadlift": {"label": "Deadlift",
                 "what": "Your best estimated 1-rep max on the deadlift.",
                 "feeds": "Feeds STR at 1RM ÷ bodyweight."},
    "vo2max": {"label": "VO₂max",
               "what": "Max millilitres of oxygen used per kg of bodyweight per minute — the gold-standard endurance measure.",
               "feeds": "Feeds END from your best run."},
    "vertical_jump": {"label": "Vertical jump",
                      "what": "Best standing vertical jump, in cm. A sprint for your legs against gravity.",
                      "feeds": "Feeds POW."},
    "sprint_40m": {"label": "40 m sprint",
                   "what": "Best 40 m sprint in seconds — a snapshot of top speed and acceleration.",
                   "feeds": "Feeds AGI (lower is better)."},
    "sit_and_reach": {"label": "Sit & reach",
                      "what": "How far past your toes you reach from a seated position, in cm.",
                      "feeds": "Feeds FLX."},
    "body_fat": {"label": "Body fat",
                 "what": "Estimated body-fat % from waist & neck (US Navy formula).",
                 "feeds": "Feeds VIT (lower is better)."},
    "bodyweight": {"label": "Bodyweight",
                   "what": "Your current bodyweight — the benchmark every lifting stat is divided by.",
                   "feeds": "Scales every lift into relative strength."},
}

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,24}$")
GENDERS = ["male", "female", "other"]

# ---------------------------------------------------------------------------
# DATABASE (context-managed connections)
# ---------------------------------------------------------------------------


@contextmanager
def db_conn():
    """Yield an open SQLite connection; always close it (and roll back on error)."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """\
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT,
    gender TEXT DEFAULT 'male',
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS profile (
    user_id INTEGER PRIMARY KEY,
    bodyweight_kg REAL,
    updated_at TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS bodyweight_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    bodyweight_kg REAL NOT NULL,
    logged_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS strength_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    lift TEXT NOT NULL,
    weight_kg REAL NOT NULL,
    reps INTEGER NOT NULL,
    logged_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS cardio_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    distance_km REAL NOT NULL,
    minutes REAL NOT NULL,
    logged_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS body_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    waist_cm REAL NOT NULL,
    neck_cm REAL NOT NULL,
    height_cm REAL NOT NULL,
    hip_cm REAL,
    logged_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS performance_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    vertical_jump_cm REAL,
    sprint_40m_s REAL,
    sit_and_reach_cm REAL,
    logged_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS exercise_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    exercise_key TEXT NOT NULL,
    exercise_name TEXT NOT NULL,
    feeds_stat TEXT NOT NULL,
    sets INTEGER NOT NULL DEFAULT 1,
    reps INTEGER,
    weight_kg REAL,
    minutes REAL,
    logged_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
"""

# Ordered migrations beyond the base schema. Each is a list of SQL statements.
MIGRATIONS = {
    2: [
        "CREATE INDEX IF NOT EXISTS idx_strength_user_date ON strength_logs(user_id, logged_at)",
        "CREATE INDEX IF NOT EXISTS idx_cardio_user_date ON cardio_logs(user_id, logged_at)",
        "CREATE INDEX IF NOT EXISTS idx_body_user_date ON body_logs(user_id, logged_at)",
        "CREATE INDEX IF NOT EXISTS idx_perf_user_date ON performance_logs(user_id, logged_at)",
        "CREATE INDEX IF NOT EXISTS idx_bw_user_date ON bodyweight_logs(user_id, logged_at)",
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)",
    ],
    3: [],
    4: [
        "CREATE TABLE IF NOT EXISTS exercise_logs ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "user_id INTEGER NOT NULL, "
        "exercise_key TEXT NOT NULL, "
        "exercise_name TEXT NOT NULL, "
        "feeds_stat TEXT NOT NULL, "
        "sets INTEGER NOT NULL DEFAULT 1, "
        "reps INTEGER, "
        "weight_kg REAL, "
        "minutes REAL, "
        "logged_at TEXT NOT NULL, "
        "FOREIGN KEY (user_id) REFERENCES users(id))",
        "CREATE INDEX IF NOT EXISTS idx_ex_user_date ON exercise_logs(user_id, logged_at)",
    ],
    5: [
        "CREATE TABLE IF NOT EXISTS plan_prefs ("
        "user_id INTEGER PRIMARY KEY, "
        "goal TEXT NOT NULL DEFAULT 'all_round', "
        "training_days INTEGER NOT NULL DEFAULT 3, "
        "rest_days TEXT NOT NULL DEFAULT '[]', "
        "focus_muscles TEXT NOT NULL DEFAULT '[]', "
        "diet_type TEXT NOT NULL DEFAULT 'non_veg', "
        "allergies TEXT NOT NULL DEFAULT '[]', "
        "diet_rules TEXT NOT NULL DEFAULT '', "
        "updated_at TEXT, "
        "FOREIGN KEY (user_id) REFERENCES users(id))",
    ],
    6: [
        "ALTER TABLE strength_logs ADD COLUMN source_exercise_log_id INTEGER",
    ],
    7: [
        # Offline sync: client-generated id makes replayed queue entries idempotent.
        "ALTER TABLE strength_logs ADD COLUMN client_id TEXT",
        "ALTER TABLE cardio_logs ADD COLUMN client_id TEXT",
        "ALTER TABLE body_logs ADD COLUMN client_id TEXT",
        "ALTER TABLE performance_logs ADD COLUMN client_id TEXT",
        "ALTER TABLE exercise_logs ADD COLUMN client_id TEXT",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_strength_client ON strength_logs(user_id, client_id) WHERE client_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_cardio_client ON cardio_logs(user_id, client_id) WHERE client_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_body_client ON body_logs(user_id, client_id) WHERE client_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_performance_client ON performance_logs(user_id, client_id) WHERE client_id IS NOT NULL",
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_exercise_client ON exercise_logs(user_id, client_id) WHERE client_id IS NOT NULL",
    ],
}


def _schema_version(conn):
    try:
        row = conn.execute("SELECT version FROM schema_version ORDER BY version DESC LIMIT 1").fetchone()
        return row["version"] if row else 0
    except sqlite3.OperationalError:
        return 0


def init_db():
    with db_conn() as conn:
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA journal_mode=WAL")
        version = _schema_version(conn)
        for target in sorted(MIGRATIONS):
            if target > version:
                for stmt in MIGRATIONS[target]:
                    try:
                        conn.execute(stmt)
                    except sqlite3.OperationalError as exc:
                        # SQLite DDL autocommits, so a partially-applied migration
                        # must be safe to re-run: re-adding an existing column is fine.
                        if "duplicate column name" not in str(exc):
                            raise
                conn.execute("INSERT INTO schema_version (version) VALUES (?)", (target,))
        # an existing pre-migration DB might have no hip_cm column
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(body_logs)").fetchall()]
        if "hip_cm" not in cols:
            conn.execute("ALTER TABLE body_logs ADD COLUMN hip_cm REAL")


# ---------------------------------------------------------------------------
# AUTH
# ---------------------------------------------------------------------------

LEGACY_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def hash_password(password):
    """PBKDF2-scrypt style password hash via werkzeug (slow, GPU-resistant)."""
    return generate_password_hash(password)


def create_user(username, password, gender="male"):
    password_hash = hash_password(password)
    with db_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO users (username, password_hash, gender, created_at) VALUES (?, ?, ?, ?)",
                (username, password_hash, gender, datetime.now().isoformat()),
            )
            return True
        except sqlite3.IntegrityError:
            return False  # username taken


def verify_user(username, password):
    """Verify credentials. Legacy SHA-256 hashes are transparently upgraded to the
    new scheme on successful login."""
    with db_conn() as conn:
        row = conn.execute("SELECT id, password_hash, salt FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            return None
        stored = row["password_hash"]
        if stored and "$" in stored:
            ok = check_password_hash(stored, password)
        elif stored and LEGACY_HASH_RE.match(stored):
            rehash = hashlib_mod.sha256(((row["salt"] or "") + password).encode()).hexdigest()
            ok = secrets.compare_digest(rehash, stored)
            if ok:
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (generate_password_hash(password), row["id"]),
                )
        else:
            return None
        return row["id"] if ok else None


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrapper


def current_user_id():
    return session.get("user_id")


# ---------------------------------------------------------------------------
# CSRF protection (dependency-free token in the signed session cookie)
# ---------------------------------------------------------------------------


def get_csrf_token():
    if "_csrf" not in session:
        session["_csrf"] = secrets.token_hex(16)
    return session["_csrf"]


@app.context_processor
def _inject_csrf():
    def _token():
        return get_csrf_token()
    return {"csrf_token": _token}


@app.context_processor
def _inject_player():
    """Persistent player HUD (level / XP / rank) for the topbar + sidebar."""
    uid = session.get("user_id")
    if not uid:
        return {}
    cached = getattr(g, "_ib_player", None)
    if cached is None:
        stats, _, _ = compute_stats(uid)
        ov = overall_score(stats)
        lvl, xp = level_from_score(ov)
        cached = {"player": {
            "level": lvl,
            "xp": xp,
            "rank": score_to_rank(ov) if ov is not None else "q",
            "overall": ov,
        }}
        g._ib_player = cached
    return cached


@app.context_processor
def _inject_glossary():
    """Explain-the-jargon cue cards available on every page."""
    return {"glossary": STAT_GLOSSARY, "metric_glossary": METRIC_GLOSSARY}


@app.before_request
def csrf_protect():
    if request.method not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    token = request.form.get("csrf_token") or request.headers.get("X-CSRF-Token", "")
    expected = session.get("_csrf", "")
    if not expected or not token or not secrets.compare_digest(token, expected):
        return Response("The form has expired. Please go back, reload the page, and try again.", status=400)


# ---------------------------------------------------------------------------
# Brute-force protection (in-memory sliding window per IP+user)
# ---------------------------------------------------------------------------

_LOGIN_ATTEMPTS = {}
_LOGIN_WINDOW = 300  # seconds
_LOGIN_MAX = 8


def _login_limited(key):
    now = time.time()
    stamps = _LOGIN_ATTEMPTS.setdefault(key, [])
    stamps[:] = [t for t in stamps if now - t < _LOGIN_WINDOW]
    return len(stamps) >= _LOGIN_MAX


def _record_failure(key):
    _LOGIN_ATTEMPTS.setdefault(key, []).append(time.time())


def _login_reset(key):
    _LOGIN_ATTEMPTS.pop(key, None)


# ---------------------------------------------------------------------------
# VALIDATION helpers (friendly errors, never a 500)
# ---------------------------------------------------------------------------


class ValidationError(Exception):
    pass


def form_float(name, label, minv=None, maxv=None, required=True, data=None):
    src = request.form if data is None else data
    raw = str(src.get(name, "") or "").strip()
    if raw == "":
        if required:
            raise ValidationError(f"{label} is required.")
        return None
    try:
        value = float(raw)
    except ValueError:
        raise ValidationError(f"{label} must be a number.")
    if minv is not None and value < minv:
        raise ValidationError(f"{label} must be at least {minv}.")
    if maxv is not None and value > maxv:
        raise ValidationError(f"{label} must be at most {maxv}.")
    return value


def form_int(name, label, minv=None, maxv=None, required=True, data=None):
    src = request.form if data is None else data
    raw = str(src.get(name, "") or "").strip()
    if raw == "":
        if required:
            raise ValidationError(f"{label} is required.")
        return None
    try:
        value = int(float(raw))
    except ValueError:
        raise ValidationError(f"{label} must be a whole number.")
    if minv is not None and value < minv:
        raise ValidationError(f"{label} must be at least {minv}.")
    if maxv is not None and value > maxv:
        raise ValidationError(f"{label} must be at most {maxv}.")
    return value


def form_logged_at(name="logged_at", data=None):
    """Accept an ISO datetime-local value (e.g. 2026-08-10T11:30); default to now."""
    src = request.form if data is None else data
    raw = str(src.get(name, "") or "").strip()
    if not raw:
        return datetime.now().isoformat()
    try:
        return datetime.fromisoformat(raw).isoformat()
    except ValueError:
        return datetime.now().isoformat()


def default_logged_at():
    return datetime.now().strftime("%Y-%m-%dT%H:%M")


# ---------------------------------------------------------------------------
# CALCULATION LAYER (raw logs -> real metrics)
# ---------------------------------------------------------------------------


def epley_1rm(weight_kg, reps):
    """Estimate 1-rep max from a set of weight x reps using the Epley formula."""
    if reps == 1:
        return weight_kg
    return weight_kg * (1 + reps / 30.0)


def best_1rm(user_id, lift_name, as_of=None):
    """Best estimated 1RM logged for a lift (optionally up to a cutoff date)."""
    with db_conn() as conn:
        if as_of:
            rows = conn.execute(
                "SELECT weight_kg, reps FROM strength_logs WHERE user_id = ? AND lift = ? AND logged_at <= ?",
                (user_id, lift_name, as_of),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT weight_kg, reps FROM strength_logs WHERE user_id = ? AND lift = ?", (user_id, lift_name)
            ).fetchall()

    if not rows:
        return None

    best = 0
    for row in rows:
        est = epley_1rm(row["weight_kg"], row["reps"])
        if est > best:
            best = est
    return round(best, 1)


def estimate_vo2max_cooper(distance_km):
    """Cooper 12-minute run test: VO2max = (distance_m - 504.9) / 44.73"""
    distance_m = distance_km * 1000
    return round((distance_m - 504.9) / 44.73, 1)


def best_vo2max(user_id, as_of=None):
    with db_conn() as conn:
        if as_of:
            rows = conn.execute(
                "SELECT distance_km, minutes FROM cardio_logs WHERE user_id = ? AND logged_at <= ?", (user_id, as_of)
            ).fetchall()
        else:
            rows = conn.execute("SELECT distance_km, minutes FROM cardio_logs WHERE user_id = ?", (user_id,)).fetchall()

    if not rows:
        return None

    best = 0
    for row in rows:
        normalized_distance = row["distance_km"] * (12.0 / row["minutes"])
        vo2 = estimate_vo2max_cooper(normalized_distance)
        if vo2 > best:
            best = vo2
    return best


def estimate_body_fat_navy(waist_cm, neck_cm, height_cm, hip_cm=None, gender="male"):
    """US Navy body fat formula. Male: 495/(1.0324 - 0.19077*log10(waist-neck)
    + 0.15456*log10(height)) - 450. Female uses hip: 495/(1.29579 -
    0.35004*log10(waist+hip-neck) + 0.22100*log10(height)) - 450."""
    if waist_cm <= neck_cm:
        return None  # invalid measurement
    try:
        if gender == "female" and hip_cm and hip_cm > 0:
            bf = 495 / (
                1.29579
                - 0.35004 * math.log10(waist_cm + hip_cm - neck_cm)
                + 0.22100 * math.log10(height_cm)
            ) - 450
        else:
            bf = 495 / (
                1.0324
                - 0.19077 * math.log10(waist_cm - neck_cm)
                + 0.15456 * math.log10(height_cm)
            ) - 450
    except (ValueError, ZeroDivisionError):
        return None
    return round(bf, 1)


def latest_body_fat(user_id, as_of=None):
    with db_conn() as conn:
        if as_of:
            row = conn.execute(
                "SELECT waist_cm, neck_cm, height_cm, hip_cm FROM body_logs WHERE user_id = ? AND logged_at <= ? "
                "ORDER BY logged_at DESC LIMIT 1", (user_id, as_of)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT waist_cm, neck_cm, height_cm, hip_cm FROM body_logs WHERE user_id = ? ORDER BY logged_at DESC LIMIT 1",
                (user_id,)
            ).fetchone()

    if not row:
        return None
    gender = get_user_gender(user_id)
    return estimate_body_fat_navy(row["waist_cm"], row["neck_cm"], row["height_cm"], row["hip_cm"], gender)


def count_logs(user_id):
    """Number of entries per log table — shared by the dashboard + analytics."""
    with db_conn() as conn:
        return {
            "strength": conn.execute("SELECT COUNT(*) as c FROM strength_logs WHERE user_id = ?", (user_id,)).fetchone()["c"],
            "cardio": conn.execute("SELECT COUNT(*) as c FROM cardio_logs WHERE user_id = ?", (user_id,)).fetchone()["c"],
            "body": conn.execute("SELECT COUNT(*) as c FROM body_logs WHERE user_id = ?", (user_id,)).fetchone()["c"],
            "performance": conn.execute("SELECT COUNT(*) as c FROM performance_logs WHERE user_id = ?", (user_id,)).fetchone()["c"],
            "exercise": conn.execute("SELECT COUNT(*) as c FROM exercise_logs WHERE user_id = ?", (user_id,)).fetchone()["c"],
        }


def get_bodyweight(user_id):
    with db_conn() as conn:
        row = conn.execute("SELECT bodyweight_kg FROM profile WHERE user_id = ?", (user_id,)).fetchone()
        return row["bodyweight_kg"] if row else None


def bodyweight_history(user_id):
    """Chronological bodyweight points for the trend chart."""
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT bodyweight_kg, logged_at FROM bodyweight_logs WHERE user_id = ? ORDER BY logged_at ASC",
            (user_id,),
        ).fetchall()
    return [{"date": r["logged_at"][:10], "kg": r["bodyweight_kg"]} for r in rows]


def bodyfat_history(user_id):
    """Chronological body-fat estimates (US Navy) for the trend chart."""
    gender = get_user_gender(user_id)
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT waist_cm, neck_cm, height_cm, hip_cm, logged_at FROM body_logs "
            "WHERE user_id = ? ORDER BY logged_at ASC",
            (user_id,),
        ).fetchall()
    out = []
    for r in rows:
        bf = estimate_body_fat_navy(r["waist_cm"], r["neck_cm"], r["height_cm"],
                                    r["hip_cm"], gender)
        if bf is not None:
            out.append({"date": r["logged_at"][:10], "pct": bf})
    return out


def get_user_gender(user_id):
    with db_conn() as conn:
        row = conn.execute("SELECT gender FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["gender"] if row else "male"


def best_performance(user_id, column, as_of=None, want_min=False):
    """Best logged field-test value. Columns are internal constants (safe to
    interpolate since they are validated against an allow-list below)."""
    if column not in ("vertical_jump_cm", "sprint_40m_s", "sit_and_reach_cm"):
        return None
    want_as = f"AND {column} IS NOT NULL"
    with db_conn() as conn:
        if as_of:
            rows = conn.execute(
                f"SELECT {column} AS v FROM performance_logs "
                f"WHERE user_id = ? {want_as} AND logged_at <= ?",
                (user_id, as_of),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT {column} AS v FROM performance_logs WHERE user_id = ? {want_as}",
                (user_id,),
            ).fetchall()

    if not rows:
        return None
    vals = [r["v"] for r in rows]
    return round(min(vals) if want_min else max(vals), 2)


def best_vertical_jump(user_id, as_of=None):
    return best_performance(user_id, "vertical_jump_cm", as_of=as_of)


def best_sprint_40m(user_id, as_of=None):
    return best_performance(user_id, "sprint_40m_s", as_of=as_of, want_min=True)


def best_sit_and_reach(user_id, as_of=None):
    return best_performance(user_id, "sit_and_reach_cm", as_of=as_of)


# ---------------------------------------------------------------------------
# BENCHMARK / TIER ENGINE
# ---------------------------------------------------------------------------
# Each metric has 4 reference points: Sedentary, Healthy, Enthusiast, Pro.
# These map to stat scores 0, 33, 66, 100; we interpolate between them.
# Lifts/VO2max/body-fat sourced from StrengthLevel/NSCA, ACSM/Cooper, and ACE.
# Field-test standards (vertical jump, sprint, sit-and-reach) are general
# population estimates from common fitness-testing norms.

BENCHMARKS_MALE = {
    "bench_ratio":    {"sedentary": 0.3,  "healthy": 0.75, "enthusiast": 1.25, "pro": 2.0},
    "squat_ratio":    {"sedentary": 0.5,  "healthy": 1.0,  "enthusiast": 1.7,  "pro": 2.5},
    "deadlift_ratio": {"sedentary": 0.75, "healthy": 1.5,  "enthusiast": 2.0,  "pro": 2.8},
    "vo2max":         {"sedentary": 30,   "healthy": 40,   "enthusiast": 50,   "pro": 65},
    "body_fat":       {"sedentary": 28,   "healthy": 20,   "enthusiast": 15,   "pro": 9},
    "vertical_jump":  {"sedentary": 25,   "healthy": 40,   "enthusiast": 55,   "pro": 70},
    "sprint_40m":     {"sedentary": 7.0,  "healthy": 6.4,  "enthusiast": 5.7,  "pro": 4.6},
    "sit_and_reach":  {"sedentary": 5,    "healthy": 15,   "enthusiast": 25,   "pro": 35},
}

BENCHMARKS_FEMALE = {
    "bench_ratio":    {"sedentary": 0.2,  "healthy": 0.5,  "enthusiast": 0.85, "pro": 1.35},
    "squat_ratio":    {"sedentary": 0.4,  "healthy": 0.8,  "enthusiast": 1.4,  "pro": 2.0},
    "deadlift_ratio": {"sedentary": 0.6,  "healthy": 1.15, "enthusiast": 1.6,  "pro": 2.2},
    "vo2max":         {"sedentary": 22,   "healthy": 32,   "enthusiast": 42,   "pro": 58},
    "body_fat":       {"sedentary": 35,   "healthy": 28,   "enthusiast": 23,   "pro": 16},
    "vertical_jump":  {"sedentary": 20,   "healthy": 32,   "enthusiast": 45,   "pro": 60},
    "sprint_40m":     {"sedentary": 7.8,  "healthy": 7.1,  "enthusiast": 6.4,  "pro": 5.3},
    "sit_and_reach":  {"sedentary": 10,   "healthy": 20,   "enthusiast": 30,   "pro": 40},
}

TIER_SCORES = {"sedentary": 0, "healthy": 33, "enthusiast": 66, "pro": 100}
TIER_LABELS = {"sedentary": "Average", "healthy": "Healthy", "enthusiast": "Enthusiast", "pro": "Pro"}


def get_benchmarks(gender):
    if gender == "female":
        return BENCHMARKS_FEMALE
    return BENCHMARKS_MALE


def interpolate_score(value, benchmark_key, benchmarks, inverted=False):
    """Map a raw metric value to a 0-100 stat score between the 4 tiers."""
    tiers = benchmarks[benchmark_key]
    points = [
        (tiers["sedentary"], TIER_SCORES["sedentary"]),
        (tiers["healthy"], TIER_SCORES["healthy"]),
        (tiers["enthusiast"], TIER_SCORES["enthusiast"]),
        (tiers["pro"], TIER_SCORES["pro"]),
    ]

    if inverted:
        points = sorted(points, key=lambda p: -p[0])
    else:
        points = sorted(points, key=lambda p: p[0])

    if (not inverted and value <= points[0][0]) or (inverted and value >= points[0][0]):
        return 0
    if (not inverted and value >= points[-1][0]) or (inverted and value <= points[-1][0]):
        return 100

    for i in range(len(points) - 1):
        x0, y0 = points[i]
        x1, y1 = points[i + 1]
        in_range = (x0 <= value <= x1) if not inverted else (x1 <= value <= x0)
        if in_range:
            if x1 == x0:
                return y0
            ratio = (value - x0) / (x1 - x0)
            return round(y0 + ratio * (y1 - y0), 1)

    return 0


# ---------------------------------------------------------------------------
# STAT AGGREGATION + RPG LAYER
# ---------------------------------------------------------------------------

def score_to_rank(score):
    """Convert 0-100 stat score to an RPG rank letter."""
    if score is None:
        return "?"
    if score >= 90:
        return "S"
    if score >= 75:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    if score >= 20:
        return "D"
    return "E"


def overall_score(stats):
    """Average of all non-empty stats (None if nothing logged yet)."""
    values = [v for v in stats.values() if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def level_from_score(score):
    """Turn a 0-100 score into (level, xp_to_next_level 0-100)."""
    if score is None:
        return (0, 0)
    lvl = int(score // 10) + 1
    xp = int(round((score % 10) * 10))
    return lvl, xp


# ---------------------------------------------------------------------------
# TRAINING CREDIT — makes every log visibly move a stat.
# ---------------------------------------------------------------------------
# Each stat score is "metric performance + recent training credit".  Metrics
# (PRs, tests) stay the dominant driver, but a bounded 0-10 credit from the
# last 7 days of logged work rewards consistency and accessory work, so a
# daily bicep-curl session visibly nudges STR instead of changing nothing.

_TRAIN_K = 10.0   # max credit
_TRAIN_SAT = 420.0  # effort points at which credit reaches ~63% of max


def _train_effort(user_id, as_of=None, since_days=7):
    """Effort points logged in the last `since_days` days per stat.

    Points approximate work done: tonnage for lifts, minutes for timed work.
    """
    if as_of:
        cutoff = datetime.fromisoformat(as_of) - timedelta(days=since_days)
        to = as_of
    else:
        cutoff = datetime.now() - timedelta(days=since_days)
        to = datetime.now().isoformat()
    from_iso = cutoff.isoformat()
    loads = {k: 0.0 for k in STATS}

    def _add(stat, pts):
        if stat in loads:
            loads[stat] += pts

    with db_conn() as conn:
        for r in conn.execute(
            "SELECT weight_kg, reps FROM strength_logs WHERE user_id = ? AND logged_at >= ? AND logged_at <= ?",
            (user_id, from_iso, to),
        ):
            _add("STR", (r["weight_kg"] * r["reps"]) / 6.0)

        for r in conn.execute(
            "SELECT distance_km, minutes FROM cardio_logs WHERE user_id = ? AND logged_at >= ? AND logged_at <= ?",
            (user_id, from_iso, to),
        ):
            _add("END", (r["minutes"] or 0) * 10.0)

        for r in conn.execute(
            "SELECT feeds_stat, sets, reps, weight_kg, minutes FROM exercise_logs "
            "WHERE user_id = ? AND logged_at >= ? AND logged_at <= ?",
            (user_id, from_iso, to),
        ):
            if r["minutes"] is not None:
                _add(r["feeds_stat"], (r["minutes"] or 0) * 10.0)
            else:
                load_kg = r["weight_kg"] if r["weight_kg"] is not None else 60.0
                reps = r["reps"] or 1
                _add(r["feeds_stat"], (load_kg * reps * (r["sets"] or 1)) / 6.0)
    return loads


def training_credit(user_id, as_of=None):
    """0..~10 bonus per stat from the last week of training (returns rounded 1dp)."""
    loads = _train_effort(user_id, as_of=as_of)
    out = {}
    for k, pts in loads.items():
        if pts <= 0:
            out[k] = 0.0
        else:
            credit = _TRAIN_K * (1.0 - math.exp(-pts / _TRAIN_SAT))
            out[k] = round(min(_TRAIN_K, credit), 1)
    return out


def current_raw_metrics(user_id, as_of=None):
    """Latest (or as-of) raw value per metric — the single source of truth
    used by the stat engine, the tier-gap calculator, and the Standards page."""
    bw = get_bodyweight(user_id)
    lifts = {lift: best_1rm(user_id, lift, as_of=as_of) for lift in ["bench", "squat", "deadlift"]}
    return {
        "bodyweight": bw,
        "lifts": lifts,
        "bench_ratio": round(lifts["bench"] / bw, 2) if bw and lifts["bench"] else None,
        "squat_ratio": round(lifts["squat"] / bw, 2) if bw and lifts["squat"] else None,
        "deadlift_ratio": round(lifts["deadlift"] / bw, 2) if bw and lifts["deadlift"] else None,
        "vo2max": best_vo2max(user_id, as_of=as_of),
        "vertical_jump": best_vertical_jump(user_id, as_of=as_of),
        "sprint_40m": best_sprint_40m(user_id, as_of=as_of),
        "sit_and_reach": best_sit_and_reach(user_id, as_of=as_of),
        "body_fat": latest_body_fat(user_id, as_of=as_of),
    }


def _stats_cache():
    """Per-request memo for stat computation (skipped outside a request/app context)."""
    if has_app_context():
        if not hasattr(g, "_ib_stats_cache"):
            g._ib_stats_cache = {}
        return g._ib_stats_cache
    return None


def _clear_stats_cache():
    """Drop the per-request memo after the underlying data changes."""
    if has_app_context() and hasattr(g, "_ib_stats_cache"):
        g._ib_stats_cache = {}


def compute_stats(user_id, as_of=None):
    cache = _stats_cache()
    if cache is not None:
        key = (user_id, as_of)
        if key in cache:
            return cache[key]

    m = current_raw_metrics(user_id, as_of=as_of)
    benchmarks = get_benchmarks(get_user_gender(user_id))
    stats = {k: None for k in STATS}
    details = {}

    if m["bodyweight"]:
        lift_scores = []
        for lift, key in [("bench", "bench_ratio"), ("squat", "squat_ratio"), ("deadlift", "deadlift_ratio")]:
            one_rm = m["lifts"][lift]
            if one_rm:
                ratio = one_rm / m["bodyweight"]
                score = interpolate_score(ratio, key, benchmarks)
                lift_scores.append(score)
                details[lift] = {"1rm_kg": one_rm, "ratio": round(ratio, 2), "score": score}
        if lift_scores:
            stats["STR"] = round(sum(lift_scores) / len(lift_scores), 1)

    vo2 = m["vo2max"]
    if vo2:
        stats["END"] = interpolate_score(vo2, "vo2max", benchmarks)
        details["vo2max"] = {"value": vo2, "score": stats["END"]}

    vj = m["vertical_jump"]
    if vj:
        stats["POW"] = interpolate_score(vj, "vertical_jump", benchmarks)
        details["vertical_jump"] = {"value": vj, "score": stats["POW"]}

    sp = m["sprint_40m"]
    if sp:
        stats["AGI"] = interpolate_score(sp, "sprint_40m", benchmarks, inverted=True)
        details["sprint"] = {"value": sp, "score": stats["AGI"]}

    sr = m["sit_and_reach"]
    if sr:
        stats["FLX"] = interpolate_score(sr, "sit_and_reach", benchmarks)
        details["sit_and_reach"] = {"value": sr, "score": stats["FLX"]}

    bf = m["body_fat"]
    if bf:
        stats["VIT"] = interpolate_score(bf, "body_fat", benchmarks, inverted=True)
        details["body_fat"] = {"value": bf, "score": stats["VIT"]}

    # bounded "training credit" for recent work — a stat still shows up (small)
    # for pure exercise logs, and every log moves something on the sheet.
    credit = training_credit(user_id, as_of=as_of)
    details["training"] = credit
    for k in STATS:
        c = credit.get(k, 0.0)
        if c > 0:
            if stats[k] is None:
                stats[k] = c
            else:
                stats[k] = round(min(100.0, stats[k] + c), 1)

    ranks = {k: score_to_rank(v) for k, v in stats.items()}
    result = (stats, details, ranks)
    if cache is not None:
        cache[key] = result
    return result


def compute_history(user_id):
    """Timeline of stat scores recomputed as-of every date something was logged."""
    with db_conn() as conn:
        dates = set()
        for table in ["strength_logs", "cardio_logs", "body_logs", "performance_logs",
                      "exercise_logs", "bodyweight_logs"]:
            rows = conn.execute(f"SELECT logged_at FROM {table} WHERE user_id = ?", (user_id,)).fetchall()
            dates.update(r["logged_at"] for r in rows)

    timeline = []
    for d in sorted(dates):
        stats, _, _ = compute_stats(user_id, as_of=d)
        entry = {"date": d[:10]}
        entry.update(stats)
        timeline.append(entry)
    return timeline


def compute_trends(history):
    """Delta (latest vs previous) for each stat that has data."""
    trends = {k: None for k in STATS}
    if len(history) >= 2:
        prev, cur = history[-2], history[-1]
        for k in trends:
            if prev.get(k) is not None and cur.get(k) is not None:
                trends[k] = round(cur[k] - prev[k], 1)
    return trends


def compare_to_tier(user_id, target_tier):
    """Compare current raw metrics against a tier; report what's needed."""
    m = current_raw_metrics(user_id)
    benchmarks = get_benchmarks(get_user_gender(user_id))
    gaps = {}

    if m["bodyweight"]:
        for lift, key in [("bench", "bench_ratio"), ("squat", "squat_ratio"), ("deadlift", "deadlift_ratio")]:
            one_rm = m["lifts"][lift]
            target_ratio = benchmarks[key][target_tier]
            target_kg = round(target_ratio * m["bodyweight"], 1)
            current_kg = one_rm or 0
            gaps[lift] = {
                "current_kg": current_kg,
                "target_kg": target_kg,
                "gap_kg": round(target_kg - current_kg, 1),
                "met": current_kg >= target_kg,
            }

    vo2 = m["vo2max"]
    target_vo2 = benchmarks["vo2max"][target_tier]
    gaps["vo2max"] = {
        "current": vo2 or 0,
        "target": target_vo2,
        "gap": round(target_vo2 - (vo2 or 0), 1),
        "met": (vo2 or 0) >= target_vo2,
    }

    vj = m["vertical_jump"]
    target_vj = benchmarks["vertical_jump"][target_tier]
    gaps["vertical_jump"] = {
        "current": vj or 0,
        "target": target_vj,
        "gap": round(target_vj - (vj or 0), 1),
        "met": (vj or 0) >= target_vj,
    }

    sp = m["sprint_40m"]
    target_sp = benchmarks["sprint_40m"][target_tier]
    gaps["sprint"] = {
        "current": sp or 0,
        "target": target_sp,
        "gap": round((sp or 0) - target_sp, 2),
        "met": (sp is not None and sp <= target_sp),
    }

    sr = m["sit_and_reach"]
    target_sr = benchmarks["sit_and_reach"][target_tier]
    gaps["sit_and_reach"] = {
        "current": sr or 0,
        "target": target_sr,
        "gap": round(target_sr - (sr or 0), 1),
        "met": (sr or 0) >= target_sr,
    }

    bf = m["body_fat"]
    target_bf = benchmarks["body_fat"][target_tier]
    gaps["body_fat"] = {
        "current": bf or None,
        "target": target_bf,
        "gap": round((bf - target_bf), 1) if bf else None,
        "met": (bf is not None and bf <= target_bf),
    }

    return gaps


def greeting():
    h = datetime.now().hour
    if h < 12:
        return "Good morning"
    if h < 18:
        return "Good afternoon"
    return "Good evening"


# ---------------------------------------------------------------------------
# GAMIFICATION — streaks, volume, records, badges
# ---------------------------------------------------------------------------

_LOG_TABLES = ("strength_logs", "cardio_logs", "body_logs", "performance_logs",
               "exercise_logs", "bodyweight_logs")


def _log_dates(user_id):
    """All 'YYYY-MM-DD' dates on which the user logged anything."""
    dates = set()
    with db_conn() as conn:
        for table in _LOG_TABLES:
            for row in conn.execute(f"SELECT logged_at FROM {table} WHERE user_id = ?", (user_id,)):
                if row["logged_at"]:
                    dates.add(row["logged_at"][:10])
    return dates


def _consecutive_days(sorted_dates):
    """Longest run of consecutive dates in an ascending list of date strings."""
    best = run = 0
    prev = None
    for d in sorted_dates:
        if prev is not None and (date.fromisoformat(d) - date.fromisoformat(prev)).days == 1:
            run += 1
        else:
            run = 1
        best = max(best, run)
        prev = d
    return best


# Rank boundaries used for "points to next rank" nudges on the dashboard.
RANK_BOUNDS = [("E", 20), ("D", 40), ("C", 60), ("B", 75), ("A", 90), ("S", 100)]


def rank_progress(score):
    """Given a 0-100 stat score, report the next rank up and points needed."""
    if score is None:
        return None
    for i, (rank, bound) in enumerate(RANK_BOUNDS):
        if score < bound:
            return {"next": rank, "points": round(bound - score, 1)}
    return {"next": None, "points": 0}


def training_heatmap(user_id, weeks=13):
    """GitHub-style grid of daily training intensity for the last `weeks` weeks.

    Returns a list of weeks (each a list of 7 day dicts) ordered oldest-first,
    Monday-first, covering full weeks only. Intensity level is 0-3 by log count.
    """
    today = datetime.now().date()
    this_monday = _iso_monday(today)
    end = this_monday + timedelta(days=6)  # Sunday of the current week
    start = end - timedelta(days=weeks * 7 - 1)
    counts = {}
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT logged_at FROM strength_logs WHERE user_id = ? "
            "UNION ALL SELECT logged_at FROM cardio_logs WHERE user_id = ? "
            "UNION ALL SELECT logged_at FROM body_logs WHERE user_id = ? "
            "UNION ALL SELECT logged_at FROM performance_logs WHERE user_id = ? "
            "UNION ALL SELECT logged_at FROM exercise_logs WHERE user_id = ?",
            (user_id, user_id, user_id, user_id, user_id),
        ).fetchall()
    for r in rows:
        stamp = r["logged_at"]
        if stamp:
            d = stamp[:10]
            if start.isoformat() <= d <= today.isoformat():
                counts[d] = counts.get(d, 0) + 1
    today_iso = today.isoformat()
    grid = []
    cursor = start
    while cursor <= end:
        week = []
        for _ in range(7):
            iso = cursor.isoformat()
            c = counts.get(iso, 0)
            level = 0 if c == 0 else 1 if c == 1 else 2 if c <= 3 else 3
            week.append({
                "iso": iso,
                "count": c,
                "level": level,
                "today": iso == today_iso,
                "future": iso > today_iso,
            })
            cursor += timedelta(days=1)
        grid.append(week)
    return grid


def compute_streak(user_id):
    """Current and best training-day streak. A day counts if any log was added.

    The current streak isn't considered broken until a full day passes with no
    log, so logging "tomorrow" doesn't wipe it out at midnight."""
    sorted_dates = sorted(_log_dates(user_id))
    if not sorted_dates:
        return {"current": 0, "best": 0, "last_log": None}
    set_dates = set(sorted_dates)
    today = datetime.now().date()
    anchor = today.isoformat() if today.isoformat() in set_dates else None
    if anchor is None:
        yesterday = (today - timedelta(days=1)).isoformat()
        if yesterday in set_dates:
            anchor = yesterday
    current = 0
    if anchor is not None:
        cursor = date.fromisoformat(anchor)
        while cursor.isoformat() in set_dates:
            current += 1
            cursor = cursor - timedelta(days=1)
    return {"current": current, "best": _consecutive_days(sorted_dates),
            "last_log": sorted_dates[-1] if sorted_dates else None}


def _iso_monday(today):
    return today - timedelta(days=today.weekday())


def weekly_volume(user_id):
    """Total kg lifted (weight x reps) this ISO week, oldest-to-now."""
    monday = _iso_monday(datetime.now().date())
    start = monday.isoformat()
    with db_conn() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(weight_kg * reps), 0) AS v, COUNT(*) AS s "
            "FROM strength_logs WHERE user_id = ? AND logged_at >= ?", (user_id, start),
        ).fetchone()
    return {"volume": row["v"], "sessions": row["s"]}


def volume_series(user_id, weeks=8):
    """Per-week lifting volume for the last `weeks` weeks (oldest first)."""
    this_monday = _iso_monday(datetime.now().date())
    series = []
    with db_conn() as conn:
        for offset in range(weeks - 1, -1, -1):
            monday = this_monday - timedelta(days=7 * offset)
            sunday = monday + timedelta(days=7)  # exclusive end
            row = conn.execute(
                "SELECT COALESCE(SUM(weight_kg * reps), 0) AS v, COUNT(*) AS s "
                "FROM strength_logs WHERE user_id = ? AND logged_at >= ? AND logged_at < ?",
                (user_id, monday.isoformat(), sunday.isoformat()),
            ).fetchone()
            series.append({
                "label": monday.strftime("%b %d"),
                "volume": row["v"],
                "sessions": row["s"],
                "is_current": offset == 0,
            })
    return series


def personal_records(user_id):
    """Best logged value per lift/test, each with the date it was achieved."""
    prs = {}
    with db_conn() as conn:
        for lift in LIFT_LABELS:
            best = None
            for r in conn.execute(
                "SELECT weight_kg, reps, logged_at FROM strength_logs WHERE user_id = ? AND lift = ?",
                (user_id, lift),
            ):
                est = epley_1rm(r["weight_kg"], r["reps"])
                if best is None or est > best["value"]:
                    best = {"value": round(est, 1),
                            "text": f"{r['weight_kg']} kg × {int(r['reps'])} reps",
                            "date": (r["logged_at"] or "")[:10]}
            if best:
                prs[f"lift_{lift}"] = best

        longest = best_vo2 = None
        for r in conn.execute("SELECT distance_km, minutes, logged_at FROM cardio_logs WHERE user_id = ?", (user_id,)):
            if longest is None or r["distance_km"] > longest["value"]:
                longest = {"value": round(r["distance_km"], 2), "date": (r["logged_at"] or "")[:10]}
            if r["minutes"]:
                vo2 = estimate_vo2max_cooper(r["distance_km"] * (12.0 / r["minutes"]))
                if best_vo2 is None or vo2 > best_vo2["value"]:
                    best_vo2 = {"value": vo2, "date": (r["logged_at"] or "")[:10]}
        if longest:
            prs["longest_run"] = longest
        if best_vo2:
            prs["vo2max"] = best_vo2

        for col, key in [("vertical_jump_cm", "vertical_jump"), ("sprint_40m_s", "sprint_40m"),
                         ("sit_and_reach_cm", "sit_and_reach")]:
            best = None
            for r in conn.execute(
                f"SELECT {col}, logged_at FROM performance_logs WHERE user_id = ?", (user_id,)
            ).fetchall():
                v = r[col]
                if v is None:
                    continue
                if best is None or (v < best["value"] if key == "sprint_40m" else v > best["value"]):
                    best = {"value": v, "date": (r["logged_at"] or "")[:10]}
            if best:
                prs[key] = best
    return prs


def gamification_context(user_id):
    """Counts + derived numbers used by badge conditions."""
    streak = compute_streak(user_id)
    counts = {"s": 0, "c": 0, "b": 0, "p": 0, "x": 0, "w": 0}
    with db_conn() as conn:
        tags = [("strength_logs", "s"), ("cardio_logs", "c"), ("body_logs", "b"),
                ("performance_logs", "p"), ("exercise_logs", "x"), ("bodyweight_logs", "w")]
        for table, key in tags:
            row = conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE user_id = ?", (user_id,)).fetchone()
            counts[key] = row["c"]
    prs = personal_records(user_id)
    stats, _, _ = compute_stats(user_id)
    ov = overall_score(stats)
    ctx = {
        "total_logs": sum(counts.values()),
        "strength_sets": counts["s"],
        "runs": counts["c"],
        "body_logs": counts["b"],
        "field_tests": counts["p"],
        "exercise_logs": counts["x"],
        "bodyweight_logs": counts["w"],
        "streak_current": streak["current"],
        "streak_best": streak["best"],
        "distinct_days": len(_log_dates(user_id)),
        "weekly_volume": weekly_volume(user_id)["volume"],
        "pr_count": len(prs),
        "has_any_strength_pr": any(k.startswith("lift_") for k in prs),
        "rank": score_to_rank(ov),
        "level": level_from_score(ov)[0],
    }
    return ctx


BADGES = [
    {"key": "first_steps", "icon": "🥉", "name": "First steps", "tier": "bronze",
     "desc": "Log your very first entry", "check": lambda c: c["total_logs"] >= 1},
    {"key": "iron_lover", "icon": "🏋️", "name": "Iron lover", "tier": "bronze",
     "desc": "Log your first strength set", "check": lambda c: c["strength_sets"] >= 1},
    {"key": "on_the_move", "icon": "🏃", "name": "On the move", "tier": "bronze",
     "desc": "Log your first run", "check": lambda c: c["runs"] >= 1},
    {"key": "precise", "icon": "📏", "name": "The Measured", "tier": "bronze",
     "desc": "Log your first body measurement", "check": lambda c: c["body_logs"] >= 1},
    {"key": "week_warrior", "icon": "🗓️", "name": "Week warrior", "tier": "silver",
     "desc": "Train 7 days in a row", "check": lambda c: c["streak_best"] >= 7},
    {"key": "consistency", "icon": "📅", "name": "Consistent", "tier": "silver",
     "desc": "Train on 20 different days", "check": lambda c: c["distinct_days"] >= 20},
    {"key": "record_breaker", "icon": "🏆", "name": "Record-breaker", "tier": "silver",
     "desc": "Set any strength PR (best 1RM)", "check": lambda c: c["has_any_strength_pr"]},
    {"key": "all_rounder", "icon": "🎯", "name": "All-rounder", "tier": "gold",
     "desc": "Hold 6 personal records", "check": lambda c: c["pr_count"] >= 6},
    {"key": "volume_monster", "icon": "💪", "name": "Volume monster", "tier": "gold",
     "desc": "Lift 5,000 kg in one week", "check": lambda c: c["weekly_volume"] >= 5000},
    {"key": "ranked", "icon": "🎖️", "name": "Ranked fighter", "tier": "silver",
     "desc": "Reach overall rank B or better", "check": lambda c: c["rank"] in ("S", "A", "B")},
    {"key": "centurion", "icon": "⚔️", "name": "Centurion", "tier": "gold",
     "desc": "Accumulate 100 logs", "check": lambda c: c["total_logs"] >= 100},
    {"key": "lunar", "icon": "🌙", "name": "Lunar champion", "tier": "platinum",
     "desc": "Keep a 28-day training streak", "check": lambda c: c["streak_best"] >= 28},
    {"key": "elite", "icon": "👑", "name": "Elite", "tier": "platinum",
     "desc": "Reach overall rank S", "check": lambda c: c["rank"] == "S"},
    {"key": "level_10", "icon": "⭐", "name": "Seasoned", "tier": "gold",
     "desc": "Reach account level 10", "check": lambda c: c["level"] >= 10},
    {"key": "legend", "icon": "🔥", "name": "Legend", "tier": "platinum",
     "desc": "Log 500 entries", "check": lambda c: c["total_logs"] >= 500},
]


def check_badges(user_id):
    """Evaluate every badge against the user's current data."""
    ctx = gamification_context(user_id)
    badges = [{k: v for k, v in b.items() if k != "check"} for b in BADGES]
    for b, defn in zip(badges, BADGES):
        b["earned"] = bool(defn["check"](ctx))
    return badges, ctx


# ---------------------------------------------------------------------------
# EXERCISE LIBRARY
# ---------------------------------------------------------------------------

# muscle-group label -> body-map regions to highlight on the exercise cards
MAP_BY_MUSCLE = {
    "Chest": ["pecs"],
    "Back": ["lats", "traps"],
    "Legs (Quads/Glutes)": ["quads", "glutes"],
    "Legs (Hamstrings)": ["hamstrings"],
    "Shoulders": ["delts"],
    "Biceps": ["biceps"],
    "Triceps": ["triceps"],
    "Forearms / Grip": ["forearms"],
    "Glutes": ["glutes"],
    "Calves": ["calves"],
    "Cardiovascular": ["heart"],
    "Cardio — Swim / Bike": ["heart"],
    "Power / Explosiveness": ["quads", "glutes"],
    "Agility": ["calves", "quads"],
    "Flexibility / Mobility": ["hamstrings"],
    "Abs / Core": ["abs"],
    "Body composition / Core": ["abs"],
    "Cardio — Sprint Intervals (HIIT)": ["heart"],
    "Cardio — Steady State (Base)": ["heart"],
    "Agility — Footwork": ["calves"],
    "Agility — Change of Direction": ["quads", "calves"],
    "Power — Lower Body": ["glutes", "quads", "calves"],
    "Power — Upper Body": ["pecs", "delts", "triceps"],
    "Flexibility — Hips & Hamstrings": ["hamstrings"],
    "Body Comp — Daily Movement (NEAT)": ["heart"],
}

EXERCISE_LIBRARY = [
    {"muscle": "Chest", "feeds_stat": "STR",
     "gym_exercise": "Barbell Bench Press",
     "gym_tip": "3-4 sets of 5-8 reps, progressive overload weekly",
     "home_alternative": "Push-ups (feet elevated for extra difficulty)",
     "home_tip": "3-4 sets to near-failure; add a weighted backpack once bodyweight gets easy"},
    {"muscle": "Back", "feeds_stat": "STR",
     "gym_exercise": "Barbell Row / Lat Pulldown",
     "gym_tip": "3-4 sets of 6-10 reps, focus on squeezing shoulder blades",
     "home_alternative": "Pull-ups (or doorway rows using a sturdy table)",
     "home_tip": "Can't do a full pull-up yet? Use resistance bands or negative reps"},
    {"muscle": "Legs (Quads/Glutes)", "feeds_stat": "STR",
     "gym_exercise": "Barbell Squat",
     "gym_tip": "3-4 sets of 5-8 reps; your highest-leverage exercise for overall STR",
     "home_alternative": "Bulgarian split squats / pistol squat progressions",
     "home_tip": "Single-leg work replaces most of the loading a barbell squat gives you"},
    {"muscle": "Legs (Hamstrings)", "feeds_stat": "STR",
     "gym_exercise": "Deadlift",
     "gym_tip": "3-5 sets of 3-6 reps, prioritize form over weight always",
     "home_alternative": "Single-leg Romanian deadlift (bodyweight or backpack)",
     "home_tip": "A slow lowering (eccentric) phase substitutes well for missing load"},
    {"muscle": "Shoulders", "feeds_stat": "STR",
     "gym_exercise": "Overhead Press",
     "gym_tip": "3 sets of 6-10 reps",
     "home_alternative": "Pike push-ups",
     "home_tip": "Elevate feet to increase shoulder loading"},
    {"muscle": "Biceps", "feeds_stat": "STR",
     "gym_exercise": "Barbell / Dumbbell Curl",
     "gym_tip": "3 sets of 8-12 reps, no swinging — strict form",
     "home_alternative": "Chin-ups or towel curls with a filled water bottle",
     "home_tip": "Negatives (slow lowering) build pulling strength without weights"},
    {"muscle": "Triceps", "feeds_stat": "STR",
     "gym_exercise": "Close-Grip Bench / Skullcrushers",
     "gym_tip": "3 sets of 8-12 reps",
     "home_alternative": "Diamond push-ups / bench dips",
     "home_tip": "Keep elbows tucked — full range of motion drives growth"},
    {"muscle": "Forearms / Grip", "feeds_stat": "STR",
     "gym_exercise": "Farmer's Carry / Wrist Curls",
     "gym_tip": "2-3 sets, carry heavy and stay tall",
     "home_alternative": "Dead hangs + towel hangs from a bar or doorframe",
     "home_tip": "Grip strength transfers to every other lift you do"},
    {"muscle": "Glutes", "feeds_stat": "STR",
     "gym_exercise": "Barbell Hip Thrust",
     "gym_tip": "3-4 sets of 8-12 reps, squeeze hard at the top",
     "home_alternative": "Single-leg glute bridges / frog pumps",
     "home_tip": "Add a backpack full of books across your hips for load"},
    {"muscle": "Calves", "feeds_stat": "STR",
     "gym_exercise": "Standing Calf Raise",
     "gym_tip": "3-4 sets of 10-15 reps, full stretch at the bottom",
     "home_alternative": "Single-leg calf raises on a step",
     "home_tip": "Slow tempo (2s up, 3s down) works without heavy weights"},
    {"muscle": "Cardiovascular", "feeds_stat": "END",
     "gym_exercise": "Treadmill intervals or rowing machine",
     "gym_tip": "20-30 min, mix steady-state with 1-2 interval sessions/week",
     "home_alternative": "Outdoor running, jump rope, or stair climbing",
     "home_tip": "Jump rope is one of the most space-efficient cardio tools that exists"},
    {"muscle": "Cardio — Swim / Bike", "feeds_stat": "END",
     "gym_exercise": "Pool laps or spin bike",
     "gym_tip": "3-4 sessions/week of 30-45 min easy-to-moderate effort",
     "home_alternative": "Cycling outdoors or high-cadence bodyweight circuits",
     "home_tip": "Swimming is gentlest on joints while building lung capacity fast"},
    {"muscle": "Power / Explosiveness", "feeds_stat": "POW",
     "gym_exercise": "Power Clean / Box Jumps / Sled Pushes",
     "gym_tip": "4-6 sets of 3-5 explosive reps, full rest — quality over fatigue",
     "home_alternative": "Broad jumps, jump squats, plyo push-ups",
     "home_tip": "Explode up as fast as possible, land soft; 3-4 rounds of 6 reps"},
    {"muscle": "Agility", "feeds_stat": "AGI",
     "gym_exercise": "5-10-5 Shuttle / Agility Ladder / Cone Drills",
     "gym_tip": "5-8 shuttle runs at max effort, 1:3 work-to-rest ratio",
     "home_alternative": "Shuttle runs between markers, tic-tac footwork, high-knee sprints",
     "home_tip": "Stay low with arms pumping — quick feet beat tall strides for AGI"},
    {"muscle": "Flexibility / Mobility", "feeds_stat": "FLX",
     "gym_exercise": "Sit-and-reach / loaded stretching / yoga flow",
     "gym_tip": "10-15 min daily after training; hold static stretches 20-30s",
     "home_alternative": "Couch stretch, hamstring slides, hip openers, cat-cow",
     "home_tip": "Consistency beats intensity — a daily 10 minutes moves FLX fast"},
    {"muscle": "Abs / Core", "feeds_stat": "VIT",
     "gym_exercise": "Cable Crunches / Weighted Planks",
     "gym_tip": "3 sets to fatigue; core responds to tension, not volume",
     "home_alternative": "Hollow-body holds, leg raises, planks",
     "home_tip": "Brace like you're about to be punched — not just crunches"},
    {"muscle": "Body composition / Core", "feeds_stat": "VIT",
     "gym_exercise": "Full-body strength + cable core work",
     "gym_tip": "Consistent training + calorie balance drives body-fat change more than any exercise",
     "home_alternative": "Bodyweight circuits (burpees, mountain climbers, planks)",
     "home_tip": "VIT responds most to diet consistency and sleep — training is only part of it"},
    {"muscle": "Cardio — Sprint Intervals (HIIT)", "feeds_stat": "END",
     "gym_exercise": "Treadmill sprints / rower intervals / assault bike",
     "gym_tip": "6-8 x 30s hard, 90s easy — the fastest way to lift your VO₂max ceiling",
     "home_alternative": "Burpee or shuttle intervals, hill sprints, jump-rope rounds",
     "home_tip": "Go genuinely hard on the work interval — pace is the whole point"},
    {"muscle": "Cardio — Steady State (Base)", "feeds_stat": "END",
     "gym_exercise": "Incline walk, easy bike, or long-row",
     "gym_tip": "30-60 min at a conversational pace, 2-3x/week alongside intervals",
     "home_alternative": "Long walk/jog, rucking with a backpack, or cycling to work",
     "home_tip": "Build the base before the speed — steady volume makes intervals feel easy"},
    {"muscle": "Agility — Footwork", "feeds_stat": "AGI",
     "gym_exercise": "Agility ladder, mini hurdles, dot drills",
     "gym_tip": "3-5 minutes of quick-feet patterns after warm-up, quality over speed",
     "home_alternative": "Tape a ladder on the floor — ikky shuffle, in-in-out, icky hops",
     "home_tip": "Light feet, small steps, arms driving — land on the balls of your feet"},
    {"muscle": "Agility — Change of Direction", "feeds_stat": "AGI",
     "gym_exercise": "Pro-agility (5-10-5) shuttle, T-drill, zig-zag cone work",
     "gym_tip": "4-6 max-effort reps with full rest; sharp cuts and low body position",
     "home_alternative": "Shuttle runs between two markers with a touch on each line",
     "home_tip": "The faster you decelerate and plant, the faster you leave — drill the plant"},
    {"muscle": "Power — Lower Body", "feeds_stat": "POW",
     "gym_exercise": "Box jumps, trap-bar jumps, broad jumps, Kettlebell swings",
     "gym_tip": "4-6 sets of 3-5 explosive reps — land soft, reset, then explode again",
     "home_alternative": "Jump squats, broad jumps, single-leg bounds, stair sprints",
     "home_tip": "Rest 60-90s between sets; power work dies the moment you get sloppy"},
    {"muscle": "Power — Upper Body", "feeds_stat": "POW",
     "gym_exercise": "Medicine ball slams, overhead throws, plyo push-ups",
     "gym_tip": "3-5 sets of 5-8 powerful reps, throw everything you have",
     "home_alternative": "Clapping push-ups, wall-ball slams with a pillow, band punch-outs",
     "home_tip": "Think speed of movement, not muscle burn — snap the arms through"},
    {"muscle": "Flexibility — Hips & Hamstrings", "feeds_stat": "FLX",
     "gym_exercise": "Couch stretch, hamstring slides, 90/90 hip switch",
     "gym_tip": "Hold 30-45s per side after training; tight hips cap every other stat",
     "home_alternative": "Deep lunge holds, seated forward folds, figure-4 stretch",
     "home_tip": "Breathe into the stretch and relax the target muscle — tension blocks gains"},
    {"muscle": "Body Comp — Daily Movement (NEAT)", "feeds_stat": "VIT",
     "gym_exercise": "Walking pad / incline treadmill at work",
     "gym_tip": "8-12k steps/day — the single easiest lever for body-fat management",
     "home_alternative": "Walk after meals, take the stairs, carry groceries by hand",
     "home_tip": "Steps you don't notice add up fast — the average person burns ~100 kcal per mile"},
]

EXERCISE_MINUTES = {  # library entries that are naturally timed, not rep-counted
    "Flexibility / Mobility", "Flexibility — Hips & Hamstrings",
    "Cardiovascular", "Cardio — Swim / Bike", "Cardio — Sprint Intervals (HIIT)",
    "Cardio — Steady State (Base)", "Body Comp — Daily Movement (NEAT)",
}

EXERCISE_BY_KEY = {item["muscle"]: item for item in EXERCISE_LIBRARY}


def exercise_by_key(key):
    return EXERCISE_BY_KEY.get(key)


# ---------------------------------------------------------------------------
# TRAINING PLAN + DIET
# ---------------------------------------------------------------------------
# A user tells us their goal, how many days a week they want to train, which
# days are rest days, which muscle groups deserve extra focus, and what they
# can (and can't) eat. From that we deterministically generate a weekly
# timetable and a day-by-day general food chart, both reusing the exercise
# library so every entry stays consistent with the real "Log it" flows.

PLAN_GOALS = {
    "muscle_building": {
        "label": "Muscle building",
        "icon": "🏋️",
        "short": "Build size and strength — body-part splits, compound lifts first.",
        "pattern": "split",
        "diet": "high_protein",
    },
    "fat_loss": {
        "label": "Fat loss / recomposition",
        "icon": "🔥",
        "short": "Drop fat, keep muscle — full-body lifts plus cardio, proteins up, portions down.",
        "pattern": "conditioning",
        "diet": "deficit",
    },
    "weight_loss": {
        "label": "General weight loss",
        "icon": "⚖️",
        "short": "On-the-scale results — mostly full-body + cardio, calorie-conscious diet.",
        "pattern": "conditioning",
        "diet": "deficit",
    },
    "body_shaping": {
        "label": "Body shaping / toning",
        "icon": "💃",
        "short": "Definition and posture — core, glutes, mobility plus lighter full-body work.",
        "pattern": "shaping",
        "diet": "balanced",
    },
    "all_round": {
        "label": "All-round fitness",
        "icon": "🛡️",
        "short": "A balanced week — strength, a bit of cardio, mobility and core every week.",
        "pattern": "balanced",
        "diet": "balanced",
    },
}

DIET_TYPES = {
    "veg": "🌱 Vegetarian — no meat, fish or egg",
    "eggetarian": "🥚 Eggetarian — plants, dairy and egg",
    "pescatarian": "🐟 Pescatarian — plants, dairy, egg and fish",
    "non_veg": "🍗 Non-veg — everything including chicken and mutton",
}

ALLERGIES = {
    "dairy": "Dairy (milk, curd, paneer, butter, ghee)",
    "gluten": "Gluten (wheat, roti, bread, pasta)",
    "soy": "Soy (tofu, soy sauce)",
    "nuts": "Tree nuts (almonds, cashews, walnuts)",
    "peanuts": "Peanuts",
    "seafood": "Seafood (fish, prawns)",
    "shellfish": "Shellfish (shrimp, crab, lobster)",
    "eggs": "Eggs",
    "sesame": "Sesame (til, tahini)",
}

ALLERGY_FOODS = {  # keywords that flag a meal as containing an allergen
    "dairy": ("milk", "curd", "paneer", "butter", "ghee", "yoghurt", "yogurt", "cheese", "lassi", "khoya"),
    "gluten": ("wheat", "roti", "chapati", "naan", "bread", "pasta", "noodle", "cereal", "granola", "sandwich"),
    "soy": ("soy", "tofu", "tempeh"),
    "nuts": ("almond", "cashew", "walnut", "pistachio", "pecan"),
    "peanuts": ("peanut", "groundnut"),
    "seafood": ("fish", "salmon", "tuna", "sardine"),
    "shellfish": ("shrimp", "prawn", "crab", "lobster", "squid"),
    "eggs": ("egg", "omelette", "boiled"),
    "sesame": ("sesame", "til"),
}

PLAN_MUSCLES = sorted(EXERCISE_BY_KEY.keys())  # valid focus-muscle choices

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Goal-specific default weekly split: a rotation of "day templates", each a
# list of muscle-group keys (single-item entries mean one gym exercise).
_PLAN_SPLITS = {
    "muscle_building": {
        1: [["Chest", "Back", "Legs (Quads/Glutes)"]],
        2: [["Chest", "Back", "Shoulders"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Biceps", "Triceps"]],
        3: [["Chest", "Shoulders", "Triceps"], ["Back", "Biceps", "Forearms / Grip"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"]],
        4: [["Chest", "Triceps"], ["Back", "Biceps"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Shoulders", "Abs / Core"]],
        5: [["Chest"], ["Back"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Shoulders", "Abs / Core"], ["Biceps", "Triceps", "Forearms / Grip"]],
        6: [["Chest"], ["Back"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Shoulders"], ["Biceps", "Triceps", "Abs / Core"], ["Cardiovascular"]],
        7: [["Chest"], ["Back"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Shoulders"], ["Biceps", "Triceps", "Forearms / Grip"], ["Cardiovascular"], ["Flexibility / Mobility"]],
    },
    "fat_loss": {
        1: [["Chest", "Back", "Legs (Quads/Glutes)"]],
        2: [["Chest", "Back", "Legs (Quads/Glutes)", "Abs / Core"], ["Cardio — Sprint Intervals (HIIT)"]],
        3: [["Chest", "Back", "Legs (Quads/Glutes)", "Abs / Core"], ["Cardio — Sprint Intervals (HIIT)"], ["Cardio — Steady State (Base)"]],
        4: [["Chest", "Back", "Legs (Quads/Glutes)"], ["Cardio — Steady State (Base)"], ["Legs (Hamstrings)", "Calves", "Abs / Core"], ["Cardio — Sprint Intervals (HIIT)"]],
        5: [["Chest", "Back"], ["Cardio — Steady State (Base)"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Cardio — Sprint Intervals (HIIT)"], ["Shoulders", "Biceps", "Triceps", "Abs / Core"]],
        6: [["Chest", "Back"], ["Cardio — Steady State (Base)"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Cardio — Sprint Intervals (HIIT)"], ["Shoulders", "Biceps", "Triceps"], ["Abs / Core", "Flexibility / Mobility"]],
        7: [["Chest", "Back"], ["Cardio — Steady State (Base)"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Cardio — Sprint Intervals (HIIT)"], ["Shoulders", "Biceps", "Triceps"], ["Abs / Core", "Flexibility / Mobility"], ["Cardio — Steady State (Base)"]],
    },
    "weight_loss": {
        1: [["Chest", "Back", "Legs (Quads/Glutes)", "Abs / Core"]],
        2: [["Chest", "Back", "Legs (Quads/Glutes)"], ["Cardio — Steady State (Base)"]],
        3: [["Chest", "Back", "Legs (Quads/Glutes)", "Abs / Core"], ["Cardio — Steady State (Base)"], ["Cardio — Swim / Bike"]],
        4: [["Chest", "Back", "Legs (Quads/Glutes)"], ["Cardio — Steady State (Base)"], ["Shoulders", "Biceps", "Triceps", "Abs / Core"], ["Cardio — Sprint Intervals (HIIT)"]],
        5: [["Chest", "Back"], ["Cardio — Steady State (Base)"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Cardio — Sprint Intervals (HIIT)"], ["Shoulders", "Triceps", "Biceps", "Abs / Core"]],
        6: [["Chest", "Back"], ["Cardio — Steady State (Base)"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Cardio — Sprint Intervals (HIIT)"], ["Shoulders", "Triceps", "Biceps"], ["Abs / Core", "Flexibility / Mobility"]],
        7: [["Chest", "Back"], ["Cardio — Steady State (Base)"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Cardio — Sprint Intervals (HIIT)"], ["Shoulders", "Triceps", "Biceps"], ["Abs / Core", "Flexibility / Mobility"], ["Cardio — Steady State (Base)"]],
    },
    "body_shaping": {
        1: [["Glutes", "Abs / Core"]],
        2: [["Glutes", "Legs (Quads/Glutes)"], ["Abs / Core", "Flexibility / Mobility"]],
        3: [["Glutes", "Legs (Hamstrings)"], ["Abs / Core", "Cardio — Steady State (Base)"], ["Shoulders", "Biceps", "Triceps"]],
        4: [["Glutes", "Legs (Quads/Glutes)"], ["Abs / Core", "Back"], ["Chest", "Shoulders"], ["Cardio — Sprint Intervals (HIIT)"]],
        5: [["Glutes", "Legs (Hamstrings)"], ["Abs / Core"], ["Back", "Biceps"], ["Shoulders", "Triceps"], ["Cardio — Steady State (Base)"]],
        6: [["Glutes", "Legs (Quads/Glutes)"], ["Abs / Core"], ["Chest", "Shoulders"], ["Back", "Biceps", "Triceps"], ["Cardio — Steady State (Base)"], ["Flexibility / Mobility"]],
        7: [["Glutes", "Legs (Quads/Glutes)"], ["Abs / Core"], ["Chest", "Shoulders"], ["Back", "Biceps", "Triceps"], ["Cardio — Steady State (Base)"], ["Flexibility / Mobility"], ["Legs (Hamstrings)", "Calves"]],
    },
    "all_round": {
        1: [["Chest", "Back", "Legs (Quads/Glutes)", "Abs / Core"]],
        2: [["Chest", "Back", "Legs (Quads/Glutes)"], ["Cardio — Steady State (Base)"]],
        3: [["Chest", "Back", "Legs (Quads/Glutes)", "Abs / Core"], ["Cardio — Steady State (Base)"], ["Flexibility / Mobility"]],
        4: [["Chest", "Back", "Legs (Quads/Glutes)"], ["Cardio — Steady State (Base)"], ["Shoulders", "Biceps", "Triceps", "Abs / Core"], ["Flexibility — Hips & Hamstrings"]],
        5: [["Chest", "Back"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Shoulders", "Biceps", "Triceps"], ["Cardio — Steady State (Base)"], ["Abs / Core", "Flexibility / Mobility"]],
        6: [["Chest", "Back"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Shoulders", "Biceps", "Triceps"], ["Cardio — Steady State (Base)"], ["Abs / Core"], ["Flexibility / Mobility"]],
        7: [["Chest", "Back"], ["Legs (Quads/Glutes)", "Legs (Hamstrings)", "Calves"], ["Shoulders", "Biceps", "Triceps"], ["Cardio — Steady State (Base)"], ["Abs / Core"], ["Flexibility / Mobility"], ["Power / Explosiveness"]],
    },
}

# Rest-day "active recovery" light suggestions (no log needed).
_REST_SUGGESTIONS = [
    ("🚶", "Easy walk", "20-30 min at a comfortable pace keeps circulation moving."),
    ("🤸", "Light stretching", "5-10 min of gentle mobility — hips, shoulders, spine."),
    ("💧", "Hydrate & sleep", "Aim for 7-9 h sleep; water before meals helps recovery."),
    ("🧘", "Breath work", "10 slow deep breaths, 5 counts in, 6 counts out."),
    ("🧊", "Recovery", "A warm shower or light foam rolling on the sorest muscles."),
]

# General food pools per meal slot and diet type. Each meal may contain
# allergen keywords (see ALLERGY_FOODS) so the generator can automatically
# swap anything a user is allergic to for a safe alternative.
_MEAL_POOLS = {
    "veg": {
        "Breakfast": [
            "Vegetable upma + a glass of milk",
            "Oats porridge with banana, dates and cinnamon",
            "Paneer bhurji + multigrain toast",
            "Moong dal chilla with mint chutney",
            "Peanut butter toast + a piece of fruit",
            "Scrambled tofu with spinach and whole-wheat bread",
            "Poha with peanuts and a small glass of milk",
            "Besan chilla stuffed with onion and tomato",
        ],
        "Lunch": [
            "Roti + dal + seasonal vegetable + curd",
            "Vegetable pulao with raita",
            "Brown rice + rajma (kidney beans) + salad",
            "Chapati + chole + cucumber salad",
            "Mix veg + paneer curry + one roti",
            "Quinoa bowl with roasted vegetables and beans",
            "Khichdi with ghee and a side of curd",
            "Veg burger patty with mashed potato and salad",
        ],
        "Evening snack": [
            "Roasted chana (chickpeas) + green tea",
            "A handful of almonds and walnuts",
            "Fruit chaat with lemon and black pepper",
            "Sprout chaat (moong sprouts with lemon)",
            "Vegetable soup with a slice of toast",
            "Peanut chikki or roasted makhana",
            "Trail mix with dried fruit and seeds",
            "Grilled corn with lime and chaat masala",
        ],
        "Dinner": [
            "Roti + mixed dal + ghee + salad",
            "Vegetable curry + rice + a glass of buttermilk",
            "Paneer tikka + mint chutney + salad",
            "Dal soup + two multigrain rotis",
            "Stuffed capsicum or cabbage paratha + curd",
            "Veg khichdi with roasted papad",
            "Stir-fried vegetables + rice noodles",
            "Mushroom curry + one roti + salad",
        ],
    },
    "eggetarian": {
        "Breakfast": [
            "2 boiled eggs + vegetable upma",
            "Omelette with onion-tomato + whole-wheat toast",
            "Egg bhurji + one roti",
            "Oats porridge with an egg-white omelette",
            "Vegetable upma + a glass of milk",
            "Scrambled eggs with spinach and milk coffee",
            "Moong dal chilla with a boiled egg on the side",
            "Poha with a boiled egg and peanuts",
        ],
        "Lunch": [
            "Roti + dal + vegetable + curd",
            "Egg curry + one chapati + salad",
            "Brown rice + rajma + a boiled egg",
            "Vegetable pulao + raita + egg omelette",
            "Paneer curry + roti + cucumber salad",
            "Quinoa bowl with eggs and grilled vegetables",
            "Chapati + chole + boiled egg",
            "Dal + rice + stir-fried vegetables + boiled egg",
        ],
        "Evening snack": [
            "Boiled egg + roasted chana",
            "Egg salad sandwich on whole-wheat bread",
            "A handful of almonds + a glass of buttermilk",
            "Sprout chaat with lemon",
            "Vegetable soup + a boiled egg",
            "Fruit chaat with black pepper",
            "Roasted makhana + green tea",
            "Peanut butter toast + a glass of milk",
        ],
        "Dinner": [
            "Roti + dal + seasonal vegetable + curd",
            "Omelette + vegetable soup + one roti",
            "Egg curry + rice + salad",
            "Vegetable khichdi + a boiled egg",
            "Paneer bhurji + multigrain toast",
            "Dal soup + two rotis + fried egg",
            "Stir-fried vegetables + rice + omelette",
            "Mushroom curry + one roti + curd",
        ],
    },
    "pescatarian": {
        "Breakfast": [
            "Fish toast + cumber slices and lime",
            "2 boiled eggs + vegetable upma",
            "Oats porridge with banana and honey",
            "Scrambled eggs with spinach and milk",
            "Vegetable upma + a glass of milk",
            "Paneer bhurji + multigrain toast",
            "Moong dal chilla with mint chutney",
            "Poha with a boiled egg on the side",
        ],
        "Lunch": [
            "Grilled fish + brown rice + greens",
            "Fish curry + one chapati + salad",
            "Roti + dal + vegetable + curd",
            "Brown rice + rajma + cucumber salad",
            "Baked salmon with sweet potato and broccoli",
            "Fish tikka + vegetable pulao",
            "Paneer curry + roti + salad",
            "Vegetable stir-fry + rice + grilled fish",
        ],
        "Evening snack": [
            "Grilled fish fingers + mint chutney",
            "Tuna sandwich on whole-wheat bread",
            "Boiled egg + roasted chana",
            "A handful of almonds + green tea",
            "Fish soup with vegetables",
            "Sprout chaat with lemon",
            "Fruit chaat with black pepper",
            "Roasted makhana + buttermilk",
        ],
        "Dinner": [
            "Steamed fish + stir-fried greens + rice",
            "Fish curry + two rotis + salad",
            "Vegetable khichdi + fish",
            "Grilled fish + mashed potato + peppers",
            "Roti + dal + seasonal vegetable",
            "Fish tikka + cucumber raita + one roti",
            "Mushroom soup + grilled fish + toast",
            "Stir-fried vegetables + rice + fish",
        ],
    },
    "non_veg": {
        "Breakfast": [
            "Chicken sausage + 2 eggs + whole-wheat toast",
            "2 boiled eggs + vegetable upma",
            "Chicken sandwich with lettuce and tomato",
            "Omelette with onion-tomato + a glass of milk",
            "Egg bhurji + one roti",
            "Oats porridge with banana + boiled eggs",
            "Grilled fish + lemon and a slice of bread",
            "Moong dal chilla with boiled egg on the side",
        ],
        "Lunch": [
            "Grilled chicken breast + brown rice + greens",
            "Chicken curry + one chapati + salad",
            "Fish curry + rice + raita",
            "Roti + dal + vegetable + curd",
            "Egg curry + two chapati + salad",
            "Chicken tikka + vegetable pulao",
            "Mutton curry + rice + cucumber salad",
            "Baked salmon + sweet potato + broccoli",
        ],
        "Evening snack": [
            "Chicken seekh kebab + green chutney",
            "Boiled eggs + roasted chana",
            "Tuna sandwich on whole-wheat bread",
            "A handful of almonds + green tea",
            "Chicken soup with vegetables",
            "Fruit chaat with black pepper",
            "Grilled fish fingers + mint chutney",
            "Trail mix with seeds and dried fruit",
        ],
        "Dinner": [
            "Chicken curry + two rotis + salad",
            "Grilled fish + stir-fried greens + rice",
            "Mutton curry + rice + raita",
            "Egg fried rice + vegetable stir-fry",
            "Chicken tikka + cucumber raita + one roti",
            "Butter chicken + one naan + salad",
            "Fish curry + rice + greens",
            "Grilled chicken + mashed potato + peppers",
        ],
    },
}

_GOAL_DIET_TWEAKS = {
    "high_protein": {
        "note": "Protein-forward — aim for ~1.6-2 g per kg bodyweight daily. Keep a protein source in every meal and snack.",
        "cap": "Portions: moderate. Carbs around training sessions, protein everywhere else.",
    },
    "deficit": {
        "note": "Modest calorie deficit — keep protein high (protects muscle), fill up on vegetables and water, watch oils and sugar.",
        "cap": "Portions: on the smaller side. Swap heavy gravies for light sauces, broth soups or grilled versions.",
    },
    "balanced": {
        "note": "Balanced plate — a palm of protein, a fist of carbs, two fists of vegetables, a thumb of fats at each main meal.",
        "cap": "Portions: steady and consistent. Eat slowly; hydrate before meals.",
    },
}

_DIET_DAY_NOTE = {
    "veg": "Meat, fish, eggs and any secret stock or bone-broth are out — check labels for gelatine.",
    "eggetarian": "Eggs are fine (any style); mutton, chicken and fish are out. Protein can come from eggs, dairy, legumes and paneer.",
    "pescatarian": "Fish and eggs are fine; red meat and poultry are out. Oily fish 2-3 times a week is a good goal.",
    "non_veg": "All foods allowed within your rules — still aim for lean choices most of the time.",
}

_FAST_MEALS = [  # lighter, fasting-friendly stand-ins for flagged special days
    "Fruit + nuts (dates, banana, almonds)",
    "Vegetable broth soup",
    "Khichdi (light, easier to digest)",
    "Fruit chaat + green tea",
    "Curd or lassi (if allowed) + dry toast",
    "Boiled vegetables + lemon water",
]


def _diet_meals(diet_type, goal, allergies, day_index, rules_text):
    """Deterministic day-by-day meal chart honoring diet type, goal, and
    allergies; flags days mentioned by the user's free-text diet rules."""
    pools = _MEAL_POOLS.get(diet_type, _MEAL_POOLS["non_veg"])
    banned = set()
    for key in allergies:
        banned.update(ALLERGY_FOODS.get(key, ()))
    banned = {b for b in banned if b}

    def _swap_candidate(name):
        return not any(word in name.lower() for word in banned)

    reached = {slot: 0 for slot in pools}
    slots_out = []
    flags = []
    for slot in ("Breakfast", "Lunch", "Evening snack", "Dinner"):
        pool = pools[slot]
        start = (day_index + reached[slot]) % len(pool)
        picked = None
        for i in range(len(pool)):
            candidate = pool[(start + i) % len(pool)]
            if _swap_candidate(candidate):
                picked = candidate
                reached[slot] = (reached[slot] + 1) % len(pool)
                break
        if not picked:  # everything in this slot is banned — keep it, tag it
            picked = pool[start]
            flags.append(f"{slot} limited by your allergies — swap with any listed alternative.")
        slots_out.append({"slot": slot, "meal": picked})
    return slots_out, banned, flags


def _flag_special_days(rules_text):
    """Weekday names mentioned in free-text diet rules → set of day indices."""
    text = (rules_text or "").lower()
    out = set()
    for i, name in enumerate(WEEKDAYS):
        if name.lower() in text:
            out.add(i)
    if "sat" in text or "sunday" in text or "sun." in text:
        if "saturday" in text or "sat" in text:
            out.add(5)
    return out


def _prefs_list(value):
    """Accept a JSON-encoded string or a plain list and return a list."""
    if isinstance(value, str):
        try:
            return json.loads(value or "[]")
        except ValueError:
            return []
    return list(value or [])


def build_weekly_plan(prefs):
    """Turn saved preferences into a structured Mon-Sun timetable.

    Rest days are the user's chosen weekdays; the remaining days get sessions
    from the goal's split, with user focus muscles injected into rotation.
    """
    goal = prefs.get("goal", "all_round")
    days = int(prefs.get("training_days", 3) or 3)
    rest_idx = [int(x) for x in _prefs_list(prefs.get("rest_days"))]
    focus = [str(k) for k in _prefs_list(prefs.get("focus_muscles"))]
    focus = [k for k in focus if k in EXERCISE_BY_KEY]
    meta = PLAN_GOALS.get(goal, PLAN_GOALS["all_round"])
    pattern = meta["pattern"]
    split = _PLAN_SPLITS.get(goal, _PLAN_SPLITS["all_round"]).get(days)
    if not split:
        split = _PLAN_SPLITS["all_round"].get(days) or _PLAN_SPLITS["all_round"][3]

    training_idx = [i for i in range(7) if i not in set(rest_idx)]
    # keep exactly `days` training days (validated on save, defensive here)
    while len(training_idx) < days and set(rest_idx) < set(range(7)):
        missing = [i for i in range(7) if i not in training_idx and i not in rest_idx]
        if not missing:
            break
        training_idx.append(missing[0])
    training_idx = training_idx[:days]

    day_cards = []
    cursor = 0
    focus_moved = list(focus)
    for i in range(7):
        if i in set(rest_idx):
            rec = _REST_SUGGESTIONS[(i % len(_REST_SUGGESTIONS)) + (pattern in ("split", "shaping"))]
            day_cards.append({
                "name": WEEKDAYS[i], "index": i, "is_rest": True,
                "rec": rec, "sessions": [],
            })
            continue
        template = split[cursor % len(split)]
        cursor += 1
        keys = list(template)
        # inject extra focus muscles across the week (one per training day)
        if focus_moved:
            keys.insert(1, focus_moved.pop(0))
        sessions = []
        for k in keys:
            item = EXERCISE_BY_KEY.get(k)
            if not item:
                continue
            sessions.append({
                "key": k, "name": item["gym_exercise"],
                "home": item["home_alternative"],
                "stat": item["feeds_stat"],
            })
        day_cards.append({
            "name": WEEKDAYS[i], "index": i, "is_rest": False,
            "sessions": sessions, "rec": None,
        })
    return {
        "goal": goal, "goal_label": meta["label"], "goal_icon": meta["icon"],
        "pattern": pattern, "days": day_cards,
    }


def build_diet_chart(prefs, bodyweight_kg=None):
    """Build a 7-day general food chart from dietary preferences, plus a
    daily calorie/macro estimate derived from the user's bodyweight & goal."""
    diet_type = prefs.get("diet_type", "non_veg")
    goal = prefs.get("goal", "all_round")
    allergies = [str(k) for k in _prefs_list(prefs.get("allergies"))]
    allergies = [k for k in allergies if k in ALLERGIES]
    rules = prefs.get("diet_rules", "") or ""
    special = _flag_special_days(rules)
    meta = PLAN_GOALS.get(goal, PLAN_GOALS["all_round"])
    tweak = _GOAL_DIET_TWEAKS.get(meta["diet"], _GOAL_DIET_TWEAKS["balanced"])
    targets = _daily_targets(goal, bodyweight_kg)

    days = []
    for i in range(7):
        slots, banned, slot_flags = _diet_meals(diet_type, goal, allergies, i, rules)
        enriched = []
        for j, s in enumerate(slots):
            frac = _SLOT_SPLIT[j][1]
            enriched.append({**s, "kcal": round(targets["kcal"] * frac)})
        day = {"name": WEEKDAYS[i], "slots": enriched, "flags": list(slot_flags),
               "special": i in special}
        days.append(day)

    excluded = []
    for key in allergies:
        excluded.append(ALLERGIES[key].split(" (")[0])
    return {
        "diet_type": diet_type, "diet_label": DIET_TYPES.get(diet_type, diet_type),
        "days": days, "excluded": excluded, "note": tweak["note"],
        "cap": tweak["cap"], "day_note": _DIET_DAY_NOTE[diet_type],
        "special_days": sorted(special),
        "special_label": ", ".join(WEEKDAYS[i] for i in sorted(special)),
        "targets": targets,
    }


_SLOT_SPLIT = [("Breakfast", 0.25), ("Lunch", 0.30), ("Evening snack", 0.15), ("Dinner", 0.30)]


# Goal-scaled daily targets. Kcal per kg bodyweight, protein per kg bodyweight.
# When no bodyweight is known we fall back to generic adult numbers.
_DAILY_TARGETS = {
    "muscle_building": {"kcal_per_kg": 34, "protein_per_kg": 1.8},
    "fat_loss":        {"kcal_per_kg": 26, "protein_per_kg": 1.7},
    "weight_loss":     {"kcal_per_kg": 26, "protein_per_kg": 1.6},
    "body_shaping":    {"kcal_per_kg": 30, "protein_per_kg": 1.5},
    "all_round":       {"kcal_per_kg": 30, "protein_per_kg": 1.4},
}


def _daily_targets(goal, bodyweight_kg):
    """Daily kcal + macro estimate for the goal, scaled by bodyweight if known."""
    t = _DAILY_TARGETS.get(goal, _DAILY_TARGETS["all_round"])
    if bodyweight_kg and bodyweight_kg > 0:
        kcal = int(round(t["kcal_per_kg"] * bodyweight_kg / 10.0) * 10)
        protein = int(round(t["protein_per_kg"] * bodyweight_kg))
        fat = max(40, int(round(0.8 * bodyweight_kg)))
        carbs = max(0, int(round((kcal - (protein * 4 + fat * 9)) / 4.0)))
        return {"kcal": kcal, "protein": protein, "carbs": carbs, "fat": fat, "uses_bw": True}
    return {"kcal": 2000, "protein": 110, "carbs": 240, "fat": 55, "uses_bw": False}


def today_plan(user_id):
    """Today's plan entry with live completion state (which planned exercises
    are already logged today) — drives the dashboard checklist. None if no plan."""
    prefs = get_plan_prefs(user_id)
    if not prefs:
        return None
    plan = build_weekly_plan(prefs)
    idx = datetime.now().weekday()
    day = next((d for d in plan["days"] if d["index"] == idx), None)
    info = {
        "has_plan": True, "day": day, "weekday": WEEKDAYS[idx],
        "goal_label": plan["goal_label"], "goal_icon": plan["goal_icon"],
    }
    if day["is_rest"]:
        info["total"] = None
        info["done_count"] = None
        return info
    today_iso = datetime.now().date().isoformat()
    with db_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT exercise_key FROM exercise_logs "
            "WHERE user_id = ? AND substr(logged_at, 1, 10) = ?",
            (user_id, today_iso),
        ).fetchall()
    done_keys = {r["exercise_key"] for r in rows}
    for s in day["sessions"]:
        s["done"] = s["key"] in done_keys
    info["total"] = len(day["sessions"])
    info["done_count"] = sum(1 for s in day["sessions"] if s["done"])
    return info


def get_plan_prefs(user_id):
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM plan_prefs WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        return None
    return {k: row[k] for k in row.keys()}


def write_plan_prefs(user_id, prefs):
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO plan_prefs (user_id, goal, training_days, rest_days, focus_muscles, "
            "diet_type, allergies, diet_rules, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET goal = excluded.goal, "
            "training_days = excluded.training_days, rest_days = excluded.rest_days, "
            "focus_muscles = excluded.focus_muscles, diet_type = excluded.diet_type, "
            "allergies = excluded.allergies, diet_rules = excluded.diet_rules, "
            "updated_at = excluded.updated_at",
            (user_id, prefs["goal"], prefs["training_days"], json.dumps(prefs["rest_days"]),
             json.dumps(prefs["focus_muscles"]), prefs["diet_type"],
             json.dumps(prefs["allergies"]), prefs["diet_rules"],
             datetime.now().isoformat()),
        )


def _validate_plan_form(form):
    """Validate the training-plan form; returns clean prefs (raises ValidationError)."""
    def _list(key):
        if hasattr(form, "getlist"):
            return form.getlist(key)
        val = form.get(key, [])
        return val if isinstance(val, list) else ([val] if val else [])

    goal = form.get("goal", "")
    if goal not in PLAN_GOALS:
        raise ValidationError("Pick a valid training goal.")
    try:
        days = int(form.get("training_days") or 0)
    except (TypeError, ValueError):
        raise ValidationError("Pick how many days a week you want to train.")
    if not 1 <= days <= 6:
        raise ValidationError("Training days must be between 1 and 6 (rest is required).")
    rest_raw = _list("rest_days")
    rest = sorted({int(x) for x in rest_raw if str(x).isdigit()} | set())
    if any(x < 0 or x > 6 for x in rest):
        raise ValidationError("Invalid rest day.")
    if len(rest) != 7 - days:
        raise ValidationError(f"Pick exactly {7 - days} rest day(s) to match {days} training days.")
    focus = []
    for k in _list("focus_muscles"):
        if k in EXERCISE_BY_KEY and k not in focus:
            focus.append(k)
    diet_type = form.get("diet_type", "")
    if diet_type not in DIET_TYPES:
        raise ValidationError("Pick a valid diet type.")
    allergies = [k for k in _list("allergies") if k in ALLERGIES]
    rules = (form.get("diet_rules", "") or "").strip()[:500]
    return {
        "goal": goal, "training_days": days, "rest_days": rest,
        "focus_muscles": focus, "diet_type": diet_type,
        "allergies": allergies, "diet_rules": rules,
    }


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------

LOG_SCHEMA = {
    "strength": {"table": "strength_logs", "label": "Strength set", "icon": "🏋️", "cls": "str"},
    "cardio": {"table": "cardio_logs", "label": "Cardio run", "icon": "🏃", "cls": "end"},
    "body": {"table": "body_logs", "label": "Body metrics", "icon": "📏", "cls": "vit"},
    "performance": {"table": "performance_logs", "label": "Field tests", "icon": "🔥", "cls": "pow"},
    "exercise": {"table": "exercise_logs", "label": "Exercise", "icon": "🎯", "cls": "ex"},
}

LIFT_LABELS = {"bench": "Bench Press", "squat": "Squat", "deadlift": "Deadlift"}

# Big-3 gym exercises logged through the general exercise library ALSO write a
# strength_logs row (via source_exercise_log_id), so one entry counts for both
# training credit and the 1RM benchmark. Minutes-based / bodyweight-only logs
# don't qualify.
BIG3_EXERCISE_TO_LIFT = {
    "Barbell Bench Press": "bench",
    "Barbell Squat": "squat",
    "Deadlift": "deadlift",
}


def _log_template(log_type):
    return {"strength": "log_strength.html", "cardio": "log_cardio.html",
            "body": "log_body.html", "performance": "log_performance.html",
            "exercise": "log_exercise.html"}[log_type]


def describe_log(log_type, row):
    """Human-readable (title, detail) for a log row."""
    if log_type == "strength":
        name = LIFT_LABELS.get(row["lift"], row["lift"].title())
        return f"{name} · {row['weight_kg']} kg × {row['reps']} reps"
    if log_type == "cardio":
        return f"{row['distance_km']} km in {row['minutes']} min"
    if log_type == "exercise":
        name = row["exercise_name"]
        if row["minutes"] is not None:
            return f"{name} · {row['minutes']} min"
        parts = [f"{row['sets']} ×"]
        parts.append(f"{row['reps']} reps" if row["reps"] is not None else "rounds")
        if row["weight_kg"] is not None:
            parts.append(f"@ {row['weight_kg']} kg")
        else:
            parts.append("(bodyweight)")
        return f"{name} · {' '.join(parts)}"
    if log_type == "body":
        parts = [f"Waist {row['waist_cm']} cm", f"Neck {row['neck_cm']} cm"]
        if row["hip_cm"] is not None:
            parts.append(f"Hips {row['hip_cm']} cm")
        return " · ".join(parts)
    parts = []
    if row["vertical_jump_cm"] is not None:
        parts.append(f"Vertical jump {row['vertical_jump_cm']} cm")
    if row["sprint_40m_s"] is not None:
        parts.append(f"Sprint {row['sprint_40m_s']} s")
    if row["sit_and_reach_cm"] is not None:
        parts.append(f"Sit & reach {row['sit_and_reach_cm']} cm")
    return " · ".join(parts)


def get_all_logs(user_id, limit=None, offset=0):
    with db_conn() as conn:
        logs = []
        for t, meta in LOG_SCHEMA.items():
            rows = conn.execute(
                f"SELECT * FROM {meta['table']} WHERE user_id = ? ORDER BY logged_at DESC, id DESC",
                (user_id,),
            ).fetchall()
            for r in rows:
                cls = meta["cls"]
                if t == "exercise":
                    cls = (r["feeds_stat"] or "ex").lower()
                logs.append({
                    "type": t, "id": r["id"], "row": r,
                    "icon": meta["icon"], "cls": cls,
                    "summary": describe_log(t, r),
                    "logged_at": r["logged_at"],
                })
    logs.sort(key=lambda x: (x["logged_at"], x["id"]), reverse=True)
    total = len(logs)
    if limit is not None:
        logs = logs[offset:offset + limit]
    return logs, total


def update_log(log_type, log_id, user_id, form):
    meta = LOG_SCHEMA.get(log_type)
    if not meta:
        return False
    with db_conn() as conn:
        row = conn.execute(
            f"SELECT id FROM {meta['table']} WHERE id = ? AND user_id = ?", (log_id, user_id)
        ).fetchone()
        if not row:
            return False
        if log_type == "strength":
            conn.execute(
                "UPDATE strength_logs SET lift = ?, weight_kg = ?, reps = ?, logged_at = ? WHERE id = ? AND user_id = ?",
                (form["lift"], float(form["weight_kg"]), int(form["reps"]), form["logged_at"], log_id, user_id),
            )
        elif log_type == "cardio":
            conn.execute(
                "UPDATE cardio_logs SET distance_km = ?, minutes = ?, logged_at = ? WHERE id = ? AND user_id = ?",
                (float(form["distance_km"]), float(form["minutes"]), form["logged_at"], log_id, user_id),
            )
        elif log_type == "body":
            conn.execute(
                "UPDATE body_logs SET waist_cm = ?, neck_cm = ?, height_cm = ?, hip_cm = ?, logged_at = ? WHERE id = ? AND user_id = ?",
                (float(form["waist_cm"]), float(form["neck_cm"]), float(form["height_cm"]),
                 form.get("hip_cm"), form["logged_at"], log_id, user_id),
            )
        elif log_type == "exercise":
            conn.execute(
                "UPDATE exercise_logs SET exercise_key = ?, exercise_name = ?, feeds_stat = ?, "
                "sets = ?, reps = ?, weight_kg = ?, minutes = ?, logged_at = ? WHERE id = ? AND user_id = ?",
                (form["exercise_key"], form["exercise_name"], form["feeds_stat"],
                 form["sets"], form.get("reps"), form.get("weight_kg"), form.get("minutes"),
                 form["logged_at"], log_id, user_id),
            )
            # Keep the linked strength_logs row in sync (dual-path big-3).
            dual_lift = BIG3_EXERCISE_TO_LIFT.get(form["exercise_name"])
            linked = conn.execute(
                "SELECT id FROM strength_logs WHERE source_exercise_log_id = ? AND user_id = ?",
                (log_id, user_id),
            ).fetchone()
            qualifies = (dual_lift and form.get("weight_kg") is not None
                         and form.get("reps") is not None and form.get("minutes") is None)
            if qualifies and linked:
                conn.execute(
                    "UPDATE strength_logs SET weight_kg = ?, reps = ?, logged_at = ? WHERE id = ?",
                    (form["weight_kg"], form["reps"], form["logged_at"], linked["id"]),
                )
            elif qualifies and not linked:
                conn.execute(
                    "INSERT INTO strength_logs (user_id, lift, weight_kg, reps, logged_at, "
                    "source_exercise_log_id) VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, dual_lift, form["weight_kg"], form["reps"],
                     form["logged_at"], log_id),
                )
            elif linked:
                conn.execute("DELETE FROM strength_logs WHERE id = ?", (linked["id"],))
        else:
            fields = {}
            for col in ("vertical_jump_cm", "sprint_40m_s", "sit_and_reach_cm"):
                raw = form.get(col, "")
                fields[col] = float(raw) if raw else None
            if not any(v is not None for v in fields.values()):
                return False
            conn.execute(
                "UPDATE performance_logs SET vertical_jump_cm = ?, sprint_40m_s = ?, sit_and_reach_cm = ?, logged_at = ? "
                "WHERE id = ? AND user_id = ?",
                (fields["vertical_jump_cm"], fields["sprint_40m_s"], fields["sit_and_reach_cm"],
                 form["logged_at"], log_id, user_id),
            )
    return True


def delete_log(log_type, log_id, user_id):
    meta = LOG_SCHEMA.get(log_type)
    if not meta:
        return False
    with db_conn() as conn:
        if log_type == "exercise":
            conn.execute(
                "DELETE FROM strength_logs WHERE source_exercise_log_id = ? AND user_id = ?",
                (log_id, user_id),
            )
        cur = conn.execute(
            f"DELETE FROM {meta['table']} WHERE id = ? AND user_id = ?", (log_id, user_id)
        )
        return cur.rowcount > 0


def delete_account(user_id):
    """Remove a user and all their data (GDPR / DPDP friendly)."""
    with db_conn() as conn:
        for table in ["strength_logs", "cardio_logs", "body_logs", "performance_logs",
                      "exercise_logs", "bodyweight_logs", "plan_prefs", "profile", "users"]:
            conn.execute(f"DELETE FROM {table} WHERE {'id' if table == 'users' else 'user_id'} = ?", (user_id,))


# ---------------------------------------------------------------------------
# AUTH ROUTES
# ---------------------------------------------------------------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        if _login_limited("signup:" + request.remote_addr):
            return render_template("signup.html", error="Too many attempts. Please wait a few minutes.")
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("password_confirm", "")
        gender = request.form.get("gender", "male")
        if gender not in GENDERS:
            gender = "male"
        if not USERNAME_RE.match(username):
            return render_template("signup.html", error="Username must be 3-24 letters, numbers, or underscores.")
        if len(password) < 8 or len(password) > 128:
            return render_template("signup.html", error="Password must be at least 8 characters.")
        if password != confirm:
            return render_template("signup.html", error="Passwords do not match.")
        ok = create_user(username, password, gender)
        if not ok:
            return render_template("signup.html", error="That username is taken.")
        user_id = verify_user(username, password)
        session.clear()
        session["user_id"] = user_id
        session["username"] = username
        flash("Account created. Welcome to IRONBOUND ⚔️", "success")
        return redirect(url_for("dashboard"))
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if _login_limited("login:" + request.remote_addr + ":" + username.lower()):
            return render_template("login.html", error="Too many attempts. Please wait a few minutes.")
        user_id = verify_user(username, password)
        if user_id is None:
            _record_failure("login:" + request.remote_addr + ":" + username.lower())
            return render_template("login.html", error="Invalid username or password.")
        _login_reset("login:" + request.remote_addr + ":" + username.lower())
        session.clear()
        session["user_id"] = user_id
        session["username"] = username
        flash("Welcome back, " + username + " ⚔️", "success")
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You've been logged out.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# MAIN DASHBOARD
# ---------------------------------------------------------------------------

@app.route("/", methods=["GET", "POST"])
def dashboard():
    uid = current_user_id()
    if uid is None:
        if request.method == "POST":
            return redirect(url_for("login"))
        return render_template("landing.html")
    if request.method == "POST":
        try:
            bw = form_float("bodyweight_kg", "Bodyweight", minv=20, maxv=500)
            logged_at = form_logged_at()
        except ValidationError as e:
            flash(str(e), "error")
            return redirect(url_for("dashboard"))
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO profile (user_id, bodyweight_kg, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET bodyweight_kg = ?, updated_at = ?",
                (uid, bw, datetime.now().isoformat(), bw, datetime.now().isoformat()),
            )
            conn.execute(
                "INSERT INTO bodyweight_logs (user_id, bodyweight_kg, logged_at) VALUES (?, ?, ?)",
                (uid, bw, logged_at),
            )
        flash("Bodyweight saved ✓", "success")
        return redirect(url_for("dashboard"))

    with db_conn() as conn:
        user_row = conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    log_counts = count_logs(uid)

    stats, details, ranks = compute_stats(uid)
    bw = get_bodyweight(uid)
    history = compute_history(uid)
    trends = compute_trends(history)
    ov = overall_score(stats)
    lvl, xp = level_from_score(ov)
    overall_rank = score_to_rank(ov)

    scored = sorted(
        ((k, v) for k, v in stats.items() if v is not None),
        key=lambda kv: kv[1], reverse=True,
    )
    kpi_map = {
        "bench": details["bench"]["1rm_kg"] if details.get("bench") else None,
        "squat": details["squat"]["1rm_kg"] if details.get("squat") else None,
        "deadlift": details["deadlift"]["1rm_kg"] if details.get("deadlift") else None,
        "vo2max": details["vo2max"]["value"] if details.get("vo2max") else None,
        "bodyweight": bw,
    }

    _badges, _ctx = check_badges(uid)
    badge_earned = len([b for b in _badges if b["earned"]])

    streak = compute_streak(uid)
    streak_danger = streak["last_log"] is not None and streak["current"] == 0
    weekly = weekly_volume(uid)

    greeting_text = greeting()
    if streak["current"] >= 2:
        greeting_text += f" · Day {streak['current']} streak 🔥"
    elif streak["current"] == 1:
        greeting_text += " · Streak started 🔥"

    # level / rank up feedback (session-persisted so it fires once)
    prev_level = session.get("ib_level")
    prev_rank = session.get("ib_rank")
    _RANK_ORDER = ["E", "D", "C", "B", "A", "S"]
    level_up = prev_level is not None and lvl > prev_level
    rank_up = (prev_rank is not None and prev_rank in _RANK_ORDER and overall_rank in _RANK_ORDER
               and _RANK_ORDER.index(overall_rank) > _RANK_ORDER.index(prev_rank))
    session["ib_level"] = lvl
    session["ib_rank"] = overall_rank

    has_data = any(v is not None for v in stats.values()) or bool(bw)

    # "train next" — the single lowest stat and what to log for it
    next_up = None
    if has_data:
        weakest = min(((s, v) for s, v in stats.items() if v is not None),
                      key=lambda kv: kv[1], default=None)
        if weakest is not None:
            _NEXT_ACTIONS = {
                "STR": ("Log a strength set", "/log/strength"),
                "END": ("Log a run", "/log/cardio"),
                "VIT": ("Log body metrics", "/log/body"),
                "POW": ("Log a vertical jump", "/log/performance"),
                "AGI": ("Log a 40 m sprint", "/log/performance"),
                "FLX": ("Log a sit & reach", "/log/performance"),
            }
            stat, score = weakest
            label, href = _NEXT_ACTIONS[stat]
            next_up = {"stat": stat, "label": STAT_META[stat].get("label", stat),
                       "score": score, "action": label, "href": href}

    return render_template(
        "dashboard.html",
        stats=stats, details=details, ranks=ranks, stat_meta=STAT_META,
        bodyweight=bw, history=history, username=session.get("username"),
        user=user_row, log_counts=log_counts, trends=trends,
        overall_rank=overall_rank, level=lvl, xp=xp, overall=ov,
        top_rings=scored[:3], kpis=kpi_map,
        greeting_text=greeting_text,
        has_data=has_data,
        now_local=default_logged_at(),
        streak=streak,
        streak_danger=streak_danger,
        weekly=weekly,
        badge_count=badge_earned,
        badge_total=len(_badges),
        level_up=level_up, rank_up=rank_up,
        next_up=next_up,
        rank_prog={k: rank_progress(v) for k, v in stats.items()},
        heatmap=training_heatmap(uid) if has_data else [],
        plan_today=today_plan(uid),
        chart_data={
            "stats": stats,
            "history": history,
            "counts": log_counts,
            "kpis": kpi_map,
            "bodyweight": bodyweight_history(uid),
        },
    )


@app.route("/achievements")
@login_required
def achievements():
    uid = current_user_id()
    badges, ctx = check_badges(uid)
    earned = [b for b in badges if b["earned"]]
    locked = [b for b in badges if not b["earned"]]
    return render_template(
        "achievements.html",
        username=session.get("username"),
        streak=compute_streak(uid),
        weekly=weekly_volume(uid),
        volume_series=volume_series(uid),
        personal_records=personal_records(uid),
        badges=earned + locked,
        earned_count=len(earned),
        badge_total=len(badges),
        ctx=ctx,
    )


@app.route("/analytics")
@login_required
def analytics():
    uid = current_user_id()
    stats, details, ranks = compute_stats(uid)
    history = compute_history(uid)
    log_counts = count_logs(uid)
    return render_template(
        "analytics.html",
        username=session.get("username"),
        log_counts=log_counts,
        has_data=(any(v is not None for v in stats.values())
                  or sum(log_counts.values()) > 0
                  or len(bodyweight_history(uid)) > 0),
        chart_data={
            "stats": stats,
            "history": history,
            "counts": log_counts,
            "bodyweight": bodyweight_history(uid),
            "bodyfat": bodyfat_history(uid),
        },
    )


@app.route("/standards")
@login_required
def standards_page():
    uid = current_user_id()
    you = current_raw_metrics(uid)
    target_tier = request.args.get("tier", "enthusiast")
    if target_tier not in TIER_SCORES:
        target_tier = "enthusiast"
    gaps = compare_to_tier(uid, target_tier)
    return render_template(
        "standards.html", male=BENCHMARKS_MALE, female=BENCHMARKS_FEMALE,
        you=you, gaps=gaps, target_tier=target_tier, tier_labels=TIER_LABELS,
    )


@app.route("/exercises")
@login_required
def exercises_page():
    library = [{**item, "map": MAP_BY_MUSCLE.get(item["muscle"], [])}
               for item in EXERCISE_LIBRARY]
    return render_template("exercises.html", library=library)


@app.route("/plan", methods=["GET", "POST"])
@login_required
def plan():
    """Training plan + diet: save goals/preferences and view the generated
    weekly timetable and day-by-day food chart."""
    uid = current_user_id()
    if request.method == "POST":
        try:
            prefs = _validate_plan_form(request.form)
        except ValidationError as e:
            flash(str(e), "error")
            return redirect(url_for("plan"))
        write_plan_prefs(uid, prefs)
        flash("Training plan saved — here's your week ✨", "success")
        return redirect(url_for("plan"))

    prefs = get_plan_prefs(uid)
    focus_groups = {}
    for it in EXERCISE_LIBRARY:
        focus_groups.setdefault(it["feeds_stat"], []).append(
            {"key": it["muscle"], "name": it["gym_exercise"]}
        )
    if prefs is None:
        return render_template(
            "plan.html", prefs=None, plan=None, diet=None,
            goals=PLAN_GOALS, diet_types=DIET_TYPES, allergies=ALLERGIES,
            weeks=PLAN_MUSCLES, weekdays=WEEKDAYS, focus_groups=focus_groups,
            bodyweight=get_bodyweight(uid),
        )
    prefs = dict(prefs)
    prefs["rest_days"] = json.loads(prefs.get("rest_days") or "[]")
    prefs["focus_muscles"] = json.loads(prefs.get("focus_muscles") or "[]")
    prefs["allergies"] = json.loads(prefs.get("allergies") or "[]")
    prefs["rest_summary"] = ", ".join(WEEKDAYS[i] for i in prefs["rest_days"])
    return render_template(
        "plan.html", prefs=prefs,
        plan=build_weekly_plan(prefs), diet=build_diet_chart(prefs, get_bodyweight(uid)),
        goals=PLAN_GOALS, diet_types=DIET_TYPES, allergies=ALLERGIES,
        weeks=PLAN_MUSCLES, weekdays=WEEKDAYS, focus_groups=focus_groups,
        bodyweight=get_bodyweight(uid),
    )


@app.route("/onboarding", methods=["GET", "POST"])
@login_required
def onboarding():
    """First-run wizard: pick a goal, schedule, diet and bodyweight in a few
    taps, then an auto-generated weekly plan + food chart are saved."""
    uid = current_user_id()
    if request.method == "POST":
        try:
            prefs = _validate_plan_form(request.form)
            bw = form_float("bodyweight_kg", "Bodyweight", minv=20, maxv=500, required=False)
        except ValidationError as e:
            flash(str(e), "error")
            return redirect(url_for("onboarding"))
        write_plan_prefs(uid, prefs)
        if bw is not None:
            with db_conn() as conn:
                conn.execute(
                    "INSERT INTO profile (user_id, bodyweight_kg, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET bodyweight_kg = ?, updated_at = ?",
                    (uid, bw, datetime.now().isoformat(), bw, datetime.now().isoformat()),
                )
                conn.execute(
                    "INSERT INTO bodyweight_logs (user_id, bodyweight_kg, logged_at) VALUES (?, ?, ?)",
                    (uid, bw, default_logged_at()),
                )
        flash("Welcome, champion — your training week is ready 🎯", "success")
        return redirect(url_for("dashboard"))

    prefs = get_plan_prefs(uid)
    if prefs is None:
        prefs = {"goal": "", "training_days": 3, "rest_days": [0, 5, 6],
                 "diet_type": "", "allergies": [], "diet_rules": ""}
    else:
        prefs = dict(prefs)
        prefs["rest_days"] = json.loads(prefs.get("rest_days") or "[]")
        prefs["allergies"] = json.loads(prefs.get("allergies") or "[]")
    default_rest = {
        2: [0, 1, 3, 5, 6], 3: [0, 3, 5, 6], 4: [0, 3, 6], 5: [0, 6], 6: [0],
    }
    return render_template(
        "onboarding.html", goals=PLAN_GOALS, diet_types=DIET_TYPES,
        allergies=ALLERGIES, weekdays=WEEKDAYS, prefs=prefs,
        bodyweight=get_bodyweight(uid), default_rest=default_rest,
    )


# ---------------------------------------------------------------------------
# SETTINGS + ACCOUNT
# ---------------------------------------------------------------------------

@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings():
    uid = current_user_id()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "password":
            old = request.form.get("old_password", "")
            new = request.form.get("new_password", "")
            if len(new) < 8 or len(new) > 128:
                flash("New password must be at least 8 characters.", "error")
            elif verify_user(session.get("username", ""), old) is None:
                flash("Current password is incorrect.", "error")
            else:
                with db_conn() as conn:
                    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                                 (generate_password_hash(new), uid))
                flash("Password updated ✓", "success")
        elif action == "gender":
            gender = request.form.get("gender")
            if gender in GENDERS:
                with db_conn() as conn:
                    conn.execute("UPDATE users SET gender = ? WHERE id = ?", (gender, uid))
                flash("Benchmark table updated ✓", "success")
        elif action == "delete":
            delete_account(uid)
            session.clear()
            flash("Your account and all data have been deleted.", "info")
            return redirect(url_for("login"))
        return redirect(url_for("settings"))

    with db_conn() as conn:
        row = conn.execute("SELECT username, gender, created_at FROM users WHERE id = ?", (uid,)).fetchone()
        bw = conn.execute("SELECT bodyweight_kg FROM profile WHERE user_id = ?", (uid,)).fetchone()
    return render_template(
        "settings.html",
        username=row["username"], gender=row["gender"],
        created_at=row["created_at"], bodyweight=bw["bodyweight_kg"] if bw else None,
        genders=GENDERS,
    )


# ---------------------------------------------------------------------------
# LOGGING ROUTES
# ---------------------------------------------------------------------------

def log_header(icon, title, stat, blurb):
    """Context for the shared log-page header card."""
    stats, _, _ = compute_stats(current_user_id())
    return {"icon": icon, "title": title, "stat": stat,
            "blurb": blurb, "stat_score": stats.get(stat)}


@app.route("/log")
@login_required
def log_hub():
    uid = current_user_id()
    stats, _, _ = compute_stats(uid)
    recent, _ = get_all_logs(uid, limit=4)
    return render_template(
        "log_hub.html",
        bodyweight=get_bodyweight(uid),
        stats=stats,
        recent=recent,
        default_logged_at=default_logged_at(),
    )


@app.route("/logs")
@login_required
def my_logs():
    uid = current_user_id()
    page = request.args.get("page", 1, type=int)
    if page < 1:
        page = 1
    per_page = 50
    logs, total = get_all_logs(uid, limit=per_page, offset=(page - 1) * per_page)
    return render_template(
        "logs.html", logs=logs, page=page, per_page=per_page, total=total,
    )


@app.route("/logs/<log_type>/<int:log_id>/edit", methods=["GET", "POST"])
@login_required
def edit_log(log_type, log_id):
    if log_type not in LOG_SCHEMA:
        return redirect(url_for("my_logs"))
    uid = current_user_id()
    ex_lib = _exercise_library_data() if log_type == "exercise" else None
    if request.method == "POST":
        try:
            form = _validate_log_form(log_type, editing=True)
            if update_log(log_type, log_id, uid, form):
                flash("Entry updated ✓", "success")
                return redirect(url_for("my_logs"))
        except ValidationError as e:
            return render_template(
                _log_template(log_type), log=None, editing=True, error=str(e),
                bodyweight=get_bodyweight(uid), header=log_header(*_LOG_HEADERS[log_type]),
                default_logged_at=default_logged_at(), exercise_library=ex_lib,
            ), 400
        return render_template(
            _log_template(log_type), log=None, editing=True,
            error="That entry no longer exists.",
            bodyweight=get_bodyweight(uid), header=log_header(*_LOG_HEADERS[log_type]),
            default_logged_at=default_logged_at(), exercise_library=ex_lib,
        )
    with db_conn() as conn:
        row = conn.execute(
            f"SELECT * FROM {LOG_SCHEMA[log_type]['table']} WHERE id = ? AND user_id = ?",
            (log_id, uid),
        ).fetchone()
    if row is None:
        return redirect(url_for("my_logs"))
    return render_template(
        _log_template(log_type), log=row, editing=True,
        bodyweight=get_bodyweight(uid), header=log_header(*_LOG_HEADERS[log_type]),
        default_logged_at=(row["logged_at"][:16] if row["logged_at"] else default_logged_at()),
        exercise_library=ex_lib,
    )


@app.route("/logs/<log_type>/<int:log_id>/delete", methods=["POST"])
@login_required
def delete_log_route(log_type, log_id):
    if delete_log(log_type, log_id, current_user_id()):
        flash("Entry deleted.", "info")
    return redirect(url_for("my_logs"))


def _validate_log_form(log_type, editing=False, data=None):
    """Validate a log form; returns a dict of clean values (raises ValidationError).

    Reads from request.form by default, or from `data` (a plain dict) when given —
    used by the offline sync endpoint to validate queued payloads."""
    logged_at = form_logged_at(data=data)
    if log_type == "strength":
        lift = str((request.form if data is None else data).get("lift", "") or "")
        if lift not in LIFT_LABELS:
            raise ValidationError("Pick a valid lift.")
        weight = form_float("weight_kg", "Weight", minv=1, maxv=1000, data=data)
        reps = form_int("reps", "Reps", minv=1, maxv=1000, data=data)
        return {"lift": lift, "weight_kg": weight, "reps": reps, "logged_at": logged_at}
    if log_type == "exercise":
        src = request.form if data is None else data
        key = str(src.get("exercise_key", "") or "")
        item = exercise_by_key(key)
        if not item:
            raise ValidationError("Pick an exercise from the library.")
        reps = form_int("reps", "Reps", minv=1, maxv=1000, required=False, data=data)
        weight = form_float("weight_kg", "Weight", minv=1, maxv=1000, required=False, data=data)
        minutes = form_float("minutes", "Duration", minv=0.5, maxv=1440, required=False, data=data)
        sets = form_int("sets", "Sets", minv=1, maxv=50, required=False, data=data)
        if sets is None:
            sets = 1
        if minutes is not None and (reps is not None or weight is not None):
            raise ValidationError("Use either sets × reps × weight, OR a duration in minutes — not both.")
        if minutes is None and reps is None:
            raise ValidationError("Enter reps (and optional weight) or a duration in minutes.")
        if minutes is not None:
            sets = 1
        return {"exercise_key": key, "exercise_name": item["gym_exercise"],
                "feeds_stat": item["feeds_stat"], "sets": sets, "reps": reps,
                "weight_kg": weight, "minutes": minutes, "logged_at": logged_at}
    if log_type == "cardio":
        distance = form_float("distance_km", "Distance", minv=0.05, maxv=200, data=data)
        minutes = form_float("minutes", "Time", minv=0.5, maxv=1440, data=data)
        return {"distance_km": distance, "minutes": minutes, "logged_at": logged_at}
    if log_type == "body":
        waist = form_float("waist_cm", "Waist", minv=30, maxv=300, data=data)
        neck = form_float("neck_cm", "Neck", minv=20, maxv=200, data=data)
        height = form_float("height_cm", "Height", minv=50, maxv=250, data=data)
        if waist <= neck:
            raise ValidationError("Waist must be larger than neck.")
        hip_raw = str((request.form if data is None else data).get("hip_cm", "") or "").strip()
        hip = None
        if hip_raw:
            hip = form_float("hip_cm", "Hips", minv=30, maxv=300, data=data)
        return {"waist_cm": waist, "neck_cm": neck, "height_cm": height,
                "hip_cm": hip, "logged_at": logged_at}
    # performance
    fields = {}
    for col, f in [("vertical_jump_cm", "vertical_jump"), ("sprint_40m_s", "sprint"),
                   ("sit_and_reach_cm", "sit_and_reach")]:
        raw = str((request.form if data is None else data).get(f, "") or "").strip()
        if raw:
            fields[col] = form_float(f, f.replace("_", " ").title(), data=data)
    if not fields:
        raise ValidationError("Enter at least one field-test result.")
    return {**fields, "logged_at": logged_at}


def _exercise_library_data():
    """Exercise library grouped by the stat it feeds, for the log form dropdown."""
    groups = {s: [] for s in STATS}
    for item in EXERCISE_LIBRARY:
        groups[item["feeds_stat"]].append({
            "key": item["muscle"], "name": item["gym_exercise"],
            "home": item["home_alternative"], "times": item["muscle"] in EXERCISE_MINUTES,
        })
    return [g for g in groups.values() if g]


def _log_render(log_type, error=None, **extra):
    return render_template(
        _log_template(log_type), error=error,
        bodyweight=get_bodyweight(current_user_id()),
        header=log_header(*_LOG_HEADERS[log_type]),
        default_logged_at=default_logged_at(),
        **extra,
    )


def _insert_log(log_type, form, uid, client_id=None):
    """Insert one validated log row (shared by the web routes and the offline
    sync endpoint). Returns a human-readable success message. `client_id` makes
    replayed offline entries idempotent: a repeat with the same id is a no-op."""
    table = LOG_SCHEMA[log_type]["table"]
    if client_id:
        with db_conn() as conn:
            dup = conn.execute(
                f"SELECT 1 FROM {table} WHERE user_id = ? AND client_id = ?",
                (uid, client_id),
            ).fetchone()
        if dup:
            return "Already synced ✔️"
    if log_type == "strength":
        best_before = best_1rm(uid, form["lift"])
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO strength_logs (user_id, lift, weight_kg, reps, logged_at, client_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, form["lift"], form["weight_kg"], form["reps"], form["logged_at"], client_id),
            )
        new_est = round(epley_1rm(form["weight_kg"], form["reps"]), 1)
        if best_before is None or new_est > best_before:
            return f"New {LIFT_LABELS[form['lift']]} PR — {new_est} kg 🎉"
        return "Strength set logged 💪"
    if log_type == "cardio":
        best_before = best_vo2max(uid)
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO cardio_logs (user_id, distance_km, minutes, logged_at, client_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (uid, form["distance_km"], form["minutes"], form["logged_at"], client_id),
            )
        new_vo2 = estimate_vo2max_cooper(form["distance_km"] * (12.0 / form["minutes"]))
        if best_before is None or new_vo2 > best_before:
            return f"New VO₂max record — {new_vo2} ml/kg/min 🏃"
        return "Run logged 🏃"
    if log_type == "body":
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO body_logs (user_id, waist_cm, neck_cm, height_cm, hip_cm, logged_at, client_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, form["waist_cm"], form["neck_cm"], form["height_cm"],
                 form["hip_cm"], form["logged_at"], client_id),
            )
        return "Body metrics saved 📏"
    if log_type == "performance":
        new_prs = []
        if form.get("vertical_jump_cm"):
            prev = best_vertical_jump(uid)
            if prev is None or form["vertical_jump_cm"] > prev:
                new_prs.append(f"vertical jump {form['vertical_jump_cm']} cm")
        if form.get("sprint_40m_s"):
            prev = best_sprint_40m(uid)
            if prev is None or form["sprint_40m_s"] < prev:
                new_prs.append(f"sprint {form['sprint_40m_s']} s")
        if form.get("sit_and_reach_cm"):
            prev = best_sit_and_reach(uid)
            if prev is None or form["sit_and_reach_cm"] > prev:
                new_prs.append(f"sit & reach {form['sit_and_reach_cm']} cm")
        with db_conn() as conn:
            conn.execute(
                "INSERT INTO performance_logs "
                "(user_id, vertical_jump_cm, sprint_40m_s, sit_and_reach_cm, logged_at, client_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, form.get("vertical_jump_cm"),
                 form.get("sprint_40m_s"), form.get("sit_and_reach_cm"), form["logged_at"], client_id),
            )
        if new_prs:
            return "New PR" + ("s" if len(new_prs) > 1 else "") + ": " + ", ".join(new_prs) + " 🔥"
        return "Field test logged 🔥"
    # exercise (with big-3 dual-path into strength_logs)
    stats_before, _, _ = compute_stats(uid)
    dual_lift = BIG3_EXERCISE_TO_LIFT.get(form["exercise_name"])
    with db_conn() as conn:
        cur = conn.execute(
            "INSERT INTO exercise_logs (user_id, exercise_key, exercise_name, feeds_stat, "
            "sets, reps, weight_kg, minutes, logged_at, client_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, form["exercise_key"], form["exercise_name"], form["feeds_stat"],
             form["sets"], form.get("reps"), form.get("weight_kg"), form.get("minutes"),
             form["logged_at"], client_id),
        )
        # Big-3 with weight × reps also counts toward the 1RM benchmark.
        if (dual_lift and form.get("weight_kg") is not None
                and form.get("reps") is not None and form.get("minutes") is None):
            conn.execute(
                "INSERT INTO strength_logs (user_id, lift, weight_kg, reps, logged_at, "
                "source_exercise_log_id, client_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, dual_lift, form["weight_kg"], form["reps"], form["logged_at"],
                 cur.lastrowid, (client_id + "-s") if client_id else None),
            )
    _clear_stats_cache()
    stats_after, _, _ = compute_stats(uid)
    stat = form["feeds_stat"]
    delta = 0.0
    if stats_after.get(stat) is not None and stats_before.get(stat) is not None:
        delta = round(stats_after[stat] - stats_before[stat], 1)
    elif stats_after.get(stat) is not None:
        delta = round(stats_after[stat], 1)
    extra = f" and {dual_lift} 1RM" if dual_lift else ""
    if delta > 0:
        return f"{form['exercise_name']} logged — {STAT_META[stat]['label']} credit +{delta}{extra} 🎯"
    return f"{form['exercise_name']} logged{extra} 🎯"


@app.route("/log/strength", methods=["GET", "POST"])
@login_required
def log_strength():
    if request.method == "POST":
        try:
            form = _validate_log_form("strength")
            message = _insert_log("strength", form, current_user_id())
        except ValidationError as e:
            return _log_render("strength", error=str(e)), 400
        flash(message, "success")
        return redirect(url_for("dashboard"))
    return _log_render("strength")


@app.route("/log/cardio", methods=["GET", "POST"])
@login_required
def log_cardio():
    if request.method == "POST":
        try:
            form = _validate_log_form("cardio")
            message = _insert_log("cardio", form, current_user_id())
        except ValidationError as e:
            return _log_render("cardio", error=str(e)), 400
        flash(message, "success")
        return redirect(url_for("dashboard"))
    return _log_render("cardio")


@app.route("/log/body", methods=["GET", "POST"])
@login_required
def log_body():
    if request.method == "POST":
        try:
            form = _validate_log_form("body")
            message = _insert_log("body", form, current_user_id())
        except ValidationError as e:
            return _log_render("body", error=str(e)), 400
        flash(message, "success")
        return redirect(url_for("dashboard"))
    return _log_render("body")


@app.route("/log/performance", methods=["GET", "POST"])
@login_required
def log_performance():
    if request.method == "POST":
        try:
            form = _validate_log_form("performance")
            message = _insert_log("performance", form, current_user_id())
        except ValidationError as e:
            return _log_render("performance", error=str(e)), 400
        flash(message, "success")
        return redirect(url_for("dashboard"))
    return _log_render("performance")


_LOG_HEADERS = {
    "strength": ("🏋️", "Strength", "STR",
                 "Bench, squat, and deadlift sets — your best estimated 1RM per lift feeds STR."),
    "cardio": ("🏃", "Endurance", "END",
               "Any run is normalized to a 12-minute Cooper-test distance to estimate VO₂max."),
    "body": ("📏", "Vitality", "VIT",
             "Waist, neck, and height estimate body-fat % — lower drives VIT."),
    "performance": ("🔥", "Field tests", "POW",
                    "Vertical jump → POW, 40 m sprint → AGI, sit & reach → FLX."),
    "exercise": ("🎯", "Exercise", "STR",
                 "Log anything else from the training library — it counts toward this week's training credit for its stat."),
}


@app.route("/log/exercise", methods=["GET", "POST"])
@login_required
def log_exercise():
    uid = current_user_id()
    if request.method == "POST":
        try:
            form = _validate_log_form("exercise")
            message = _insert_log("exercise", form, uid)
        except ValidationError as e:
            return _log_render("exercise", error=str(e),
                               exercise_library=_exercise_library_data()), 400
        flash(message, "success")
        return redirect(url_for("dashboard"))
    selected = request.args.get("exercise", "")
    return _log_render("exercise", exercise_library=_exercise_library_data(),
                       selected=selected if exercise_by_key(selected) else "")


# ---------------------------------------------------------------------------
# DATA EXPORT / IMPORT
# ---------------------------------------------------------------------------

def _sanitize_rows(rows):
    out = []
    for r in rows:
        d = {k: v for k, v in dict(r).items() if k != "user_id"}
        out.append(d)
    return out


@app.route("/export")
@login_required
def export_data():
    """Download every log + profile value for the current user as JSON (backup)."""
    uid = current_user_id()
    with db_conn() as conn:
        def dump(table):
            return _sanitize_rows(conn.execute(f"SELECT * FROM {table} WHERE user_id = ?", (uid,)).fetchall())

        payload = {
            "exported_at": datetime.now().isoformat(),
            "username": session.get("username"),
            "schema_version": 3,
            "bodyweight_kg": get_bodyweight(uid),
            "bodyweight_logs": dump("bodyweight_logs"),
            "strength_logs": dump("strength_logs"),
            "cardio_logs": dump("cardio_logs"),
            "body_logs": dump("body_logs"),
            "performance_logs": dump("performance_logs"),
            "exercise_logs": dump("exercise_logs"),
            "plan_prefs": _sanitize_rows(conn.execute("SELECT * FROM plan_prefs WHERE user_id = ?", (uid,)).fetchall()),
        }
    filename = f"ironbound-backup-{datetime.now():%Y%m%d-%H%M}.json"
    return Response(
        json.dumps(payload, indent=2),
        mimetype="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/export.csv")
@login_required
def export_csv():
    """Download every log as a spreadsheet-friendly CSV (one row per entry)."""
    uid = current_user_id()
    out = StringIO()
    writer = csv.writer(out)
    writer.writerow(["type", "metric", "metric2", "metric3", "metric4", "logged_at"])
    with db_conn() as conn:
        for r in conn.execute(
            "SELECT lift, weight_kg, reps, logged_at FROM strength_logs WHERE user_id = ?", (uid,)
        ):
            writer.writerow(["strength", r["lift"], r["weight_kg"], r["reps"], "", r["logged_at"]])
        for r in conn.execute(
            "SELECT distance_km, minutes, logged_at FROM cardio_logs WHERE user_id = ?", (uid,)
        ):
            writer.writerow(["cardio", r["distance_km"], r["minutes"], "", "", r["logged_at"]])
        for r in conn.execute(
            "SELECT waist_cm, neck_cm, height_cm, hip_cm, logged_at FROM body_logs WHERE user_id = ?", (uid,)
        ):
            writer.writerow(["body", r["waist_cm"], r["neck_cm"], r["height_cm"], r["hip_cm"] or "", r["logged_at"]])
        for r in conn.execute(
            "SELECT vertical_jump_cm, sprint_40m_s, sit_and_reach_cm, logged_at FROM performance_logs WHERE user_id = ?",
            (uid,),
        ):
            writer.writerow(["performance", r["vertical_jump_cm"], r["sprint_40m_s"], r["sit_and_reach_cm"], "", r["logged_at"]])
        for r in conn.execute(
            "SELECT bodyweight_kg, logged_at FROM bodyweight_logs WHERE user_id = ?", (uid,)
        ):
            writer.writerow(["bodyweight", r["bodyweight_kg"], "", "", "", r["logged_at"]])
        for r in conn.execute(
            "SELECT exercise_name, sets, reps, weight_kg, minutes, logged_at FROM exercise_logs WHERE user_id = ?",
            (uid,),
        ):
            writer.writerow(["exercise", r["exercise_name"], r["sets"], r["reps"] or "",
                             r["weight_kg"] if r["weight_kg"] is not None else r["minutes"] or "",
                             r["logged_at"]])
    resp = Response(out.getvalue(), mimetype="text/csv")
    resp.headers["Content-Disposition"] = f"attachment; filename=ironbound-{datetime.now():%Y%m%d}.csv"
    return resp


@app.route("/import", methods=["POST"])
@login_required
def import_data():
    uid = current_user_id()
    file = request.files.get("file")
    if not file or not file.filename:
        flash("Choose a backup JSON file to import.", "error")
        return redirect(url_for("my_logs"))
    try:
        payload = json.loads(file.read().decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        flash("That file isn't valid JSON.", "error")
        return redirect(url_for("my_logs"))
    if not isinstance(payload, dict):
        flash("That file isn't a valid backup (expected a JSON object).", "error")
        return redirect(url_for("my_logs"))

    counts = {"strength": 0, "cardio": 0, "body": 0, "performance": 0, "exercise": 0, "bodyweight": 0}
    with db_conn() as conn:
        def put(key, table, cols, rows, mapper):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                try:
                    values = mapper(row)
                except (ValueError, TypeError, KeyError, OverflowError):
                    continue  # skip malformed rows instead of crashing the import
                if values is None:
                    continue
                conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                    values,
                )
                counts[key] += 1

        put("strength", "strength_logs", ["user_id", "lift", "weight_kg", "reps", "logged_at"],
            payload.get("strength_logs", []),
            lambda r: (uid, str(r.get("lift", "")), float(r["weight_kg"]), int(r["reps"]),
                       str(r.get("logged_at") or datetime.now().isoformat()))
            if r.get("weight_kg") and r.get("reps") else None)
        put("cardio", "cardio_logs", ["user_id", "distance_km", "minutes", "logged_at"],
            payload.get("cardio_logs", []),
            lambda r: (uid, float(r["distance_km"]), float(r["minutes"]),
                       str(r.get("logged_at") or datetime.now().isoformat()))
            if r.get("distance_km") and r.get("minutes") else None)
        put("body", "body_logs", ["user_id", "waist_cm", "neck_cm", "height_cm", "hip_cm", "logged_at"],
            payload.get("body_logs", []),
            lambda r: (uid, float(r["waist_cm"]), float(r["neck_cm"]), float(r["height_cm"]),
                       float(r["hip_cm"]) if r.get("hip_cm") is not None else None,
                       str(r.get("logged_at") or datetime.now().isoformat()))
            if r.get("waist_cm") and r.get("neck_cm") and r.get("height_cm") else None)
        put("performance", "performance_logs", ["user_id", "vertical_jump_cm", "sprint_40m_s", "sit_and_reach_cm", "logged_at"],
            payload.get("performance_logs", []),
            lambda r: (uid,
                       float(r["vertical_jump_cm"]) if r.get("vertical_jump_cm") is not None else None,
                       float(r["sprint_40m_s"]) if r.get("sprint_40m_s") is not None else None,
                       float(r["sit_and_reach_cm"]) if r.get("sit_and_reach_cm") is not None else None,
                       str(r.get("logged_at") or datetime.now().isoformat())))
        put("bodyweight", "bodyweight_logs", ["user_id", "bodyweight_kg", "logged_at"],
            payload.get("bodyweight_logs", []),
            lambda r: (uid, float(r["bodyweight_kg"]),
                       str(r.get("logged_at") or datetime.now().isoformat()))
            if r.get("bodyweight_kg") else None)
        put("exercise", "exercise_logs",
            ["user_id", "exercise_key", "exercise_name", "feeds_stat", "sets",
             "reps", "weight_kg", "minutes", "logged_at"],
            payload.get("exercise_logs", []),
            lambda r: (uid, str(r.get("exercise_key", "") or "custom"),
                       str(r.get("exercise_name", "") or "Exercise"),
                       str(r.get("feeds_stat", "") or "STR"),
                       int(r.get("sets") or 1),
                       int(r["reps"]) if r.get("reps") is not None else None,
                       float(r["weight_kg"]) if r.get("weight_kg") is not None else None,
                       float(r["minutes"]) if r.get("minutes") is not None else None,
                       str(r.get("logged_at") or datetime.now().isoformat()))
            if r.get("exercise_name") and (r.get("reps") is not None or r.get("minutes") is not None)
            else None)
        pf = payload.get("plan_prefs")
        if isinstance(pf, list) and pf and isinstance(pf[0], dict):
            p = pf[0]
            conn.execute(
                "INSERT OR REPLACE INTO plan_prefs (user_id, goal, training_days, rest_days, "
                "focus_muscles, diet_type, allergies, diet_rules, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uid, str(p.get("goal") or "all_round"),
                 min(6, max(1, int(p.get("training_days") or 3))),
                 str(p.get("rest_days") or "[]"),
                 str(p.get("focus_muscles") or "[]"),
                 str(p.get("diet_type") or "non_veg"),
                 str(p.get("allergies") or "[]"),
                 str(p.get("diet_rules") or ""),
                 datetime.now().isoformat()),
            )

    imported = sum(counts.values())
    flash(f"Import complete: {imported} entries restored ✓", "success")
    return redirect(url_for("my_logs"))


# ---------------------------------------------------------------------------
# OFFLINE SYNC (queued log entries from the PWA)
# ---------------------------------------------------------------------------

@app.route("/sync/queue", methods=["POST"])
@login_required
def sync_queue():
    """Bulk-create log entries queued offline by the browser.

    Body: {"items": [{"type": "strength", "data": {...}, "client_id": "c..."}, ...]}
    Each item is validated exactly like the web form; bad items are skipped (and
    reported) instead of failing the whole batch. client_id makes replays
    idempotent — a repeat is acknowledged without creating a duplicate row."""
    uid = current_user_id()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return _sync_json(False, "Expected a JSON object."), 400
    items = payload.get("items")
    if not isinstance(items, list):
        return _sync_json(False, "Expected an items list."), 400
    if len(items) > 200:
        return _sync_json(False, "Too many queued entries (max 200)."), 400

    results = []
    created = 0
    for i, raw in enumerate(items):
        if not isinstance(raw, dict):
            results.append({"index": i, "ok": False, "error": "Malformed entry."})
            continue
        log_type = str(raw.get("type", "") or "")
        if log_type not in LOG_SCHEMA:
            results.append({"index": i, "ok": False, "error": "Unknown log type."})
            continue
        fields = raw.get("data")
        if not isinstance(fields, dict):
            results.append({"index": i, "ok": False, "error": "Missing data."})
            continue
        client_id = raw.get("client_id")
        client_id = str(client_id).strip()[:64] if client_id else None
        try:
            form = _validate_log_form(log_type, data=fields)
            message = _insert_log(log_type, form, uid, client_id=client_id)
            created += 1
            results.append({"index": i, "ok": True, "message": message})
        except ValidationError as e:
            results.append({"index": i, "ok": False, "error": str(e)})
    return _sync_json(True, None, created=created, results=results)


def _sync_json(ok, error=None, **extra):
    body = {"ok": ok}
    if error:
        body["error"] = error
    body.update(extra)
    return Response(json.dumps(body), mimetype="application/json")


# ---------------------------------------------------------------------------
# ERROR HANDLING + SECURITY HEADERS
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(500)
def internal_error(e):
    return render_template("500.html"), 500


@app.route("/offline")
def offline():
    """Public fallback page served from the service-worker cache when the
    app shell can't be reached (no connection)."""
    return render_template("offline.html")


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self' https://api.open-meteo.com https://ipwho.is; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


if __name__ == "__main__":
    init_db()
    # Keep the app private by default (127.0.0.1). For a cloudflare/ngrok tunnel
    # you do NOT need to change this. To share on your home Wi-Fi instead, set:
    #   $env:IRONBOUND_HOST="0.0.0.0"
    host = os.environ.get("IRONBOUND_HOST", "127.0.0.1")
    port = int(os.environ.get("IRONBOUND_PORT", "5000"))
    if os.environ.get("IRONBOUND_SERVER", "").lower() == "waitress":
        from waitress import serve
        print(f"IRONBOUND serving on http://{host}:{port} (Waitress)")
        serve(app, host=host, port=port)
    else:
        debug = os.environ.get("IRONBOUND_DEBUG", "0") == "1"
        app.run(host=host, port=port, debug=debug)