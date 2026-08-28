"""IRONBOUND configuration module."""

import os
from datetime import timedelta


class Config:
    """Application configuration loaded from environment variables."""

    # Security
    SECRET_KEY = os.environ.get("IRONBOUND_SECRET_KEY") or ""
    WTF_CSRF_ENABLED = True
    DEBUG = os.environ.get("FLASK_ENV") == "development"

    # Server configuration
    HOST = os.environ.get("IRONBOUND_HOST", "0.0.0.0")
    PORT = int(os.environ.get("IRONBOUND_PORT", 5000))
    THREADED_DEBUG = True

    # Session configuration
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.environ.get("IRONBOUND_COOKIE_SECURE", "0") == "1"
    SESSION_COOKIE_NAME = "ironbound_session"
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    # Security headers
    SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "X-XSS-Protection": "1; mode=block",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    }

    # File upload configuration
    MAX_CONTENT_LENGTH = 4 * 1024 * 1024  # 4 MB

    # Database configuration
    DB_PATH = os.environ.get("IRONBOUND_DB") or "ironbound.db"

    # Feature flags
    ENABLE_AURORA = True
    ENABLE_QUICK_LOG = True
    ENABLE_BODY_FAT = True