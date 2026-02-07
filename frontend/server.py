#!/usr/bin/env python3
"""
Flask server for the News Tracker frontend.
Serves the dashboard UI and provides a REST API for the pipeline.
"""

import glob
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

from flask import Flask, Response, jsonify, request, send_from_directory
from flask_cors import CORS

# Ensure the project root is importable
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.utils import (
    db_connection,
    get_cache_dir,
    get_data_dir,
    get_default_dedupe_db,
    get_logs_dir,
    get_status_file,
    is_postgres,
    is_vercel,
    normalize_date,
    ph,
    url_to_hash,
)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__, static_folder=".")
CORS(app)

CACHE_DIR = get_cache_dir()
LOGS_DIR = get_logs_dir()
DEDUPE_DB = get_default_dedupe_db()

# ---------------------------------------------------------------------------
# Database initialisation (lazy, thread-safe via flag)
# ---------------------------------------------------------------------------

_db_initialized = False


def _ensure_db():
    """Create application-specific tables if they don't exist yet."""
    global _db_initialized
    if _db_initialized:
        return
    try:
        auto_id = "SERIAL PRIMARY KEY" if is_postgres() else "INTEGER PRIMARY KEY AUTOINCREMENT"
        with db_connection(DEDUPE_DB) as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS deleted_articles (
                    id {auto_id},
                    url TEXT UNIQUE NOT NULL,
                    original_data TEXT NOT NULL,
                    rewritten_data TEXT,
                    quality_gate_data TEXT,
                    source_name TEXT,
                    deleted_at TEXT NOT NULL,
                    deleted_reason TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_deleted_url ON deleted_articles(url)")
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS candidates (
                    id {auto_id},
                    url TEXT UNIQUE NOT NULL,
                    title TEXT,
                    description TEXT,
                    published_at TEXT,
                    source TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_url ON candidates(url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_candidates_source ON candidates(source)")
        _db_initialized = True
    except Exception as e:
        print(f"Warning: DB init error: {e}")


def _error_response(message: str, status_code: int = 500):
    """Return a JSON error without leaking tracebacks to the client."""
    return jsonify({"error": message}), status_code


# ---------------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/article.html")
def article_page():
    return send_from_directory(".", "article.html")


# ---------------------------------------------------------------------------
# Articles API
# ---------------------------------------------------------------------------

@app.route("/api/articles")
def get_articles():
    """Return all extracted + rewritten articles."""
    articles = []
    processed_urls: set = set()

    # Rewritten files first (contain both original + rewritten)
    for json_file in sorted(glob.glob(str(CACHE_DIR / "rewritten_*.json")), reverse=True):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            article = {
                **data.get("original", {}),
                "rewritten": data.get("rewritten", {}),
                "has_rewritten": True,
                "processed_at": data.get("processed_at"),
                "quality_gate": data.get("quality_gate", {}),
            }
            articles.append(article)
            processed_urls.add(data.get("url", ""))
        except Exception as e:
            print(f"Error reading {json_file}: {e}")

    # Then extracted-only (not yet rewritten)
    for json_file in sorted(glob.glob(str(CACHE_DIR / "extracted_*.json")), reverse=True):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                article = json.load(f)
            if article.get("url") not in processed_urls:
                article["has_rewritten"] = False
                articles.append(article)
        except Exception as e:
            print(f"Error reading {json_file}: {e}")

    return jsonify(articles)


@app.route("/api/article/<path:filename>")
def get_article(filename):
    json_file = CACHE_DIR / filename
    if json_file.exists():
        with open(json_file, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return _error_response("Article not found", 404)


@app.route("/api/article-by-url")
def get_article_by_url():
    url = request.args.get("url")
    if not url:
        return _error_response("URL not specified", 400)

    file_hash = url_to_hash(url)
    rewritten_file = CACHE_DIR / f"rewritten_{file_hash}.json"
    extracted_file = CACHE_DIR / f"extracted_{file_hash}.json"

    if rewritten_file.exists():
        with open(rewritten_file, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))

    if extracted_file.exists():
        with open(extracted_file, "r", encoding="utf-8") as f:
            article = json.load(f)
        return jsonify({
            "original": article,
            "rewritten": {},
            "url": article.get("url"),
            "source_name": article.get("source_name"),
        })

    return _error_response("Article not found", 404)


# ---------------------------------------------------------------------------
# Candidates API
# ---------------------------------------------------------------------------

@app.route("/api/candidates", methods=["GET"])
def get_candidates():
    """Return candidates grouped by source. Pass ?refresh=true to re-fetch feeds."""
    try:
        _ensure_db()
        refresh = request.args.get("refresh", "false").lower() == "true"

        if refresh:
            batch = int(request.args.get("batch", "0"))
            _refresh_candidates(batch=batch)

        # Read candidates from DB
        with db_connection(DEDUPE_DB) as conn:
            cur = conn.execute("""
                SELECT url, title, description, published_at, source
                FROM candidates ORDER BY published_at DESC
            """)
            rows = cur.fetchall()

        by_source: dict = {}
        for url, title, description, published_at, source in rows:
            by_source.setdefault(source, []).append({
                "url": url,
                "title": title or "",
                "description": description or "",
                "published_at": normalize_date(published_at),
                "source": source,
            })

        for articles in by_source.values():
            articles.sort(key=lambda x: x.get("published_at") or "", reverse=True)

        total = sum(len(v) for v in by_source.values())
        result = {
            "success": True,
            "total": total,
            "by_source": by_source,
            "sources": list(by_source.keys()),
        }
        if refresh and is_vercel():
            import yaml
            config_path = BASE_DIR / "config" / "sources.yaml"
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            n_enabled = len([f for f in cfg.get("rss_feeds", []) if f.get("enabled", True)])
            import math
            result["total_batches"] = math.ceil(n_enabled / _VERCEL_BATCH_SIZE)
        return jsonify(result)

    except Exception as e:
        print(f"Candidates error: {e}")
        return _error_response(f"Error fetching candidates: {e}")


_VERCEL_BATCH_SIZE = 4  # feeds per batch on Vercel (keeps each request under 10s)


def _refresh_candidates(batch=0):
    """Fetch RSS feeds and store candidates in the database.

    On Vercel, feeds are fetched in batches of _VERCEL_BATCH_SIZE.
    batch=0 clears existing candidates first; batch>0 appends.
    Locally, all feeds are fetched in a single call (batch is ignored).
    """
    import yaml

    config_path = BASE_DIR / "config" / "sources.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        sources_config = yaml.safe_load(f) or {}

    if is_vercel():
        all_candidates = _fetch_feeds_parallel(sources_config, batch=batch)
    else:
        from src.fetch_sources import SourceFetcher
        fetcher = SourceFetcher(
            sources_config=sources_config,
            dedupe_db_path=str(DEDUPE_DB),
            rate_limit_delay=sources_config.get("rate_limit", {}).get("delay_between_requests", 6.0),
            timeout=sources_config.get("timeouts", {}).get("download", 30),
        )
        all_candidates = fetcher.fetch_all(limit=None)

    now = datetime.now().isoformat()
    p = ph()
    conflict = "ON CONFLICT (url) DO NOTHING" if is_postgres() else "OR IGNORE"
    with db_connection(DEDUPE_DB) as conn:
        if batch == 0:
            conn.execute("DELETE FROM candidates")
        for c in all_candidates:
            conn.execute(f"""
                INSERT {'' if is_postgres() else conflict} INTO candidates
                (url, title, description, published_at, source, created_at, updated_at)
                VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
                {conflict if is_postgres() else ''}
            """, (
                c.get("url"),
                c.get("title", ""),
                c.get("description", ""),
                normalize_date(c.get("published_at")),
                c.get("source", "Unknown"),
                now, now,
            ))


def _fetch_feeds_parallel(sources_config, batch=0):
    """Fetch RSS feeds in parallel (for Vercel's 10s limit).

    Feeds are split into batches of _VERCEL_BATCH_SIZE. Only the
    requested batch is fetched per invocation.
    """
    import feedparser as fp
    from concurrent.futures import ThreadPoolExecutor, as_completed

    feeds = sources_config.get("rss_feeds", [])
    enabled = [f for f in feeds if f.get("enabled", True)]
    start = batch * _VERCEL_BATCH_SIZE
    enabled = enabled[start:start + _VERCEL_BATCH_SIZE]
    if not enabled:
        return []

    def fetch_one(feed_cfg):
        name = feed_cfg.get("name", feed_cfg["url"])
        try:
            resp = requests.get(
                feed_cfg["url"], timeout=2,
                headers={"User-Agent": "Mozilla/5.0 (compatible; NewsPipeline/1.0)"},
            )
            resp.raise_for_status()
            parsed = fp.parse(resp.content)
            results = []
            for entry in parsed.entries:
                url = entry.get("link", "")
                if not url:
                    continue
                pub_str = None
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    pub_str = datetime(*entry.published_parsed[:6]).isoformat()
                elif hasattr(entry, "published") and entry.published:
                    pub_str = entry.published
                results.append({
                    "url": url,
                    "source": name,
                    "published_at": pub_str,
                    "title": entry.get("title", ""),
                    "description": entry.get("description", ""),
                })
            return results
        except Exception:
            return []

    all_candidates = []
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_one, f): f for f in enabled}
        for future in as_completed(futures, timeout=5):
            try:
                all_candidates.extend(future.result())
            except Exception:
                pass

    all_candidates.sort(key=lambda x: x.get("published_at") or "", reverse=True)
    return all_candidates


@app.route("/api/candidates/refresh-stream")
def refresh_candidates_stream():
    """SSE endpoint for real-time feed refresh progress."""
    import feedparser as fp
    import yaml

    _ensure_db()

    def generate():
        try:
            config_path = BASE_DIR / "config" / "sources.yaml"
            with open(config_path, "r", encoding="utf-8") as f:
                sources_config = yaml.safe_load(f) or {}

            feeds = sources_config.get("rss_feeds", [])
            enabled = [f for f in feeds if f.get("enabled", True)]
            total = len(enabled)

            if total == 0:
                yield f"data: {json.dumps({'type': 'complete', 'total_candidates': 0})}\n\n"
                return

            from src.fetch_sources import SourceFetcher

            delay = sources_config.get("rate_limit", {}).get("delay_between_requests", 6.0)
            if is_vercel():
                delay = 0  # No delay on Vercel — must fit within 10s Hobby plan limit
            fetcher = SourceFetcher(
                sources_config=sources_config,
                dedupe_db_path=str(DEDUPE_DB),
                rate_limit_delay=delay,
                timeout=min(sources_config.get("timeouts", {}).get("download", 30), 3 if is_vercel() else 30),
            )
            fetcher._load_processed_cache()

            all_candidates = []

            for i, feed_cfg in enumerate(enabled):
                name = feed_cfg.get("name", feed_cfg["url"])
                pct = round(i / total * 100)
                yield f"data: {json.dumps({'type': 'progress', 'feed': name, 'index': i, 'total': total, 'pct': pct})}\n\n"

                try:
                    if i > 0:
                        time.sleep(delay)

                    resp = fetcher.session.get(feed_cfg["url"], timeout=fetcher.timeout)
                    resp.raise_for_status()

                    parsed = fp.parse(resp.content)
                    count = 0

                    for entry in parsed.entries:
                        url = entry.get("link", "")
                        if not url or not fetcher._is_domain_allowed(url):
                            continue
                        if fetcher._is_url_processed(url):
                            continue

                        pub_str, pub_dt = fetcher._parse_entry_date(entry)
                        if fetcher._is_too_old(pub_dt):
                            continue

                        all_candidates.append({
                            "url": url,
                            "source": name,
                            "published_at": pub_str,
                            "title": entry.get("title", ""),
                            "description": entry.get("description", ""),
                        })
                        count += 1
                        fetcher._mark_url_seen(url, processed=False)

                    done_pct = round((i + 1) / total * 100)
                    yield f"data: {json.dumps({'type': 'feed_done', 'feed': name, 'index': i, 'total': total, 'articles': count, 'pct': done_pct})}\n\n"

                except Exception as e:
                    done_pct = round((i + 1) / total * 100)
                    yield f"data: {json.dumps({'type': 'feed_error', 'feed': name, 'index': i, 'total': total, 'error': str(e)[:100], 'pct': done_pct})}\n\n"

            # Store candidates in DB
            now = datetime.now().isoformat()
            p = ph()
            conflict = "ON CONFLICT (url) DO NOTHING" if is_postgres() else "OR IGNORE"
            with db_connection(DEDUPE_DB) as conn:
                conn.execute("DELETE FROM candidates")
                for c in all_candidates:
                    conn.execute(f"""
                        INSERT {'' if is_postgres() else conflict} INTO candidates
                        (url, title, description, published_at, source, created_at, updated_at)
                        VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
                        {conflict if is_postgres() else ''}
                    """, (
                        c.get("url"),
                        c.get("title", ""),
                        c.get("description", ""),
                        normalize_date(c.get("published_at")),
                        c.get("source", "Unknown"),
                        now, now,
                    ))

            yield f"data: {json.dumps({'type': 'complete', 'total_candidates': len(all_candidates)})}\n\n"

        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)[:200]})}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Pipeline API
# ---------------------------------------------------------------------------

@app.route("/api/extract-articles", methods=["POST"])
def extract_articles():
    """Run the extraction/rewrite pipeline."""
    try:
        data = request.get_json() or {}
        limit = data.get("limit", 10)
        selected_urls = data.get("urls")

        from src.pipeline import NewsPipeline

        pipeline = NewsPipeline(config_dir=str(BASE_DIR / "config"), dry_run=True)

        if selected_urls:
            pipeline.run(limit=None, candidate_urls=selected_urls)
            msg = f"Pipeline completed: {len(selected_urls)} selected articles processed"
        else:
            pipeline.run(limit=limit)
            msg = f"Pipeline completed: up to {limit} articles processed"

        return jsonify({"success": True, "message": msg, "status": "completed"})

    except Exception as e:
        print(f"Pipeline error: {e}")
        return _error_response(f"Pipeline error: {e}")


@app.route("/api/pipeline-status")
def get_pipeline_status():
    status_file = get_status_file()
    if status_file.exists():
        try:
            with open(status_file, "r", encoding="utf-8") as f:
                return jsonify(json.load(f))
        except Exception as e:
            return _error_response(f"Error reading pipeline status: {e}")
    return jsonify({"status": "idle", "message": "No pipeline running"})


@app.route("/api/rewrite-article", methods=["POST"])
def rewrite_article():
    """Extract (if needed) and rewrite a single article."""
    try:
        data = request.get_json()
        article_url = data.get("url")
        if not article_url:
            return _error_response("URL not specified", 400)

        from dotenv import load_dotenv
        load_dotenv()

        from src.extract_article import ArticleExtractor
        from src.quality_gates import QualityGates
        from src.rewrite import ArticleRewriter

        file_hash = url_to_hash(article_url)
        extracted_file = CACHE_DIR / f"extracted_{file_hash}.json"

        # Use cached extraction or extract now
        if extracted_file.exists():
            with open(extracted_file, "r", encoding="utf-8") as f:
                extracted_data = json.load(f)
        else:
            extractor = ArticleExtractor(cache_dir=str(CACHE_DIR))
            extracted_data = extractor.extract(article_url)

        text = extracted_data.get("text", "").strip()
        title = extracted_data.get("title", "").strip()

        if not text or len(text) < 100:
            return _error_response(f"Content too short ({len(text)} chars)", 400)
        if not title or len(title) < 10:
            return _error_response(f"Title too short ({len(title)} chars)", 400)

        # Rewrite
        llm_provider = os.getenv("LLM_PROVIDER", "openai").lower()
        rewriter = ArticleRewriter(
            provider=llm_provider,
            model=os.getenv("OPENAI_MODEL") or os.getenv("ANTHROPIC_MODEL"),
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
        )
        rewritten_data = rewriter.rewrite(extracted_data)

        # Quality gate
        quality_gates = QualityGates()
        quality_result = quality_gates.check(extracted_data, rewritten_data)

        # Save combined result
        combined = {
            "original": extracted_data,
            "rewritten": rewritten_data,
            "url": extracted_data.get("url"),
            "source_name": extracted_data.get("source_name"),
            "processed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "quality_gate": {
                "passed": quality_result.get("ok", False),
                "similarity_score": quality_result.get("similarity_score", 0.0),
                "risk_level": quality_result.get("risk_level", "low"),
                "issues": quality_result.get("issues", []),
            },
        }

        rewritten_file = CACHE_DIR / f"rewritten_{file_hash}.json"
        with open(rewritten_file, "w", encoding="utf-8") as f:
            json.dump(combined, f, indent=2, ensure_ascii=False)

        return jsonify({
            "success": True,
            "rewritten": rewritten_data,
            "article_url": f"/article.html?url={article_url}",
        })

    except Exception as e:
        print(f"Rewrite error: {e}")
        return _error_response(f"Rewrite error: {e}")


# ---------------------------------------------------------------------------
# Delete / Deleted articles
# ---------------------------------------------------------------------------

@app.route("/api/delete-article", methods=["POST"])
def delete_article():
    """Soft-delete an article (archive to DB, remove cache files)."""
    try:
        _ensure_db()
        data = request.get_json()
        article_url = data.get("url")
        if not article_url:
            return _error_response("URL not specified", 400)

        file_hash = url_to_hash(article_url)
        rewritten_file = CACHE_DIR / f"rewritten_{file_hash}.json"
        extracted_file = CACHE_DIR / f"extracted_{file_hash}.json"

        article_data = None
        for path in (rewritten_file, extracted_file):
            if path.exists():
                with open(path, "r", encoding="utf-8") as f:
                    article_data = json.load(f)
                break

        if article_data is None:
            return _error_response("Article not found", 404)

        source_name = article_data.get("source_name", "Unknown")

        p = ph()
        with db_connection(DEDUPE_DB) as conn:
            if is_postgres():
                conn.execute(f"""
                    INSERT INTO deleted_articles
                    (url, original_data, rewritten_data, quality_gate_data, source_name, deleted_at, deleted_reason)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
                    ON CONFLICT (url) DO UPDATE SET
                        original_data = EXCLUDED.original_data,
                        rewritten_data = EXCLUDED.rewritten_data,
                        quality_gate_data = EXCLUDED.quality_gate_data,
                        deleted_at = EXCLUDED.deleted_at,
                        deleted_reason = EXCLUDED.deleted_reason
                """, (
                    article_url,
                    json.dumps(article_data.get("original", article_data), ensure_ascii=False),
                    json.dumps(article_data.get("rewritten", {}), ensure_ascii=False) if article_data.get("rewritten") else None,
                    json.dumps(article_data.get("quality_gate", {}), ensure_ascii=False) if article_data.get("quality_gate") else None,
                    source_name,
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    data.get("reason", "Deleted by user"),
                ))
            else:
                conn.execute(f"""
                    INSERT OR REPLACE INTO deleted_articles
                    (url, original_data, rewritten_data, quality_gate_data, source_name, deleted_at, deleted_reason)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
                """, (
                    article_url,
                    json.dumps(article_data.get("original", article_data), ensure_ascii=False),
                    json.dumps(article_data.get("rewritten", {}), ensure_ascii=False) if article_data.get("rewritten") else None,
                    json.dumps(article_data.get("quality_gate", {}), ensure_ascii=False) if article_data.get("quality_gate") else None,
                    source_name,
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    data.get("reason", "Deleted by user"),
                ))

        for path in (rewritten_file, extracted_file):
            if path.exists():
                path.unlink()

        return jsonify({"success": True, "message": "Article deleted and archived"})

    except Exception as e:
        print(f"Delete error: {e}")
        return _error_response(f"Delete error: {e}")


@app.route("/api/deleted-articles")
def get_deleted_articles():
    try:
        _ensure_db()
        with db_connection(DEDUPE_DB) as conn:
            cur = conn.execute("""
                SELECT url, original_data, rewritten_data, quality_gate_data,
                       source_name, deleted_at, deleted_reason
                FROM deleted_articles ORDER BY deleted_at DESC LIMIT 100
            """)
            rows = cur.fetchall()

        deleted = []
        for url, orig_json, rew_json, qg_json, source, deleted_at, reason in rows:
            article = {"url": url, "source_name": source, "deleted_at": deleted_at, "deleted_reason": reason}
            if orig_json:
                article["original"] = json.loads(orig_json)
            if rew_json:
                article["rewritten"] = json.loads(rew_json)
            if qg_json:
                article["quality_gate"] = json.loads(qg_json)
            deleted.append(article)

        return jsonify(deleted)
    except Exception as e:
        return _error_response(f"Error fetching deleted articles: {e}")


# ---------------------------------------------------------------------------
# Logs & Stats
# ---------------------------------------------------------------------------

@app.route("/api/logs")
def get_logs():
    logs = []
    audit_files = glob.glob(str(LOGS_DIR / "audit_*.jsonl"))
    if audit_files:
        latest = max(audit_files, key=os.path.getmtime)
        try:
            with open(latest, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        entry = json.loads(line)
                        status = entry.get("status", "")
                        level = {"created": "info", "skipped": "warning", "failed": "error"}.get(status, "info")
                        logs.append({
                            "timestamp": entry.get("timestamp"),
                            "level": level,
                            "message": f"[{entry.get('operation', '?')}] {entry.get('url', '')} - {status}",
                        })
        except Exception as e:
            print(f"Log read error: {e}")
    return jsonify(logs[-100:])


@app.route("/api/stats")
def get_stats():
    stats = {"total_articles": 0, "extracted": 0, "created": 0, "skipped": 0, "failed": 0}

    json_files = glob.glob(str(CACHE_DIR / "extracted_*.json"))
    stats["total_articles"] = len(json_files)
    stats["extracted"] = len(json_files)

    report_files = glob.glob(str(LOGS_DIR / "report_*.json"))
    if report_files:
        latest = max(report_files, key=os.path.getmtime)
        try:
            with open(latest, "r", encoding="utf-8") as f:
                report = json.load(f)
            if "stats" in report:
                stats.update(report["stats"])
        except Exception as e:
            print(f"Report read error: {e}")

    return jsonify(stats)


# ---------------------------------------------------------------------------
# Monitor API
# ---------------------------------------------------------------------------

@app.route("/api/monitor/start", methods=["POST"])
def start_monitor():
    try:
        data = request.get_json() or {}
        poll_interval = data.get("poll_interval", 300)

        from src.monitor import get_monitor
        monitor = get_monitor(poll_interval=poll_interval, dry_run=True)

        if monitor.is_running:
            return _error_response("Monitor already running", 400)

        monitor.start()
        return jsonify({"success": True, "message": f"Monitor started (polling every {poll_interval}s)", "poll_interval": poll_interval})
    except Exception as e:
        return _error_response(f"Monitor start error: {e}")


@app.route("/api/monitor/stop", methods=["POST"])
def stop_monitor():
    try:
        from src.monitor import get_monitor
        monitor = get_monitor()

        if not monitor.is_running:
            return _error_response("Monitor not running", 400)

        monitor.stop()
        return jsonify({"success": True, "message": "Monitor stopped"})
    except Exception as e:
        return _error_response(f"Monitor stop error: {e}")


@app.route("/api/monitor/status")
def get_monitor_status():
    try:
        from src.monitor import get_monitor
        return jsonify(get_monitor().get_stats())
    except Exception as e:
        return jsonify({"error": str(e), "running": False}), 500


# ---------------------------------------------------------------------------
# Email test
# ---------------------------------------------------------------------------

@app.route("/api/test-email", methods=["POST"])
def test_email():
    try:
        from src.email_notifier import get_email_notifier
        notifier = get_email_notifier()

        if not notifier.enabled:
            return _error_response("Email notifications not enabled. Set EMAIL_NOTIFICATIONS_ENABLED=true.", 400)

        test_articles = [{"url": "https://example.com/test", "title": "Test Article", "source": "Test"}]
        if notifier.send_new_articles_notification(test_articles):
            return jsonify({"success": True, "message": f"Test email sent to {notifier.recipient}"})
        return _error_response("Email send failed — check logs.")
    except Exception as e:
        return _error_response(f"Email test error: {e}")


# ---------------------------------------------------------------------------
# Vercel handler & local startup
# ---------------------------------------------------------------------------

def handler(request):
    """Vercel serverless handler."""
    return app(request.environ, lambda status, headers: None)


if __name__ == "__main__":
    print("Starting News Tracker server...")
    print(f"  Cache:    {CACHE_DIR}")
    print(f"  Logs:     {LOGS_DIR}")
    print(f"  Database: {DEDUPE_DB}")

    port = 5000
    try:
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", port))
    except OSError:
        print(f"  Port {port} busy, using 5001")
        port = 5001

    print(f"\n  Open http://localhost:{port}\n")

    try:
        app.run(debug=True, host="127.0.0.1", port=port)
    except PermissionError:
        print(f"  Error: no permission for port {port}. Try a different port or sudo.")
        sys.exit(1)
    except Exception as e:
        print(f"  Error: {e}")
        sys.exit(1)
