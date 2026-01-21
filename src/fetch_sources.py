"""
Modulo per raccolta URL da feed RSS e whitelist domini.
Gestisce rate limiting, timeout e persistenza URL già processati.
"""

import time
import hashlib
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional
from urllib.parse import urlparse
import feedparser
import requests
from dateutil import parser as date_parser
from src.logger import get_logger

logger = get_logger()


class SourceFetcher:
    """Gestisce la raccolta di URL da feed RSS e whitelist."""
    
    def __init__(
        self,
        sources_config: Dict,
        dedupe_db_path: str = "./data/dedupe.db",
        rate_limit_delay: float = 6.0,
        timeout: int = 30
    ):
        self.sources_config = sources_config
        self.dedupe_db_path = Path(dedupe_db_path)
        self.dedupe_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.rate_limit_delay = rate_limit_delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (compatible; NewsPipeline/1.0; +https://example.com/bot)"
        })
        
        # Inizializza DB per tracking URL processati
        self._init_db()
        
        # Callback per aggiornare stato (se disponibile)
        self.status_callback = None
    
    def set_status_callback(self, callback):
        """Imposta callback per aggiornare stato."""
        self.status_callback = callback
    
    def _update_status_if_available(self, step, message):
        """Aggiorna stato se callback disponibile."""
        if self.status_callback:
            try:
                self.status_callback(step, message)
            except:
                pass
    
    def _init_db(self):
        """Inizializza database SQLite per tracking URL."""
        conn = sqlite3.connect(str(self.dedupe_db_path))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_urls (
                url_hash TEXT PRIMARY KEY,
                url TEXT UNIQUE NOT NULL,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                processed BOOLEAN DEFAULT 0
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_url ON processed_urls(url)
        """)
        conn.commit()
        conn.close()
    
    def _is_url_processed(self, url: str) -> bool:
        """Verifica se un URL è già stato processato."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        conn = sqlite3.connect(str(self.dedupe_db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT processed FROM processed_urls WHERE url_hash = ?", (url_hash,))
        result = cursor.fetchone()
        conn.close()
        return result is not None and result[0] == 1
    
    def _mark_url_seen(self, url: str, processed: bool = False):
        """Marca un URL come visto (non necessariamente processato)."""
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        now = datetime.now().isoformat()
        conn = sqlite3.connect(str(self.dedupe_db_path))
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO processed_urls 
            (url_hash, url, first_seen_at, last_seen_at, processed)
            VALUES (?, ?, 
                COALESCE((SELECT first_seen_at FROM processed_urls WHERE url_hash = ?), ?),
                ?, ?)
        """, (url_hash, url, url_hash, now, now, 1 if processed else 0))
        conn.commit()
        conn.close()
    
    def _is_domain_allowed(self, url: str) -> bool:
        """Verifica se il dominio dell'URL è nella whitelist."""
        whitelist_config = self.sources_config.get("whitelist_domains", {})
        
        if not whitelist_config.get("enabled", False):
            return True  # Se whitelist disabilitata, accetta tutto
        
        allowed_domains = whitelist_config.get("domains", [])
        if not allowed_domains:
            return True
        
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        
        # Rimuovi www. per confronto
        if domain.startswith("www."):
            domain = domain[4:]
        
        # Controlla dominio esatto o sottodomini
        for allowed in allowed_domains:
            allowed = allowed.lower().replace("www.", "")
            if domain == allowed or domain.endswith(f".{allowed}"):
                return True
        
        return False
    
    def fetch_rss_feeds(self) -> List[Dict]:
        """
        Scarica e parsa tutti i feed RSS abilitati.
        
        Returns:
            Lista di candidati: [{"url": "...", "source": "...", "published_at": "...", "title": "..."}]
        """
        candidates = []
        feeds = self.sources_config.get("rss_feeds", [])
        
        for feed_config in feeds:
            if not feed_config.get("enabled", True):
                continue
            
            feed_url = feed_config["url"]
            source_name = feed_config.get("name", feed_url)
            
            try:
                logger.log_info(f"Fetching RSS feed: {source_name}")
                self._update_status_if_available("fetching", f"Scaricando feed: {source_name}")
                
                # Rate limiting
                time.sleep(self.rate_limit_delay)
                
                # Download feed
                response = self.session.get(feed_url, timeout=self.timeout)
                response.raise_for_status()
                
                # Parse feed
                feed = feedparser.parse(response.content)
                
                entry_count = len(feed.entries)
                logger.log_info(f"Trovati {entry_count} articoli nel feed RSS '{source_name}'")
                self._update_status_if_available("fetching", f"✅ Trovati {entry_count} articoli nel feed '{source_name}'")
                
                # Contatori per statistiche
                skipped_whitelist = 0
                skipped_processed = 0
                skipped_old = 0
                added = 0
                
                for entry in feed.entries:
                    url = entry.get("link", "")
                    if not url:
                        continue
                    
                    # Verifica whitelist
                    if not self._is_domain_allowed(url):
                        skipped_whitelist += 1
                        continue
                    
                    # Verifica se già processato
                    if self._is_url_processed(url):
                        skipped_processed += 1
                        continue
                    
                    # Estrai metadata
                    published_at = None
                    published_datetime = None
                    if hasattr(entry, "published_parsed") and entry.published_parsed:
                        published_datetime = datetime(*entry.published_parsed[:6])
                        published_at = published_datetime.isoformat()
                    elif hasattr(entry, "published"):
                        published_at = entry.published
                        try:
                            # Prova a parsare la data
                            published_datetime = date_parser.parse(published_at)
                        except (ValueError, TypeError):
                            pass
                    
                    # Filtra articoli più vecchi di 2 giorni
                    if published_datetime:
                        two_days_ago = datetime.now() - timedelta(days=2)
                        if published_datetime < two_days_ago:
                            skipped_old += 1
                            continue
                    elif published_at:
                        # Se abbiamo solo la stringa, prova a parsarla
                        try:
                            published_datetime = date_parser.parse(published_at)
                            two_days_ago = datetime.now() - timedelta(days=2)
                            if published_datetime < two_days_ago:
                                skipped_old += 1
                                continue
                        except (ValueError, TypeError):
                            # Se non riusciamo a parsare, accettiamo l'articolo (meglio includere che escludere)
                            pass
                    
                    candidate = {
                        "url": url,
                        "source": source_name,
                        "published_at": published_at,
                        "title": entry.get("title", ""),
                        "description": entry.get("description", "")
                    }
                    
                    candidates.append(candidate)
                    added += 1
                    self._mark_url_seen(url, processed=False)
                
                # Log statistiche finali per questo feed
                logger.log_info(
                    f"Feed '{source_name}': {added} candidati aggiunti "
                    f"(scartati: {skipped_processed} già processati, {skipped_old} troppo vecchi, {skipped_whitelist} fuori whitelist)"
                )
                
            except requests.exceptions.HTTPError as e:
                if e.response.status_code == 404:
                    error_msg = f"⚠️ Feed non disponibile: {source_name} (404)"
                    logger.log_warning(error_msg)
                    self._update_status_if_available("fetching", error_msg)
                else:
                    error_msg = f"❌ Errore feed {source_name}: {e.response.status_code}"
                    logger.log_error(error_msg)
                    self._update_status_if_available("fetching", error_msg)
                continue
            except Exception as e:
                error_msg = f"❌ Errore nel fetch feed {source_name}: {str(e)[:100]}"
                logger.log_error(f"Errore nel fetch feed {source_name}: {e}", exc_info=True)
                self._update_status_if_available("fetching", error_msg)
                continue
        
        return candidates
    
    def fetch_all(self, limit: Optional[int] = None) -> List[Dict]:
        """
        Raccoglie tutti i candidati da tutte le fonti.
        
        Args:
            limit: Limite massimo di candidati da restituire
        
        Returns:
            Lista di candidati ordinati per data (più recenti prima), distribuiti tra le fonti
        """
        all_candidates = []
        
        # Fetch RSS feeds
        rss_candidates = self.fetch_rss_feeds()
        all_candidates.extend(rss_candidates)
        
        # Qui si potrebbero aggiungere altre fonti (whitelist URL diretti, etc.)
        
        # Se c'è un limite, distribuisci gli articoli tra le fonti
        if limit and limit > 0:
            # Raggruppa per fonte
            by_source = {}
            for candidate in all_candidates:
                source = candidate.get("source", "Unknown")
                if source not in by_source:
                    by_source[source] = []
                by_source[source].append(candidate)
            
            # Ordina ogni gruppo per data (più recenti prima)
            for source in by_source:
                by_source[source].sort(
                    key=lambda x: x.get("published_at") or "",
                    reverse=True
                )
            
            # Prendi articoli in modo round-robin dalle fonti
            distributed = []
            sources_list = list(by_source.keys())
            max_per_source = (limit // len(sources_list)) + 1 if sources_list else limit
            
            for i in range(limit):
                source_idx = i % len(sources_list) if sources_list else 0
                source = sources_list[source_idx]
                
                if by_source[source] and len([c for c in distributed if c.get("source") == source]) < max_per_source:
                    distributed.append(by_source[source].pop(0))
                
                # Se una fonte è finita, rimuovila
                if not by_source[source]:
                    sources_list.remove(source)
                    if not sources_list:
                        break
            
            # Ordina risultato finale per data
            distributed.sort(
                key=lambda x: x.get("published_at") or "",
                reverse=True
            )
            
            all_candidates = distributed
        else:
            # Senza limite, ordina tutti per data
            all_candidates.sort(
                key=lambda x: x.get("published_at") or "",
                reverse=True
            )
        
        # Filtra ulteriormente per sicurezza (in caso alcuni siano sfuggiti)
        filtered_candidates = []
        two_days_ago = datetime.now() - timedelta(days=2)
        skipped_old = 0
        
        for candidate in all_candidates:
            published_at = candidate.get("published_at")
            if published_at:
                try:
                    if isinstance(published_at, str):
                        published_datetime = date_parser.parse(published_at)
                    elif isinstance(published_at, datetime):
                        published_datetime = published_at
                    else:
                        published_datetime = None
                    
                    if published_datetime and published_datetime < two_days_ago:
                        skipped_old += 1
                        continue
                except (ValueError, TypeError):
                    # Se non riusciamo a parsare, includiamo l'articolo
                    pass
            
            filtered_candidates.append(candidate)
        
        if skipped_old > 0:
            logger.log_info(f"Filtrati {skipped_old} articoli più vecchi di 2 giorni")
        
        logger.log_info(f"Totale candidati raccolti: {len(filtered_candidates)}")
        return filtered_candidates
