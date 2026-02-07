"""
RSS feed fetcher with rate limiting, deduplication tracking, and domain whitelisting.
"""

import hashlib
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import feedparser
import requests
from dateutil import parser as date_parser

from src.logger import get_logger
from src.utils import db_connection

logger = get_logger()


class SourceFetcher:
    """Collects article URLs from RSS feeds with deduplication and rate limiting."""

    def __init__(
        self,
        sources_config: Dict,
        dedupe_db_path: str = "./data/dedupe.db",
        rate_limit_delay: float = 6.0,
        timeout: int = 30,
    ):
        self.sources_config = sources_config
        self.dedupe_db_path = Path(dedupe_db_path)
        try:
            self.dedupe_db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.dedupe_db_path = Path("/tmp") / self.dedupe_db_path.name
            self.dedupe_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.rate_limit_delay = rate_limit_delay
        self.timeout = timeout

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; NewsPipeline/1.0; +https://example.com/bot)"
        })

        self._processed_cache: Optional[set] = None
        self.status_callback = None

        self._init_db()

    def set_status_callback(self, callback):
        self.status_callback = callback

    def _update_status(self, step: str, message: str):
        if self.status_callback:
            try:
                self.status_callback(step, message)
            except Exception as e:
                logger.log_warning(f"Status callback error: {e}")

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _init_db(self):
        with db_connection(self.dedupe_db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_urls (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT UNIQUE NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    processed BOOLEAN DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_url ON processed_urls(url)")

    def _load_processed_cache(self):
        if self._processed_cache is not None:
            return
        try:
            with db_connection(self.dedupe_db_path) as conn:
                rows = conn.execute(
                    "SELECT url_hash FROM processed_urls WHERE processed = 1"
                ).fetchall()
                self._processed_cache = {row[0] for row in rows}
        except Exception as e:
            logger.log_warning(f"Could not load processed URL cache: {e}")
            self._processed_cache = set()

    def _is_url_processed(self, url: str) -> bool:
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        if self._processed_cache is not None and url_hash in self._processed_cache:
            return True
        with db_connection(self.dedupe_db_path) as conn:
            result = conn.execute(
                "SELECT processed FROM processed_urls WHERE url_hash = ?", (url_hash,)
            ).fetchone()
        return result is not None and result[0] == 1

    def _mark_url_seen(self, url: str, processed: bool = False):
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        now = datetime.now().isoformat()
        with db_connection(self.dedupe_db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO processed_urls
                (url_hash, url, first_seen_at, last_seen_at, processed)
                VALUES (?, ?,
                    COALESCE((SELECT first_seen_at FROM processed_urls WHERE url_hash = ?), ?),
                    ?, ?)
            """, (url_hash, url, url_hash, now, now, 1 if processed else 0))

        if processed:
            if self._processed_cache is None:
                self._processed_cache = set()
            self._processed_cache.add(url_hash)

    # ------------------------------------------------------------------
    # Domain whitelist
    # ------------------------------------------------------------------

    def _is_domain_allowed(self, url: str) -> bool:
        whitelist = self.sources_config.get("whitelist_domains", {})
        if not whitelist.get("enabled", False):
            return True

        allowed_domains = whitelist.get("domains", [])
        if not allowed_domains:
            return True

        domain = urlparse(url).netloc.lower().removeprefix("www.")

        return any(
            domain == d.lower().removeprefix("www.") or domain.endswith(f".{d.lower().removeprefix('www.')}")
            for d in allowed_domains
        )

    # ------------------------------------------------------------------
    # Date helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_entry_date(entry) -> tuple[Optional[str], Optional[datetime]]:
        """Extract published_at string and datetime from a feedparser entry."""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            dt = datetime(*entry.published_parsed[:6])
            return dt.isoformat(), dt

        if hasattr(entry, "published") and entry.published:
            try:
                dt = date_parser.parse(entry.published)
                return dt.isoformat(), dt
            except (ValueError, TypeError):
                return entry.published, None

        return None, None

    @staticmethod
    def _is_too_old(dt: Optional[datetime], max_age_days: int = 2) -> bool:
        if dt is None:
            return False
        return dt < datetime.now() - timedelta(days=max_age_days)

    # ------------------------------------------------------------------
    # RSS fetching
    # ------------------------------------------------------------------

    def fetch_rss_feeds(self) -> List[Dict]:
        candidates: List[Dict] = []
        self._load_processed_cache()
        feeds = self.sources_config.get("rss_feeds", [])

        for feed_config in feeds:
            if not feed_config.get("enabled", True):
                continue

            feed_url = feed_config["url"]
            source_name = feed_config.get("name", feed_url)

            try:
                logger.log_info(f"Fetching RSS feed: {source_name}")
                self._update_status("fetching", f"Scaricando feed: {source_name}")

                time.sleep(self.rate_limit_delay)

                response = self.session.get(feed_url, timeout=self.timeout)
                response.raise_for_status()

                feed = feedparser.parse(response.content)
                entry_count = len(feed.entries)
                logger.log_info(f"Found {entry_count} articles in feed '{source_name}'")
                self._update_status("fetching", f"Found {entry_count} articles in '{source_name}'")

                skipped_whitelist = skipped_processed = skipped_old = added = 0

                for entry in feed.entries:
                    url = entry.get("link", "")
                    if not url:
                        continue

                    if not self._is_domain_allowed(url):
                        skipped_whitelist += 1
                        continue

                    if self._is_url_processed(url):
                        skipped_processed += 1
                        continue

                    published_at, published_dt = self._parse_entry_date(entry)

                    if self._is_too_old(published_dt):
                        skipped_old += 1
                        continue

                    candidates.append({
                        "url": url,
                        "source": source_name,
                        "published_at": published_at,
                        "title": entry.get("title", ""),
                        "description": entry.get("description", ""),
                    })
                    added += 1
                    self._mark_url_seen(url, processed=False)

                logger.log_info(
                    f"Feed '{source_name}': {added} candidates added "
                    f"(skipped: {skipped_processed} processed, {skipped_old} too old, {skipped_whitelist} whitelist)"
                )

            except requests.exceptions.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                if status == 404:
                    msg = f"Feed unavailable: {source_name} (404)"
                    logger.log_warning(msg)
                else:
                    msg = f"Feed error {source_name}: HTTP {status}"
                    logger.log_error(msg)
                self._update_status("fetching", msg)
            except Exception as e:
                msg = f"Feed fetch error {source_name}: {str(e)[:100]}"
                logger.log_error(f"Feed fetch error {source_name}: {e}", exc_info=True)
                self._update_status("fetching", msg)

        return candidates

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_all(self, limit: Optional[int] = None) -> List[Dict]:
        """
        Collect candidates from all sources, sorted by date (newest first).
        When *limit* is set, articles are distributed round-robin across sources.
        """
        all_candidates = self.fetch_rss_feeds()

        # Final age filter (safety net)
        cutoff = datetime.now() - timedelta(days=2)
        filtered: List[Dict] = []
        skipped_old = 0

        for c in all_candidates:
            pub = c.get("published_at")
            if pub:
                try:
                    dt = date_parser.parse(pub) if isinstance(pub, str) else pub
                    if isinstance(dt, datetime) and dt < cutoff:
                        skipped_old += 1
                        continue
                except (ValueError, TypeError):
                    pass
            filtered.append(c)

        if skipped_old:
            logger.log_info(f"Filtered {skipped_old} articles older than 2 days")

        # Apply limit with round-robin distribution across sources
        if limit and limit > 0 and len(filtered) > limit:
            by_source: Dict[str, List[Dict]] = {}
            for c in filtered:
                by_source.setdefault(c.get("source", "Unknown"), []).append(c)

            for articles in by_source.values():
                articles.sort(key=lambda x: x.get("published_at") or "", reverse=True)

            distributed: List[Dict] = []
            sources = list(by_source.keys())
            max_per_source = (limit // len(sources)) + 1 if sources else limit

            for i in range(limit):
                if not sources:
                    break
                src = sources[i % len(sources)]
                taken = sum(1 for d in distributed if d.get("source") == src)
                if by_source[src] and taken < max_per_source:
                    distributed.append(by_source[src].pop(0))
                if not by_source[src]:
                    sources.remove(src)

            filtered = distributed

        filtered.sort(key=lambda x: x.get("published_at") or "", reverse=True)
        logger.log_info(f"Total candidates collected: {len(filtered)}")
        return filtered
