"""
Modulo per monitoraggio continuo dei feed RSS.
Controlla periodicamente i feed e processa automaticamente nuovi articoli.
"""

import time
import threading
import json
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime
from src.pipeline import NewsPipeline
from src.logger import get_logger

logger = get_logger()


class FeedMonitor:
    """Monitora continuamente i feed RSS e processa nuovi articoli."""
    
    def __init__(
        self,
        config_dir: str = "./config",
        poll_interval: int = 300,  # 5 minuti di default
        dry_run: bool = False
    ):
        """
        Inizializza monitor.
        
        Args:
            config_dir: Directory con file di configurazione
            poll_interval: Intervallo tra controlli in secondi (default: 300 = 5 minuti)
            dry_run: Se True, non crea post WordPress
        """
        self.config_dir = config_dir
        self.poll_interval = poll_interval
        self.dry_run = dry_run
        self.is_running = False
        self.monitor_thread = None
        self.pipeline = None
        self.stats_file = Path("./data/monitor_status.json")
        
        # Statistiche
        self.stats = {
            "running": False,
            "started_at": None,
            "last_check": None,
            "poll_interval": poll_interval,
            "total_checks": 0,
            "total_articles_found": 0,
            "total_articles_processed": 0,
            "last_articles": []  # Ultimi 10 articoli processati
        }
    
    def start(self):
        """Avvia monitoraggio in background."""
        if self.is_running:
            logger.log_warning("Monitor già in esecuzione")
            return
        
        self.is_running = True
        self.stats["running"] = True
        self.stats["started_at"] = datetime.now().isoformat()
        self._save_stats()
        
        # Inizializza pipeline
        self.pipeline = NewsPipeline(config_dir=self.config_dir, dry_run=self.dry_run)
        
        # Avvia thread
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()
        
        logger.log_info(f"Monitor avviato (controllo ogni {self.poll_interval} secondi)")
    
    def stop(self):
        """Ferma monitoraggio."""
        if not self.is_running:
            logger.log_warning("Monitor non in esecuzione")
            return
        
        self.is_running = False
        self.stats["running"] = False
        self._save_stats()
        
        logger.log_info("Monitor fermato")
    
    def _monitor_loop(self):
        """Loop principale di monitoraggio."""
        while self.is_running:
            try:
                self._check_feeds()
            except Exception as e:
                logger.log_error(f"Errore durante controllo feed: {e}", exc_info=True)
            
            # Attendi prima del prossimo controllo
            for _ in range(self.poll_interval):
                if not self.is_running:
                    break
                time.sleep(1)
    
    def _check_feeds(self):
        """Controlla feed e processa nuovi articoli."""
        check_start = datetime.now()
        logger.log_info("🔍 Controllo feed RSS in corso...")
        
        self.stats["last_check"] = check_start.isoformat()
        self.stats["total_checks"] += 1
        
        try:
            # Fetch candidati (limite ragionevole per monitoraggio continuo)
            candidates = self.pipeline.fetcher.fetch_all(limit=20)
            
            if not candidates:
                logger.log_info("Nessun nuovo articolo trovato")
                self._save_stats()
                return
            
            self.stats["total_articles_found"] += len(candidates)
            logger.log_info(f"Trovati {len(candidates)} nuovi articoli, avvio processing...")
            
            # Invia notifica email se configurata
            try:
                from src.email_notifier import get_email_notifier
                email_notifier = get_email_notifier()
                if email_notifier.enabled:
                    # Prepara lista articoli per email
                    articles_for_email = [
                        {
                            "url": c.get("url"),
                            "title": c.get("title", "Nessun titolo"),
                            "source": c.get("source", "Unknown")
                        }
                        for c in candidates[:10]  # Massimo 10 articoli per email
                    ]
                    email_notifier.send_new_articles_notification(articles_for_email)
            except Exception as e:
                logger.log_warning(f"Errore invio notifica email: {e}")
            
            # Processa articoli
            processed_count = 0
            for candidate in candidates:
                if not self.is_running:
                    break
                
                try:
                    result = self.pipeline.process_article(candidate)
                    
                    if result.get("status") == "created":
                        processed_count += 1
                        # Aggiungi agli ultimi articoli processati
                        self.stats["last_articles"].insert(0, {
                            "url": candidate.get("url"),
                            "title": candidate.get("title"),
                            "source": candidate.get("source"),
                            "processed_at": datetime.now().isoformat(),
                            "post_id": result.get("post_id")
                        })
                        # Mantieni solo ultimi 10
                        self.stats["last_articles"] = self.stats["last_articles"][:10]
                        
                except Exception as e:
                    logger.log_error(f"Errore processing articolo {candidate.get('url')}: {e}", exc_info=True)
            
            self.stats["total_articles_processed"] += processed_count
            
            if processed_count > 0:
                logger.log_info(f"✅ Processati {processed_count} nuovi articoli")
            else:
                logger.log_info("Nessun articolo processato (tutti scartati o già esistenti)")
            
        except Exception as e:
            logger.log_error(f"Errore durante controllo feed: {e}", exc_info=True)
        
        self._save_stats()
    
    def _save_stats(self):
        """Salva statistiche su file."""
        try:
            self.stats_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.stats_file, "w", encoding="utf-8") as f:
                json.dump(self.stats, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.log_warning(f"Errore salvataggio statistiche monitor: {e}")
    
    def get_stats(self) -> Dict:
        """Restituisce statistiche correnti."""
        # Carica da file se disponibile
        if self.stats_file.exists():
            try:
                with open(self.stats_file, "r", encoding="utf-8") as f:
                    file_stats = json.load(f)
                    self.stats.update(file_stats)
            except Exception as e:
                logger.log_warning(f"Errore caricamento statistiche: {e}")
        
        return self.stats


# Istanza globale del monitor
_monitor_instance: Optional[FeedMonitor] = None


def get_monitor(poll_interval: int = 300, dry_run: bool = False) -> FeedMonitor:
    """Ottiene o crea istanza globale del monitor."""
    global _monitor_instance
    if _monitor_instance is None:
        _monitor_instance = FeedMonitor(poll_interval=poll_interval, dry_run=dry_run)
    return _monitor_instance
