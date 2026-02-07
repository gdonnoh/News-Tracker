"""
Article deduplication via hash matching and semantic similarity.
"""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    HAS_ML_DEPS = True
except ImportError:
    HAS_ML_DEPS = False
    np = None
    SentenceTransformer = None

from src.logger import get_logger
from src.utils import db_connection, is_postgres, ph

logger = get_logger()


class Deduplicator:
    """Hash-based and semantic-similarity deduplication."""

    def __init__(
        self,
        dedupe_db_path: str = "./data/dedupe.db",
        similarity_threshold: float = 0.85,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
    ):
        self.dedupe_db_path = Path(dedupe_db_path)
        try:
            self.dedupe_db_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.dedupe_db_path = Path("/tmp") / self.dedupe_db_path.name
            self.dedupe_db_path.parent.mkdir(parents=True, exist_ok=True)
        self.similarity_threshold = similarity_threshold

        self._model = None
        self.model_name = model_name

        self._init_db()

    # ------------------------------------------------------------------
    # ML model (lazy-loaded, shared via get_embedding_model())
    # ------------------------------------------------------------------

    def _get_model(self):
        if not HAS_ML_DEPS:
            logger.log_warning("ML dependencies unavailable — deduplication uses hash-only mode")
            return None
        if self._model is None:
            logger.log_info(f"Loading embedding model: {self.model_name}")
            self._model = SentenceTransformer(self.model_name)
        return self._model

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    def _init_db(self):
        blob_type = "BYTEA" if is_postgres() else "BLOB"
        with db_connection(self.dedupe_db_path) as conn:
            conn.execute("""
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
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS article_embeddings (
                    hash_id TEXT PRIMARY KEY,
                    title_embedding {blob_type},
                    body_embedding {blob_type},
                    FOREIGN KEY (hash_id) REFERENCES article_hashes(hash_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_canonical_url ON article_hashes(canonical_url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_title_hash ON article_hashes(title_hash)")

    # ------------------------------------------------------------------
    # Text helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_title(title: str) -> str:
        if not title:
            return ""
        normalised = title.lower().strip()
        for ch in ".,;:!?-_":
            normalised = normalised.replace(ch, " ")
        return " ".join(normalised.split())

    @staticmethod
    def _jaccard(text_a: str, text_b: str) -> float:
        words_a, words_b = set(text_a.split()), set(text_b.split())
        if not words_a or not words_b:
            return 0.0
        return len(words_a & words_b) / len(words_a | words_b)

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _embed(self, text: str):
        model = self._get_model()
        if not model:
            return None
        return model.encode(text[:1000], normalize_embeddings=True)

    @staticmethod
    def _cosine(v1, v2) -> float:
        return float(np.dot(v1, v2))

    # ------------------------------------------------------------------
    # Duplicate check
    # ------------------------------------------------------------------

    def check_duplicate(self, canonical_url: str, title: str, body: Optional[str] = None) -> Dict:
        """
        Check whether an article is a duplicate.

        Returns dict with: is_duplicate, reason, similar_to, wp_post_id, similarity_score
        """
        norm_title = self._normalize_title(title)
        title_hash = self._sha256(norm_title)
        hash_id = self._sha256(f"{canonical_url}|{norm_title}")

        p = ph()
        with db_connection(self.dedupe_db_path) as conn:
            # Check 1: exact hash
            cur = conn.execute(
                f"SELECT hash_id, wp_post_id FROM article_hashes WHERE hash_id = {p}", (hash_id,)
            )
            row = cur.fetchone()
            if row:
                return {"is_duplicate": True, "reason": "exact_hash_match", "similar_to": row[0], "wp_post_id": row[1], "similarity_score": 1.0}

            # Check 2: same canonical URL
            cur = conn.execute(
                f"SELECT hash_id, wp_post_id FROM article_hashes WHERE canonical_url = {p}", (canonical_url,)
            )
            row = cur.fetchone()
            if row:
                return {"is_duplicate": True, "reason": "same_canonical_url", "similar_to": row[0], "wp_post_id": row[1], "similarity_score": 1.0}

            # Check 3: similar title (semantic)
            cur = conn.execute(
                f"SELECT hash_id, wp_post_id, normalized_title FROM article_hashes WHERE title_hash = {p}",
                (title_hash,),
            )
            title_matches = cur.fetchall()

        if title_matches:
            try:
                current_emb = self._embed(norm_title)
                if current_emb is not None:
                    best_sim, best_match = 0.0, None
                    for match_hash, match_wp, match_title in title_matches:
                        if self._jaccard(norm_title, match_title) < self.similarity_threshold * 0.7:
                            continue
                        match_emb = self._embed(match_title)
                        if match_emb is None:
                            continue
                        sim = self._cosine(current_emb, match_emb)
                        if sim > best_sim:
                            best_sim, best_match = sim, (match_hash, match_wp)

                    if best_sim >= self.similarity_threshold:
                        return {
                            "is_duplicate": True,
                            "reason": "semantic_similarity_title",
                            "similar_to": best_match[0],
                            "wp_post_id": best_match[1],
                            "similarity_score": best_sim,
                        }
            except Exception as e:
                logger.log_warning(f"Similarity computation error: {e}")

        return {"is_duplicate": False, "reason": None, "similar_to": None, "similarity_score": None}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register_article(
        self,
        canonical_url: str,
        title: str,
        body: Optional[str] = None,
        wp_post_id: Optional[int] = None,
    ) -> str:
        norm_title = self._normalize_title(title)
        title_hash = self._sha256(norm_title)
        body_hash = self._sha256(body) if body else None
        hash_id = self._sha256(f"{canonical_url}|{norm_title}")
        created_at = datetime.now().isoformat()

        p = ph()
        with db_connection(self.dedupe_db_path) as conn:
            if is_postgres():
                conn.execute(f"""
                    INSERT INTO article_hashes
                    (hash_id, canonical_url, normalized_title, title_hash, body_hash, created_at, wp_post_id)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
                    ON CONFLICT (hash_id) DO UPDATE SET
                        canonical_url = EXCLUDED.canonical_url,
                        normalized_title = EXCLUDED.normalized_title,
                        title_hash = EXCLUDED.title_hash,
                        body_hash = EXCLUDED.body_hash,
                        wp_post_id = EXCLUDED.wp_post_id
                """, (hash_id, canonical_url, norm_title, title_hash, body_hash, created_at, wp_post_id))
            else:
                conn.execute(f"""
                    INSERT OR REPLACE INTO article_hashes
                    (hash_id, canonical_url, normalized_title, title_hash, body_hash, created_at, wp_post_id)
                    VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p})
                """, (hash_id, canonical_url, norm_title, title_hash, body_hash, created_at, wp_post_id))

        logger.log_info(f"Article registered: {hash_id}")
        return hash_id
