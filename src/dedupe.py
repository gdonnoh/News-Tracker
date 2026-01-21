"""
Modulo per deduplicazione articoli.
Calcola hash e similarità semantica per evitare doppioni.
"""

import hashlib
import sqlite3
from pathlib import Path
from typing import Dict, Optional, Tuple
try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    HAS_ML_DEPS = True
except ImportError:
    HAS_ML_DEPS = False
    # Fallback per ambienti senza ML dependencies
    np = None
    SentenceTransformer = None

from src.logger import get_logger

logger = get_logger()


class Deduplicator:
    """Gestisce deduplicazione basata su hash e similarità semantica."""
    
    def __init__(
        self,
        dedupe_db_path: str = "./data/dedupe.db",
        similarity_threshold: float = 0.85,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"
    ):
        self.dedupe_db_path = Path(dedupe_db_path)
        self.dedupe_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.similarity_threshold = similarity_threshold
        
        # Carica modello per embeddings (lazy loading)
        self._model = None
        self.model_name = model_name
        
        # Inizializza DB
        self._init_db()
    
    def _get_model(self):
        """Lazy loading del modello di embeddings."""
        if not HAS_ML_DEPS:
            logger.log_warning("ML dependencies non disponibili, deduplicazione basata solo su hash")
            return None
        
        if self._model is None:
            logger.log_info(f"Caricamento modello embeddings: {self.model_name}")
            self._model = SentenceTransformer(self.model_name)
        return self._model
    
    def _init_db(self):
        """Inizializza database per deduplicazione."""
        conn = sqlite3.connect(str(self.dedupe_db_path))
        cursor = conn.cursor()
        
        # Tabella per hash articoli
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS article_hashes (
                hash_id TEXT PRIMARY KEY,
                canonical_url TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                title_hash TEXT NOT NULL,
                body_hash TEXT,
                created_at TEXT NOT NULL,
                wp_post_id INTEGER
            )
        """)
        
        # Tabella per embeddings (opzionale, per similarità)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS article_embeddings (
                hash_id TEXT PRIMARY KEY,
                title_embedding BLOB,
                body_embedding BLOB,
                FOREIGN KEY (hash_id) REFERENCES article_hashes(hash_id)
            )
        """)
        
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_canonical_url ON article_hashes(canonical_url)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_title_hash ON article_hashes(title_hash)")
        
        conn.commit()
        conn.close()
    
    def _normalize_title(self, title: str) -> str:
        """Normalizza titolo per confronto."""
        if not title:
            return ""
        # Lowercase, rimuovi punteggiatura extra, normalizza spazi
        normalized = title.lower().strip()
        # Rimuovi caratteri speciali comuni
        for char in ".,;:!?-_":
            normalized = normalized.replace(char, " ")
        # Normalizza spazi multipli
        normalized = " ".join(normalized.split())
        return normalized
    
    def _compute_hash(self, text: str) -> str:
        """Calcola hash SHA256 di un testo."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()
    
    def _compute_embedding(self, text: str):
        """Calcola embedding semantico del testo."""
        model = self._get_model()
        if not model:
            return None  # ML dependencies non disponibili
        
        # Limita lunghezza per performance
        text = text[:1000] if len(text) > 1000 else text
        embedding = model.encode(text, normalize_embeddings=True)
        return embedding
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Calcola similarità coseno tra due vettori."""
        return float(np.dot(vec1, vec2))
    
    def check_duplicate(
        self,
        canonical_url: str,
        title: str,
        body: Optional[str] = None
    ) -> Dict:
        """
        Verifica se un articolo è duplicato.
        
        Args:
            canonical_url: URL canonico normalizzato
            title: Titolo dell'articolo
            body: Corpo dell'articolo (opzionale)
        
        Returns:
            Dict con:
            - is_duplicate: bool
            - reason: str (se duplicato)
            - similar_to: str (hash_id o wp_post_id se trovato simile)
            - similarity_score: float (se calcolato)
        """
        normalized_title = self._normalize_title(title)
        title_hash = self._compute_hash(normalized_title)
        
        # Hash combinato URL + titolo
        combined = f"{canonical_url}|{normalized_title}"
        hash_id = self._compute_hash(combined)
        
        conn = sqlite3.connect(str(self.dedupe_db_path))
        cursor = conn.cursor()
        
        # Controllo 1: Hash esatto
        cursor.execute(
            "SELECT hash_id, wp_post_id FROM article_hashes WHERE hash_id = ?",
            (hash_id,)
        )
        exact_match = cursor.fetchone()
        if exact_match:
            conn.close()
            return {
                "is_duplicate": True,
                "reason": "exact_hash_match",
                "similar_to": exact_match[0],
                "wp_post_id": exact_match[1],
                "similarity_score": 1.0
            }
        
        # Controllo 2: Stesso URL canonico
        cursor.execute(
            "SELECT hash_id, wp_post_id FROM article_hashes WHERE canonical_url = ?",
            (canonical_url,)
        )
        url_match = cursor.fetchone()
        if url_match:
            conn.close()
            return {
                "is_duplicate": True,
                "reason": "same_canonical_url",
                "similar_to": url_match[0],
                "wp_post_id": url_match[1],
                "similarity_score": 1.0
            }
        
        # Controllo 3: Titolo molto simile (hash collision o variante)
        cursor.execute(
            "SELECT hash_id, wp_post_id, normalized_title FROM article_hashes WHERE title_hash = ?",
            (title_hash,)
        )
        title_matches = cursor.fetchall()
        
        if title_matches:
            # Verifica similarità semantica se disponibile
            try:
                current_title_emb = self._compute_embedding(normalized_title)
                
                if current_title_emb is None:
                    # ML dependencies non disponibili, salta controllo semantico
                    pass
                else:
                    # Carica embeddings esistenti o calcola al volo
                    best_similarity = 0.0
                    best_match = None
                    
                    for match_hash_id, match_wp_id, match_title in title_matches:
                        # Calcola embedding del match (o carica da DB se salvato)
                        match_title_emb = self._compute_embedding(match_title)
                        if match_title_emb is None:
                            continue
                        similarity = self._cosine_similarity(current_title_emb, match_title_emb)
                        
                        if similarity > best_similarity:
                            best_similarity = similarity
                            best_match = (match_hash_id, match_wp_id)
                    
                    if best_similarity >= self.similarity_threshold:
                        conn.close()
                        return {
                            "is_duplicate": True,
                            "reason": "semantic_similarity_title",
                            "similar_to": best_match[0],
                            "wp_post_id": best_match[1],
                            "similarity_score": best_similarity
                        }
            except Exception as e:
                logger.log_warning(f"Errore nel calcolo similarità: {e}")
        
        conn.close()
        
        return {
            "is_duplicate": False,
            "reason": None,
            "similar_to": None,
            "similarity_score": None
        }
    
    def register_article(
        self,
        canonical_url: str,
        title: str,
        body: Optional[str] = None,
        wp_post_id: Optional[int] = None
    ) -> str:
        """
        Registra un articolo nel database di deduplicazione.
        
        Args:
            canonical_url: URL canonico
            title: Titolo
            body: Corpo (opzionale)
            wp_post_id: ID post WordPress (opzionale)
        
        Returns:
            hash_id dell'articolo registrato
        """
        normalized_title = self._normalize_title(title)
        title_hash = self._compute_hash(normalized_title)
        body_hash = self._compute_hash(body) if body else None
        
        combined = f"{canonical_url}|{normalized_title}"
        hash_id = self._compute_hash(combined)
        
        from datetime import datetime
        created_at = datetime.now().isoformat()
        
        conn = sqlite3.connect(str(self.dedupe_db_path))
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT OR REPLACE INTO article_hashes
            (hash_id, canonical_url, normalized_title, title_hash, body_hash, created_at, wp_post_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (hash_id, canonical_url, normalized_title, title_hash, body_hash, created_at, wp_post_id))
        
        # Opzionalmente salva embeddings (per performance future)
        # Per ora skip per non appesantire DB
        
        conn.commit()
        conn.close()
        
        logger.log_info(f"Articolo registrato: {hash_id}")
        return hash_id
