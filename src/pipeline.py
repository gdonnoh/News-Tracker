"""
Main end-to-end pipeline for article processing.
Orchestrates: fetch -> extract -> dedupe -> rewrite -> quality -> wp_post
"""

import argparse
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from dotenv import load_dotenv

from src.logger import get_logger
from src.fetch_sources import SourceFetcher
from src.extract_article import ArticleExtractor
from src.dedupe import Deduplicator
from src.rewrite import ArticleRewriter
from src.quality_gates import QualityGates
from src.wp_client import WordPressClient
from src.utils import (
    get_data_dir,
    get_cache_dir,
    get_status_file,
    url_to_hash,
)

load_dotenv()
logger = get_logger()


class NewsPipeline:
    """Main pipeline for article processing."""

    def __init__(self, config_dir: str = "./config", dry_run: bool = False):
        self.config_dir = Path(config_dir)
        self.dry_run = dry_run
        self.status_file = get_status_file()

        # Load configs
        self.sources_config = self._load_config("sources.yaml")
        self.categories_config = self._load_config("categories.yaml")

        data_dir = get_data_dir()
        default_dedupe_db = data_dir / "dedupe.db"

        # --- Component initialisation ---
        self.fetcher = SourceFetcher(
            sources_config=self.sources_config,
            dedupe_db_path=os.getenv("DEDUPE_DB_PATH", str(default_dedupe_db)),
            rate_limit_delay=self.sources_config.get("rate_limit", {}).get("delay_between_requests", 6.0),
            timeout=self.sources_config.get("timeouts", {}).get("download", 30),
        )
        self.fetcher.set_status_callback(self._update_fetch_status)

        self.extractor = ArticleExtractor(
            cache_dir=str(data_dir / "cache"),
            timeout=self.sources_config.get("timeouts", {}).get("download", 30),
            rate_limit_delay=float(os.getenv("EXTRACT_DELAY", "0.5")),
            save_raw_html=os.getenv("SAVE_RAW_HTML", "0") == "1",
            save_extracted_json=os.getenv("SAVE_EXTRACTED_JSON", "1") == "1",
        )

        self.deduplicator = Deduplicator(
            dedupe_db_path=os.getenv("DEDUPE_DB_PATH", str(default_dedupe_db)),
            similarity_threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.85")),
        )

        llm_provider = os.getenv("LLM_PROVIDER", "openai").lower()
        self.rewriter = ArticleRewriter(
            provider=llm_provider,
            model=os.getenv("OPENAI_MODEL") or os.getenv("ANTHROPIC_MODEL"),
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY"),
        )

        self.quality_gates = QualityGates(
            similarity_threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.85")),
            min_length=int(os.getenv("MIN_ARTICLE_LENGTH", "200")),
            max_length=int(os.getenv("MAX_ARTICLE_LENGTH", "2000")),
        )

        # WordPress client (only when not dry-running)
        self.wp_client = None
        if not dry_run:
            wp_url = os.getenv("WORDPRESS_URL")
            if wp_url:
                self.wp_client = WordPressClient(
                    wp_url=wp_url,
                    username=os.getenv("WORDPRESS_USERNAME"),
                    app_password=os.getenv("WORDPRESS_APP_PASSWORD"),
                    jwt_token=os.getenv("WORDPRESS_JWT_TOKEN"),
                )
            else:
                logger.log_warning("WORDPRESS_URL not configured — posts will not be created.")
        else:
            logger.log_info("DRY RUN mode: no WordPress posts will be created")

    # ------------------------------------------------------------------
    # Config
    # ------------------------------------------------------------------

    def _load_config(self, filename: str) -> Dict:
        config_path = self.config_dir / filename
        if not config_path.exists():
            logger.log_warning(f"Config file not found: {config_path}. Using defaults.")
            return {}
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # ------------------------------------------------------------------
    # Status persistence (single implementation)
    # ------------------------------------------------------------------

    def _save_status(self, stats: Dict):
        """Persist current pipeline status to disk for the frontend."""
        try:
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.status_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.log_warning(f"Error saving status: {e}")

    def _update_fetch_status(self, step: str, message: str):
        """Callback invoked by SourceFetcher to append status messages."""
        try:
            stats = {}
            if self.status_file.exists():
                with open(self.status_file, "r", encoding="utf-8") as f:
                    stats = json.load(f)

            if "messages" not in stats:
                stats["messages"] = []

            stats["messages"].append({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "step": step,
                "message": message,
            })
            stats["messages"] = stats["messages"][-20:]
            self._save_status(stats)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Rewritten data persistence
    # ------------------------------------------------------------------

    def _save_rewritten_data(self, extracted_data: Dict, rewritten_data: Dict, quality_result: Optional[Dict] = None):
        """Save combined original + rewritten data for the frontend."""
        try:
            cache_dir = get_cache_dir()
            file_hash = url_to_hash(extracted_data.get("url", ""))
            rewritten_file = cache_dir / f"rewritten_{file_hash}.json"

            combined = {
                "original": extracted_data,
                "rewritten": rewritten_data,
                "url": extracted_data.get("url"),
                "source_name": extracted_data.get("source_name"),
                "processed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }

            if quality_result:
                combined["quality_gate"] = {
                    "passed": quality_result.get("ok", False),
                    "similarity_score": quality_result.get("similarity_score", 0.0),
                    "risk_level": quality_result.get("risk_level", "low"),
                    "issues": quality_result.get("issues", []),
                }

            with open(rewritten_file, "w", encoding="utf-8") as f:
                json.dump(combined, f, indent=2, ensure_ascii=False)

            similarity = quality_result.get("similarity_score", 0.0) if quality_result else None
            sim_str = f"{similarity:.2f}" if similarity is not None else "N/A"
            logger.log_info(f"[SAVE] Rewritten data saved: {rewritten_file.name} (similarity: {sim_str})")
        except Exception as e:
            logger.log_error(f"Error saving rewritten data: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Single-article processing
    # ------------------------------------------------------------------

    def process_article(self, candidate: Dict) -> Dict:
        """
        Process a single article end-to-end.

        Returns dict with keys: status, reason, post_id, timing
        """
        url = candidate["url"]
        timing: Dict[str, float] = {}
        start_time = time.time()

        try:
            # Step 1: Extract
            step_start = time.time()
            logger.log_info(f"[EXTRACT] Processing: {url}")
            extracted_data = self.extractor.extract(url, source_name=candidate.get("source"))
            timing["extract"] = time.time() - step_start

            text = extracted_data.get("text", "").strip()
            title = extracted_data.get("title", "").strip()

            # Paywall check
            if extracted_data.get("is_paywalled") and os.getenv("SKIP_PAYWALL", "0") == "1":
                logger.log_warning("[SKIP] Paywall detected")
                return {"status": "skipped", "reason": "paywall_detected", "post_id": None, "timing": timing}

            if not text or len(text) < 100:
                logger.log_warning(f"[SKIP] Content empty or too short: {len(text)} chars")
                return {"status": "skipped", "reason": f"empty_content: {len(text)} chars", "post_id": None, "timing": timing}

            if not title or len(title) < 10:
                logger.log_warning(f"[SKIP] Title empty or too short: {len(title)} chars")
                return {"status": "skipped", "reason": f"empty_title: {len(title)} chars", "post_id": None, "timing": timing}

            # Step 2: Dedupe
            step_start = time.time()
            logger.log_info(f"[DEDUPE] Checking: {url}")
            dedupe_result = self.deduplicator.check_duplicate(
                canonical_url=extracted_data["canonical_url"],
                title=extracted_data["title"],
                body=extracted_data.get("text"),
            )
            timing["dedupe"] = time.time() - step_start

            if dedupe_result["is_duplicate"]:
                logger.log_info(f"[SKIP] Duplicate: {dedupe_result['reason']}")
                return {"status": "skipped", "reason": f"duplicate: {dedupe_result['reason']}", "post_id": None, "timing": timing}

            # Step 3: Rewrite
            step_start = time.time()
            logger.log_info(f"[REWRITE] Rewriting: {url}")
            rewritten_data = self.rewriter.rewrite(extracted_data)
            timing["rewrite"] = time.time() - step_start

            # Step 4: Quality Gates
            step_start = time.time()
            logger.log_info(f"[QUALITY] Checking: {url}")
            quality_result = self.quality_gates.check(extracted_data, rewritten_data)
            timing["quality"] = time.time() - step_start

            # Save for frontend (even if quality gate fails)
            self._save_rewritten_data(extracted_data, rewritten_data, quality_result)

            if not quality_result["ok"] or quality_result["risk_level"] == "high":
                reason = f"quality_gate_failed: {', '.join(quality_result['issues'])}"
                logger.log_warning(f"[SKIP] Quality gate failed: {reason}")
                return {"status": "skipped", "reason": reason, "post_id": None, "timing": timing}

            # Step 5: WordPress Post
            if not self.wp_client:
                logger.log_warning("[SKIP] WordPress client not configured")
                return {"status": "skipped", "reason": "wp_client_not_configured", "post_id": None, "timing": timing}

            step_start = time.time()
            logger.log_info(f"[WP_POST] Creating post: {url}")
            category_mapping = self.categories_config.get("category_mapping", {})
            post_id = self.wp_client.create_post_from_pipeline(
                rewritten_data=rewritten_data,
                original_data=extracted_data,
                quality_result=quality_result,
                category_mapping=category_mapping,
            )
            timing["wp_post"] = time.time() - step_start

            if not post_id:
                return {"status": "failed", "reason": "wp_post_creation_failed", "post_id": None, "timing": timing}

            # Register in deduplicator
            self.deduplicator.register_article(
                canonical_url=extracted_data["canonical_url"],
                title=extracted_data["title"],
                body=extracted_data.get("text"),
                wp_post_id=post_id,
            )

            timing["total"] = time.time() - start_time
            logger.log_info(f"[SUCCESS] Post created: ID {post_id}")
            return {"status": "created", "reason": None, "post_id": post_id, "timing": timing}

        except Exception as e:
            logger.log_error(f"[FAILED] Error processing {url}: {e}", exc_info=True)
            timing["total"] = time.time() - start_time
            return {"status": "failed", "reason": str(e), "post_id": None, "timing": timing}

    # ------------------------------------------------------------------
    # Full pipeline run
    # ------------------------------------------------------------------

    def run(self, limit: Optional[int] = None, candidate_urls: Optional[List[str]] = None):
        run_id = str(uuid.uuid4())[:8]
        logger.log_info(f"=== Starting pipeline run: {run_id} ===")

        stats = {
            "total_candidates": 0,
            "processed": 0,
            "created": 0,
            "skipped": 0,
            "failed": 0,
            "run_id": run_id,
            "status": "running",
            "current_step": "fetching",
            "current_article": None,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._save_status(stats)

        try:
            # Step 1: Fetch sources
            logger.log_info("[FETCH] Collecting sources...")
            if candidate_urls:
                candidates = [{"url": u, "source": "Manual Selection", "title": "", "published_at": None} for u in candidate_urls]
                logger.log_info(f"Processing {len(candidates)} manually selected URLs")
            else:
                candidates = self.fetcher.fetch_all(limit=limit)

            stats["total_candidates"] = len(candidates)
            logger.log_info(f"[FETCH] Found {len(candidates)} candidates")

            # Step 2: Process each candidate
            stats["current_step"] = "processing"
            self._save_status(stats)

            for i, candidate in enumerate(candidates, 1):
                stats["current_article"] = {
                    "url": candidate["url"],
                    "title": candidate.get("title", "")[:80],
                    "number": i,
                    "total": len(candidates),
                }
                self._save_status(stats)
                logger.log_info(f"\n--- Processing {i}/{len(candidates)}: {candidate['url']} ---")

                result = self.process_article(candidate)
                stats["processed"] += 1

                logger.log_operation(
                    operation="pipeline",
                    url=candidate["url"],
                    status=result["status"],
                    details={"reason": result.get("reason")},
                    timing=result.get("timing"),
                    post_id=result.get("post_id"),
                )

                if result["status"] == "created":
                    stats["created"] += 1
                elif result["status"] == "skipped":
                    stats["skipped"] += 1
                elif result["status"] == "failed":
                    stats["failed"] += 1

                if i < len(candidates):
                    time.sleep(2)

        except KeyboardInterrupt:
            logger.log_warning("Pipeline interrupted by user")
        except Exception as e:
            logger.log_error(f"Pipeline error: {e}", exc_info=True)
        finally:
            stats["status"] = "completed"
            stats["current_step"] = "completed"
            stats["current_article"] = None
            stats["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._save_status(stats)
            logger.log_info(f"\n=== Pipeline run {run_id} completed ===")
            logger.log_info(f"Stats: {stats}")
            logger.generate_report(run_id, stats)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="News Pipeline - Process articles from RSS to WordPress")
    parser.add_argument("--limit", type=int, default=None, help="Max articles to process")
    parser.add_argument("--dry-run", action="store_true", help="Dry run: skip WordPress posting")
    parser.add_argument("--config-dir", type=str, default="./config", help="Config directory path")
    args = parser.parse_args()

    dry_run = args.dry_run or os.getenv("DRY_RUN", "false").lower() == "true"
    limit = args.limit or int(os.getenv("ARTICLES_LIMIT", "0") or "0") or None

    pipeline = NewsPipeline(config_dir=args.config_dir, dry_run=dry_run)
    pipeline.run(limit=limit)


if __name__ == "__main__":
    main()
