"""
Logging module per audit trail completo.
Ogni operazione viene loggata in formato JSON per facilitare analisi e debugging.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


class AuditLogger:
    """Logger per audit trail con output JSON strutturato."""
    
    def __init__(self, log_dir: str = "./data/logs", log_level: str = "INFO"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup logging standard Python
        self.logger = logging.getLogger("news_pipeline")
        self.logger.setLevel(getattr(logging, log_level.upper()))
        
        # Handler per file
        log_file = self.log_dir / f"pipeline_{datetime.now().strftime('%Y%m%d')}.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        )
        self.logger.addHandler(file_handler)
        
        # Handler per console
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(
            logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        )
        self.logger.addHandler(console_handler)
        
        # File per audit JSON line-by-line
        self.audit_file = self.log_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"
        self.audit_file.touch(exist_ok=True)
    
    def log_operation(
        self,
        operation: str,
        url: str,
        status: str,  # "skipped", "created", "failed"
        details: Optional[Dict[str, Any]] = None,
        timing: Optional[Dict[str, float]] = None,
        post_id: Optional[int] = None
    ):
        """
        Logga un'operazione in formato JSON per audit.
        
        Args:
            operation: Nome dell'operazione (fetch, extract, dedupe, rewrite, quality, wp_post)
            url: URL dell'articolo processato
            status: Esito ("skipped", "created", "failed")
            details: Dettagli aggiuntivi (motivo skip, errori, etc.)
            timing: Timing step-by-step in secondi
            post_id: ID del post WordPress creato (se applicabile)
        """
        audit_entry = {
            "timestamp": datetime.now().isoformat(),
            "operation": operation,
            "url": url,
            "status": status,
            "post_id": post_id,
            "timing": timing or {},
            "details": details or {}
        }
        
        # Scrivi su file JSONL
        with open(self.audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(audit_entry, ensure_ascii=False) + "\n")
        
        # Log anche su logger standard
        message = f"[{operation}] {url} - {status}"
        if post_id:
            message += f" (post_id: {post_id})"
        if details:
            message += f" - {json.dumps(details)}"
        
        if status == "failed":
            self.logger.error(message)
        elif status == "skipped":
            self.logger.warning(message)
        else:
            self.logger.info(message)
    
    def log_info(self, message: str):
        """Log messaggio informativo."""
        self.logger.info(message)
    
    def log_error(self, message: str, exc_info: bool = False):
        """Log errore."""
        self.logger.error(message, exc_info=exc_info)
    
    def log_warning(self, message: str):
        """Log warning."""
        self.logger.warning(message)
    
    def generate_report(self, run_id: str, stats: Dict[str, Any]) -> str:
        """
        Genera report finale della run.
        
        Args:
            run_id: Identificatore univoco della run
            stats: Statistiche della run (total, created, skipped, failed, etc.)
        
        Returns:
            Path del file report generato
        """
        report_file = self.log_dir / f"report_{run_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        
        report = {
            "run_id": run_id,
            "timestamp": datetime.now().isoformat(),
            "stats": stats
        }
        
        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        self.logger.info(f"Report generato: {report_file}")
        return str(report_file)


# Singleton instance
_logger_instance: Optional[AuditLogger] = None


def get_logger(log_dir: str = None, log_level: str = None) -> AuditLogger:
    """Ottieni istanza singleton del logger."""
    global _logger_instance
    
    if _logger_instance is None:
        log_dir = log_dir or os.getenv("LOG_DIR", "./data/logs")
        log_level = log_level or os.getenv("LOG_LEVEL", "INFO")
        _logger_instance = AuditLogger(log_dir=log_dir, log_level=log_level)
    
    return _logger_instance
