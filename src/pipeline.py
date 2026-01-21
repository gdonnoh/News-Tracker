"""
Pipeline principale end-to-end per processing articoli.
Orchestra tutti i moduli: fetch -> extract -> dedupe -> rewrite -> quality -> wp_post
"""

import os
import sys
import time
import argparse
import uuid
import json
from pathlib import Path
from typing import Dict, List, Optional
import yaml
from dotenv import load_dotenv

from src.logger import get_logger, AuditLogger
from src.fetch_sources import SourceFetcher
from src.extract_article import ArticleExtractor
from src.dedupe import Deduplicator
from src.rewrite import ArticleRewriter
from src.quality_gates import QualityGates
from src.wp_client import WordPressClient

# Carica variabili ambiente
load_dotenv()

logger = get_logger()


class NewsPipeline:
    """Pipeline principale per processing articoli."""
    
    def __init__(self, config_dir: str = "./config", dry_run: bool = False):
        """
        Inizializza pipeline.
        
        Args:
            config_dir: Directory con file di configurazione
            dry_run: Se True, non crea post WordPress
        """
        self.config_dir = Path(config_dir)
        self.dry_run = dry_run
        
        # Carica configurazioni
        self.sources_config = self._load_config("sources.yaml")
        self.categories_config = self._load_config("categories.yaml")
        
        # Inizializza componenti
        self.fetcher = SourceFetcher(
            sources_config=self.sources_config,
            dedupe_db_path=os.getenv("DEDUPE_DB_PATH", "./data/dedupe.db"),
            rate_limit_delay=self.sources_config.get("rate_limit", {}).get("delay_between_requests", 6.0),
            timeout=self.sources_config.get("timeouts", {}).get("download", 30)
        )
        
        # Imposta callback per aggiornare stato
        self.fetcher.set_status_callback(self._update_fetch_status)
        
        self.extractor = ArticleExtractor(
            cache_dir="./data/cache",
            timeout=self.sources_config.get("timeouts", {}).get("download", 30),
            rate_limit_delay=2.0
        )
        
        self.deduplicator = Deduplicator(
            dedupe_db_path=os.getenv("DEDUPE_DB_PATH", "./data/dedupe.db"),
            similarity_threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.85"))
        )
        
        # LLM provider
        llm_provider = os.getenv("LLM_PROVIDER", "openai").lower()
        self.rewriter = ArticleRewriter(
            provider=llm_provider,
            model=os.getenv("OPENAI_MODEL") or os.getenv("ANTHROPIC_MODEL"),
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        )
        
        self.quality_gates = QualityGates(
            similarity_threshold=float(os.getenv("SIMILARITY_THRESHOLD", "0.85")),
            min_length=int(os.getenv("MIN_ARTICLE_LENGTH", "200")),
            max_length=int(os.getenv("MAX_ARTICLE_LENGTH", "2000"))
        )
        
        # WordPress client (solo se non dry_run)
        self.wp_client = None
        if not dry_run:
            wp_url = os.getenv("WORDPRESS_URL")
            wp_username = os.getenv("WORDPRESS_USERNAME")
            wp_app_password = os.getenv("WORDPRESS_APP_PASSWORD")
            wp_jwt_token = os.getenv("WORDPRESS_JWT_TOKEN")
            
            if wp_url:
                self.wp_client = WordPressClient(
                    wp_url=wp_url,
                    username=wp_username,
                    app_password=wp_app_password,
                    jwt_token=wp_jwt_token
                )
            else:
                logger.log_warning("WORDPRESS_URL non configurato. Post non verranno creati.")
        else:
            logger.log_info("DRY RUN mode: nessun post WordPress verrà creato")
    
    def _load_config(self, filename: str) -> Dict:
        """Carica file YAML di configurazione."""
        config_path = self.config_dir / filename
        if not config_path.exists():
            logger.log_warning(f"File config non trovato: {config_path}. Usando defaults.")
            return {}
        
        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    
    def _save_rewritten_data(self, extracted_data: Dict, rewritten_data: Dict, quality_result: Dict = None):
        """Salva dati riscritti in file JSON per frontend."""
        try:
            import json
            from pathlib import Path
            
            # Usa path assoluto per evitare problemi con working directory
            base_dir = Path(__file__).parent.parent
            cache_dir = base_dir / "data" / "cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            
            # Usa stesso nome file dell'estratto ma con prefisso rewritten_
            url_hash = extracted_data.get("url", "").replace("://", "_").replace("/", "_").replace("?", "_")[:100]
            rewritten_file = cache_dir / f"rewritten_{url_hash}.json"
            
            # Combina dati originali e riscritti
            combined_data = {
                "original": extracted_data,
                "rewritten": rewritten_data,
                "url": extracted_data.get("url"),
                "source_name": extracted_data.get("source_name"),
                "processed_at": time.strftime("%Y-%m-%dT%H:%M:%S")
            }
            
            # Aggiungi info quality gate se disponibile
            if quality_result:
                combined_data["quality_gate"] = {
                    "passed": quality_result.get("ok", False),
                    "similarity_score": quality_result.get("similarity_score", 0.0),
                    "risk_level": quality_result.get("risk_level", "low"),
                    "issues": quality_result.get("issues", [])
                }
            
            with open(rewritten_file, "w", encoding="utf-8") as f:
                json.dump(combined_data, f, indent=2, ensure_ascii=False)
            
            # Log salvataggio con similarità
            if quality_result:
                similarity = quality_result.get('similarity_score', 0.0)
                logger.log_info(f"[SAVE] Dati riscritti salvati: {rewritten_file.name} (similarity: {similarity:.2f})")
            else:
                logger.log_info(f"[SAVE] Dati riscritti salvati: {rewritten_file.name} (similarity: N/A)")
            
        except Exception as e:
            logger.log_error(f"Errore salvataggio dati riscritti: {e}", exc_info=True)
    
    def process_article(self, candidate: Dict) -> Dict:
        """
        Processa un singolo articolo end-to-end.
        
        Args:
            candidate: Candidato da fetch_sources
        
        Returns:
            Dict con risultato processing:
            - status: "skipped" | "created" | "failed"
            - reason: motivo se skipped/failed
            - post_id: ID post WordPress se creato
            - timing: timing step-by-step
        """
        url = candidate["url"]
        timing = {}
        start_time = time.time()
        
        try:
            # Step 1: Extract
            step_start = time.time()
            logger.log_info(f"[EXTRACT] Processing: {url}")
            extracted_data = self.extractor.extract(url, source_name=candidate.get("source"))
            timing["extract"] = time.time() - step_start
            
            # Controllo contenuto estratto
            text = extracted_data.get("text", "").strip()
            title = extracted_data.get("title", "").strip()
            
            if not text or len(text) < 100:
                logger.log_warning(f"[SKIP] Contenuto vuoto o troppo corto: {len(text)} caratteri")
                return {
                    "status": "skipped",
                    "reason": f"empty_content: {len(text)} caratteri",
                    "post_id": None,
                    "timing": timing
                }
            
            if not title or len(title) < 10:
                logger.log_warning(f"[SKIP] Titolo vuoto o troppo corto: {len(title)} caratteri")
                return {
                    "status": "skipped",
                    "reason": f"empty_title: {len(title)} caratteri",
                    "post_id": None,
                    "timing": timing
                }
            
            # Step 2: Dedupe
            step_start = time.time()
            logger.log_info(f"[DEDUPE] Checking: {url}")
            dedupe_result = self.deduplicator.check_duplicate(
                canonical_url=extracted_data["canonical_url"],
                title=extracted_data["title"],
                body=extracted_data.get("text")
            )
            timing["dedupe"] = time.time() - step_start
            
            if dedupe_result["is_duplicate"]:
                logger.log_info(f"[SKIP] Duplicato: {dedupe_result['reason']}")
                return {
                    "status": "skipped",
                    "reason": f"duplicate: {dedupe_result['reason']}",
                    "post_id": None,
                    "timing": timing
                }
            
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
            
            # Salva dati riscritti per frontend (anche se scartati dal quality gate)
            self._save_rewritten_data(extracted_data, rewritten_data, quality_result)
            
            if not quality_result["ok"] or quality_result["risk_level"] == "high":
                reason = f"quality_gate_failed: {', '.join(quality_result['issues'])}"
                logger.log_warning(f"[SKIP] Quality gate failed: {reason}")
                return {
                    "status": "skipped",
                    "reason": reason,
                    "post_id": None,
                    "timing": timing
                }
            
            # Step 5: WordPress Post
            if not self.wp_client:
                logger.log_warning("[SKIP] WordPress client non disponibile")
                return {
                    "status": "skipped",
                    "reason": "wp_client_not_configured",
                    "post_id": None,
                    "timing": timing
                }
            
            step_start = time.time()
            logger.log_info(f"[WP_POST] Creating post: {url}")
            
            category_mapping = self.categories_config.get("category_mapping", {})
            post_id = self.wp_client.create_post_from_pipeline(
                rewritten_data=rewritten_data,
                original_data=extracted_data,
                quality_result=quality_result,
                category_mapping=category_mapping
            )
            timing["wp_post"] = time.time() - step_start
            
            if not post_id:
                return {
                    "status": "failed",
                    "reason": "wp_post_creation_failed",
                    "post_id": None,
                    "timing": timing
                }
            
            # Registra articolo nel deduplicator
            self.deduplicator.register_article(
                canonical_url=extracted_data["canonical_url"],
                title=extracted_data["title"],
                body=extracted_data.get("text"),
                wp_post_id=post_id
            )
            
            timing["total"] = time.time() - start_time
            
            logger.log_info(f"[SUCCESS] Post creato: ID {post_id}")
            return {
                "status": "created",
                "reason": None,
                "post_id": post_id,
                "timing": timing
            }
            
        except Exception as e:
            logger.log_error(f"[FAILED] Errore processing {url}: {e}", exc_info=True)
            timing["total"] = time.time() - start_time
            return {
                "status": "failed",
                "reason": str(e),
                "post_id": None,
                "timing": timing
            }
    
    def run(self, limit: Optional[int] = None):
        """
        Esegue pipeline completa.
        
        Args:
            limit: Limite massimo articoli da processare
        """
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
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S")
        }
        
        # Salva stato iniziale
        self._save_status(stats)
        
        try:
            # Step 1: Fetch sources
            logger.log_info("[FETCH] Collecting sources...")
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
                    "total": len(candidates)
                }
                self._save_status(stats)
                logger.log_info(f"\n--- Processing {i}/{len(candidates)}: {candidate['url']} ---")
                
                result = self.process_article(candidate)
                stats["processed"] += 1
                
                # Log audit
                logger.log_operation(
                    operation="pipeline",
                    url=candidate["url"],
                    status=result["status"],
                    details={"reason": result.get("reason")},
                    timing=result.get("timing"),
                    post_id=result.get("post_id")
                )
                
                # Update stats
                if result["status"] == "created":
                    stats["created"] += 1
                elif result["status"] == "skipped":
                    stats["skipped"] += 1
                elif result["status"] == "failed":
                    stats["failed"] += 1
                
                # Rate limiting tra articoli
                if i < len(candidates):
                    time.sleep(2)
        
        except KeyboardInterrupt:
            logger.log_warning("Pipeline interrotta dall'utente")
        except Exception as e:
            logger.log_error(f"Errore pipeline: {e}", exc_info=True)
        
        finally:
            # Generate report
            stats["status"] = "completed"
            stats["current_step"] = "completed"
            stats["current_article"] = None
            stats["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._save_status(stats)
            logger.log_info(f"\n=== Pipeline run {run_id} completed ===")
            logger.log_info(f"Stats: {stats}")
            report_path = logger.generate_report(run_id, stats)
            logger.log_info(f"Report salvato: {report_path}")
    
    def _update_fetch_status(self, step: str, message: str):
        """Callback per aggiornare stato durante fetch."""
        try:
            # Usa path assoluto per evitare problemi con working directory
            base_dir = Path(__file__).parent.parent
            status_file = base_dir / "data" / "pipeline_status.json"
            if status_file.exists():
                with open(status_file, "r", encoding="utf-8") as f:
                    stats = json.load(f)
            else:
                stats = {}
            
            if "messages" not in stats:
                stats["messages"] = []
            
            stats["messages"].append({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "step": step,
                "message": message
            })
            
            # Mantieni solo ultimi 20 messaggi
            stats["messages"] = stats["messages"][-20:]
            
            self._save_status(stats)
        except Exception as e:
            pass
    
    def _save_status(self, stats: Dict):
        """Salva stato corrente della pipeline per frontend."""
        try:
            import json
            from pathlib import Path
            
            # Usa path assoluto per evitare problemi con working directory
            base_dir = Path(__file__).parent.parent
            status_file = base_dir / "data" / "pipeline_status.json"
            status_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(status_file, "w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.log_warning(f"Errore salvataggio status: {e}")


def main():
    """Entry point CLI."""
    parser = argparse.ArgumentParser(description="News Pipeline - Processa articoli da RSS a WordPress")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limite massimo articoli da processare"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run: non crea post WordPress"
    )
    parser.add_argument(
        "--config-dir",
        type=str,
        default="./config",
        help="Directory con file di configurazione"
    )
    
    args = parser.parse_args()
    
    # Override dry_run da env se presente
    dry_run = args.dry_run or os.getenv("DRY_RUN", "false").lower() == "true"
    
    # Override limit da env se presente
    limit = args.limit or int(os.getenv("ARTICLES_LIMIT", "0") or "0") or None
    
    pipeline = NewsPipeline(config_dir=args.config_dir, dry_run=dry_run)
    pipeline.run(limit=limit)


if __name__ == "__main__":
    main()
