"""IRONBOUND database module - SQLite connection management and migrations."""

import sqlite3
import json
from contextlib import contextmanager
import os

DB_PATH = os.environ.get("IRONBOUND_DB") or "ironbound.db"


def get_db():
    """Get a new SQLite connection with optimal settings for a fitness tracker."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def get_db_connection():
    """Context manager for database connections with automatic cleanup."""
    conn = get_db()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute_sql_file(conn, filepath):
    """Execute SQL from a file, splitting by GO/ statements."""
    with open(filepath, "r") as f:
        sql = f.read()
    # Split by GO (T-SQL style) or semicolons
    statements = [s.strip() for s in sql.split(";") if s.strip()]
    for stmt in statements:
        if stmt.upper().startswith("GO"):
            continue
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            # Skip statements that fail (e.g., duplicate column adds)
            pass


def init_db():
    """Initialize the database with schema and migrations."""
    # Apply V1 init script (creates all tables)
    conn = get_db()
    try:
        execute_sql_file(conn, "ironbound/migrations/1_init.sql")
        # Apply subsequent migrations tolerancefully
        _apply_migrations(conn)
    finally:
        conn.close()


def migrate_db():
    """Run all pending database migrations."""
    init_db()


def _apply_migrations(conn):
    """Apply migrations that tolerate re-running."""
    # V2: Add client_id column (tolerates re-application)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN client_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_client_id ON users(client_id) WHERE client_id IS NOT NULL")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise

    # V3: Add bodyfat column (tolerates re-application)
    try:
        conn.execute("ALTER TABLE body_logs ADD COLUMN bodyfat_percent REAL")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise

    # V5: Add missing columns for tests
    try:
        conn.execute("ALTER TABLE strength_logs ADD COLUMN weight_kg REAL")
        conn.execute("ALTER TABLE strength_logs ADD COLUMN reps INTEGER")
        conn.execute("ALTER TABLE strength_logs ADD COLUMN notes TEXT")
        conn.execute("ALTER TABLE users ADD COLUMN salt TEXT")
    except sqlite3.OperationalError as e:
        if "duplicate column" not in str(e).lower():
            raise

    # V4: Add indexes (tolerates re-application with IF NOT EXISTS)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_strength_logs_user_stat ON strength_logs(user_id, stat)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cardio_logs_user ON cardio_logs(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_body_logs_user ON body_logs(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_stats_user_stat ON stats(user_id, stat)")
    except sqlite3.OperationalError as e:
        if "already exists" not in str(e).lower():
            raise


# SQL migration templates stored as strings
_SQL_V1_INIT = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS strength_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    stat TEXT NOT NULL,
    value REAL NOT NULL,
    date_logged TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS cardio_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    distance_km REAL,
    duration_min REAL,
    date_logged TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS body_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    neck_cm REAL,
    waist_cm REAL,
    hip_cm REAL,
    height_cm REAL,
    date_logged TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    stat TEXT NOT NULL,
    value REAL NOT NULL,
    date_logged TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users(username);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email);
"""

_SQL_V2_ADD_CLIENT_ID = """
ALTER TABLE users ADD COLUMN client_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_client_id ON users(client_id) WHERE client_id IS NOT NULL;
"""

_SQL_V3_ADD_BODYFAT = """
ALTER TABLE body_logs ADD COLUMN bodyfat_percent REAL;
"""

_SQL_V4_ADD_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_strength_logs_user_stat ON strength_logs(user_id, stat);
CREATE INDEX IF NOT EXISTS idx_cardio_logs_user ON cardio_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_body_logs_user ON body_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_stats_user_stat ON stats(user_id, stat);
"""