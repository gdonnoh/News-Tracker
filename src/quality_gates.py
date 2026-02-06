"""
Quality gates for articles before publication.
Checks similarity, sanity, and policy compliance.
"""

import re
from typing import Dict, List, Tuple

try:
    import numpy as np
    from sentence_transformers import SentenceTransformer
    HAS_ML_DEPS = True
except ImportError:
    HAS_ML_DEPS = False
    np = None
    SentenceTransformer = None

from src.logger import get_logger

logger = get_logger()

# Pre-compile dangerous-content patterns once
_DANGEROUS_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in (r"<script", r"<iframe", r"javascript:", r"onclick=", r"onerror=")
]

# Pre-compile sensitive-data patterns once
_SENSITIVE_PATTERNS = [
    re.compile(r"\b\d{16}\b"),                                          # Credit card
    re.compile(r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b"),       # Italian fiscal code
    re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\b"),                  # Card with spaces
]


class QualityGates:
    """Pre-publication quality checks: similarity, sanity, policy."""

    def __init__(
        self,
        similarity_threshold: float = 0.85,
        min_length: int = 200,
        max_length: int = 2000,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2",
    ):
        self.similarity_threshold = similarity_threshold
        self.min_length = min_length
        self.max_length = max_length

        self._model = None
        self.model_name = model_name

        self.risk_keywords = {
            "high": [
                "diffamazione", "calunnia", "hate speech", "incitamento",
                "dati sensibili", "codice fiscale", "numero carta", "password",
            ],
            "medium": [
                "gossip", "scandalo", "polemica", "controversia",
            ],
        }

    # ------------------------------------------------------------------
    # Embedding model (lazy)
    # ------------------------------------------------------------------

    def _get_model(self):
        if not HAS_ML_DEPS:
            logger.log_warning("ML dependencies unavailable — quality gates use fallback")
            return None
        if self._model is None:
            logger.log_info(f"Loading quality-gate embedding model: {self.model_name}")
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def set_model(self, model):
        """Allow sharing a pre-loaded model (e.g. from Deduplicator)."""
        self._model = model

    # ------------------------------------------------------------------
    # Similarity
    # ------------------------------------------------------------------

    @staticmethod
    def _jaccard(text_a: str, text_b: str) -> float:
        w1, w2 = set(text_a.lower().split()), set(text_b.lower().split())
        if not w1 or not w2:
            return 0.0
        return len(w1 & w2) / len(w1 | w2)

    def _check_similarity(self, original: str, rewritten: str) -> Tuple[bool, float]:
        try:
            orig, rew = original[:2000], rewritten[:2000]

            quick = self._jaccard(orig, rew)
            if quick < self.similarity_threshold * 0.7:
                return False, quick

            model = self._get_model()
            if not model:
                logger.log_info("Using Jaccard fallback for similarity (no ML)")
                return quick >= self.similarity_threshold, quick

            orig_emb = model.encode(orig, normalize_embeddings=True)
            rew_emb = model.encode(rew, normalize_embeddings=True)
            similarity = float(np.dot(orig_emb, rew_emb))
            return similarity >= self.similarity_threshold, similarity
        except Exception as e:
            logger.log_warning(f"Similarity check error: {e}")
            return False, 0.0

    # ------------------------------------------------------------------
    # Sanity
    # ------------------------------------------------------------------

    @staticmethod
    def _count_words(text: str) -> int:
        return len(text.split())

    def _check_sanity(self, rewritten: Dict, min_len: int, max_len: int) -> List[str]:
        issues: List[str] = []
        body = rewritten.get("body_markdown", "")
        headline = rewritten.get("headline", "")
        lead = rewritten.get("lead", "")
        wc = rewritten.get("word_count", 0)

        if wc < min_len:
            issues.append(f"Article too short: {wc} words (min: {min_len})")
        if wc > max_len:
            issues.append(f"Article too long: {wc} words (max: {max_len})")
        if not body or len(body.strip()) < 100:
            issues.append("Body empty or too short")
        if not headline or len(headline.strip()) < 10:
            issues.append("Headline empty or too short")
        if not lead or len(lead.strip()) < 20:
            issues.append("Lead empty or too short")

        # Excessive repetition
        words = body.lower().split()
        if words:
            freq: Dict[str, int] = {}
            for w in words:
                if len(w) > 4:
                    freq[w] = freq.get(w, 0) + 1
            if freq and max(freq.values()) > len(words) * 0.1:
                issues.append("Excessive word repetition in content")

        # Dangerous HTML/JS
        for pattern in _DANGEROUS_PATTERNS:
            if pattern.search(body):
                issues.append(f"Dangerous pattern detected: {pattern.pattern}")

        return issues

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------

    def _check_policy(self, rewritten: Dict, original: Dict) -> Tuple[str, List[str]]:
        issues: List[str] = []
        risk = "low"

        combined = " ".join([
            rewritten.get("headline", ""),
            rewritten.get("lead", ""),
            rewritten.get("body_markdown", ""),
        ]).lower()

        for kw in self.risk_keywords.get("high", []):
            if kw.lower() in combined:
                issues.append(f"High-risk keyword: '{kw}'")
                risk = "high"

        if risk != "high":
            for kw in self.risk_keywords.get("medium", []):
                if kw.lower() in combined:
                    issues.append(f"Medium-risk keyword: '{kw}'")
                    if risk == "low":
                        risk = "medium"

        for pattern in _SENSITIVE_PATTERNS:
            if pattern.search(combined):
                issues.append(f"Sensitive data detected: {pattern.pattern}")
                risk = "high"

        return risk, issues

    # ------------------------------------------------------------------
    # Main gate
    # ------------------------------------------------------------------

    def check(self, original_data: Dict, rewritten_data: Dict) -> Dict:
        """
        Run all quality gates.

        Returns dict with: ok, issues, risk_level, similarity_score, needs_review
        """
        all_issues: List[str] = []
        original_text = original_data.get("text", "")
        rewritten_text = rewritten_data.get("body_markdown", "")

        # Adaptive length bounds based on original
        owc = self._count_words(original_text) if original_text else 0
        if owc > 0:
            if owc < self.min_length:
                min_len = max(140, int(owc * 0.6))
                max_len = max(260, int(owc * 1.5))
            else:
                min_len = max(self.min_length, int(owc * 0.7))
                max_len = max(self.max_length, int(owc * 1.4))
        else:
            min_len, max_len = self.min_length, self.max_length

        # Gate 1: Similarity
        is_too_similar, similarity_score = self._check_similarity(original_text, rewritten_text)
        if is_too_similar:
            all_issues.append(f"Too similar to original (similarity: {similarity_score:.2f})")

        # Gate 2: Sanity
        all_issues.extend(self._check_sanity(rewritten_data, min_len, max_len))

        # Gate 3: Policy
        risk_level, policy_issues = self._check_policy(rewritten_data, original_data)
        all_issues.extend(policy_issues)

        ok = len(all_issues) == 0 and risk_level != "high"
        needs_review = risk_level in ("medium", "high") or is_too_similar or bool(all_issues)

        result = {
            "ok": ok,
            "issues": all_issues,
            "risk_level": risk_level,
            "similarity_score": similarity_score,
            "needs_review": needs_review,
        }

        if ok:
            logger.log_info(f"Quality gates PASSED (similarity: {similarity_score:.2f}, risk: {risk_level})")
        else:
            logger.log_warning(f"Quality gates FAILED: {len(all_issues)} issues, risk: {risk_level}")

        return result
