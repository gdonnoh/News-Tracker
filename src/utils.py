"""
Shared utilities for the News Pipeline.
Centralizes path handling, date parsing, database access, and URL hashing.
"""

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Path / environment helpers
# ---------------------------------------------------------------------------

_BASE_DIR: Optional[Path] = None


def get_base_dir() -> Path:
    """Return the project root directory (parent of src/)."""
    global _BASE_DIR
    if _BASE_DIR is None:
        _BASE_DIR = Path(__file__).resolve().parent.parent
    return _BASE_DIR


def is_vercel() -> bool:
    """Detect whether we are running inside a Vercel serverless environment."""
    return (
        os.getenv("VERCEL") == "1"
        or os.getenv("VERCEL_ENV") is not None
        or "/var/task" in str(Path(__file__).resolve())
    )


def get_data_dir() -> Path:
    """Return the writable data directory (Vercel-aware)."""
    if is_vercel():
        data_dir = Path("/tmp")
    else:
        data_dir = get_base_dir() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_cache_dir() -> Path:
    """Return the cache directory for extracted/rewritten JSON files."""
    cache_dir = get_data_dir() / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def get_logs_dir() -> Path:
    """Return the logs directory."""
    logs_dir = get_data_dir() / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    return logs_dir


def get_default_dedupe_db() -> Path:
    """Return the default path for the deduplication database."""
    return get_data_dir() / "dedupe.db"


def get_status_file() -> Path:
    """Return the path for the pipeline status JSON file."""
    return get_data_dir() / "pipeline_status.json"


# ---------------------------------------------------------------------------
# URL hashing
# ---------------------------------------------------------------------------

def url_to_hash(url: str) -> str:
    """
    Convert a URL to a filesystem-safe hash string.
    Used for naming cache files (extracted_*, rewritten_*).
    """
    return url.replace("://", "_").replace("/", "_").replace("?", "_")[:100]


# ---------------------------------------------------------------------------
# Italian date normalisation
# ---------------------------------------------------------------------------

_ITALIAN_TO_ENGLISH = {
    # Days (abbreviated)
    "lun": "Mon", "mar": "Tue", "mer": "Wed", "gio": "Thu",
    "ven": "Fri", "sab": "Sat", "dom": "Sun",
    # Days (full)
    "lunedì": "Monday", "martedì": "Tuesday", "mercoledì": "Wednesday",
    "giovedì": "Thursday", "venerdì": "Friday", "sabato": "Saturday",
    "domenica": "Sunday",
    # Months (abbreviated)
    "gen": "Jan", "feb": "Feb",
    "apr": "Apr", "mag": "May", "giu": "Jun", "lug": "Jul",
    "ago": "Aug", "set": "Sep", "ott": "Oct", "nov": "Nov", "dic": "Dec",
    # Months (full)
    "gennaio": "January", "febbraio": "February", "marzo": "March",
    "aprile": "April", "maggio": "May", "giugno": "June",
    "luglio": "July", "agosto": "August", "settembre": "September",
    "ottobre": "October", "novembre": "November", "dicembre": "December",
}

# Pre-compile the regex patterns for speed
_ITALIAN_DATE_PATTERNS = [
    (re.compile(r"\b" + re.escape(it) + r"\b", re.IGNORECASE), en)
    for it, en in _ITALIAN_TO_ENGLISH.items()
]


def normalize_date(date_str: Optional[str]) -> Optional[str]:
    """
    Parse a date string (including Italian formats) and return ISO-8601.
    Returns the original string if parsing fails entirely.
    Returns None for None/empty input.
    """
    if not date_str:
        return None

    # Fast path: already looks like ISO
    if "T" in date_str or (date_str.startswith("20") and len(date_str) >= 10):
        return date_str

    # Translate Italian day/month names to English
    normalised = date_str
    for pattern, replacement in _ITALIAN_DATE_PATTERNS:
        normalised = pattern.sub(replacement, normalised)

    # Try dateutil first
    try:
        from dateutil import parser as date_parser
        return date_parser.parse(normalised).isoformat()
    except (ValueError, TypeError, AttributeError):
        pass

    # Fallback: feedparser's RFC-2822 parser
    try:
        import feedparser
        parsed = feedparser._parse_date(normalised)
        if parsed:
            return datetime(*parsed[:6]).isoformat()
    except Exception:
        pass

    return date_str


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

@contextmanager
def db_connection(db_path: Optional[Path] = None, timeout: float = 10.0):
    """
    Context manager for SQLite connections.

    Usage:
        with db_connection() as conn:
            conn.execute("SELECT ...")
    """
    if db_path is None:
        db_path = get_default_dedupe_db()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=timeout)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
