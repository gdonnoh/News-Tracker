"""
Modulo per quality gates prima della pubblicazione.
Controlla similarità, coerenza, policy e rischio.
"""

import re
from typing import Dict, List, Tuple
from sentence_transformers import SentenceTransformer
import numpy as np
from src.logger import get_logger

logger = get_logger()


class QualityGates:
    """Controlli qualità per articoli prima della pubblicazione."""
    
    def __init__(
        self,
        similarity_threshold: float = 0.85,
        min_length: int = 200,
        max_length: int = 2000,
        model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"
    ):
        self.similarity_threshold = similarity_threshold
        self.min_length = min_length
        self.max_length = max_length
        
        # Lazy loading modello
        self._model = None
        self.model_name = model_name
        
        # Parole chiave per policy check
        self.risk_keywords = {
            "high": [
                "diffamazione", "calunnia", "hate speech", "incitamento",
                "dati sensibili", "codice fiscale", "numero carta", "password"
            ],
            "medium": [
                "gossip", "scandalo", "polemica", "controversia"
            ]
        }
    
    def _get_model(self):
        """Lazy loading modello embeddings."""
        if self._model is None:
            try:
                logger.log_info(f"Caricamento modello per quality gates: {self.model_name}")
                self._model = SentenceTransformer(self.model_name)
            except Exception as e:
                logger.log_error(f"Errore caricamento modello embeddings: {e}")
                raise
        return self._model
    
    def _count_words(self, text: str) -> int:
        """Conta parole in un testo."""
        return len(text.split())
    
    def _check_similarity(
        self,
        original_text: str,
        rewritten_text: str
    ) -> Tuple[bool, float]:
        """
        Verifica similarità tra testo originale e riscritto.
        
        Returns:
            (is_too_similar, similarity_score)
        """
        try:
            model = self._get_model()
            
            # Limita lunghezza per performance
            orig_limited = original_text[:2000]
            rew_limited = rewritten_text[:2000]
            
            orig_emb = model.encode(orig_limited, normalize_embeddings=True)
            rew_emb = model.encode(rew_limited, normalize_embeddings=True)
            
            similarity = float(np.dot(orig_emb, rew_emb))
            
            is_too_similar = similarity >= self.similarity_threshold
            
            return is_too_similar, similarity
            
        except Exception as e:
            logger.log_warning(f"Errore calcolo similarità: {e}")
            return False, 0.0
    
    def _check_sanity(self, rewritten_data: Dict) -> List[str]:
        """
        Controlli di sanità mentale sul contenuto riscritto.
        
        Returns:
            Lista di issue trovati (vuota se tutto ok)
        """
        issues = []
        
        body = rewritten_data.get("body_markdown", "")
        headline = rewritten_data.get("headline", "")
        lead = rewritten_data.get("lead", "")
        
        # Controllo lunghezza
        word_count = rewritten_data.get("word_count", 0)
        if word_count < self.min_length:
            issues.append(f"Articolo troppo corto: {word_count} parole (min: {self.min_length})")
        if word_count > self.max_length:
            issues.append(f"Articolo troppo lungo: {word_count} parole (max: {self.max_length})")
        
        # Controllo presenza contenuto
        if not body or len(body.strip()) < 100:
            issues.append("Corpo articolo vuoto o troppo corto")
        
        if not headline or len(headline.strip()) < 10:
            issues.append("Headline vuota o troppo corta")
        
        if not lead or len(lead.strip()) < 20:
            issues.append("Lead vuoto o troppo corto")
        
        # Controllo ripetizioni eccessive
        body_lower = body.lower()
        words = body_lower.split()
        if len(words) > 0:
            word_freq = {}
            for word in words:
                if len(word) > 4:  # Solo parole significative
                    word_freq[word] = word_freq.get(word, 0) + 1
            
            # Controlla se qualche parola appare troppo spesso
            max_freq = max(word_freq.values()) if word_freq else 0
            if max_freq > len(words) * 0.1:  # Più del 10% delle parole
                issues.append("Ripetizioni eccessive nel contenuto")
        
        # Controllo presenza script/tag HTML pericolosi
        dangerous_patterns = [
            r"<script",
            r"<iframe",
            r"javascript:",
            r"onclick=",
            r"onerror="
        ]
        
        for pattern in dangerous_patterns:
            if re.search(pattern, body, re.IGNORECASE):
                issues.append(f"Contenuto contiene pattern pericoloso: {pattern}")
        
        return issues
    
    def _check_policy(self, rewritten_data: Dict, original_data: Dict) -> Tuple[str, List[str]]:
        """
        Controlla policy e rischio contenuto.
        
        Returns:
            (risk_level, issues)
            risk_level: "low", "medium", "high"
        """
        issues = []
        risk_level = "low"
        
        body = rewritten_data.get("body_markdown", "").lower()
        headline = rewritten_data.get("headline", "").lower()
        lead = rewritten_data.get("lead", "").lower()
        
        combined_text = f"{headline} {lead} {body}"
        
        # Controllo keyword ad alto rischio
        for keyword in self.risk_keywords.get("high", []):
            if keyword.lower() in combined_text:
                issues.append(f"Contenuto ad alto rischio: keyword '{keyword}' trovata")
                risk_level = "high"
        
        # Controllo keyword a medio rischio (solo se non già high)
        if risk_level != "high":
            for keyword in self.risk_keywords.get("medium", []):
                if keyword.lower() in combined_text:
                    issues.append(f"Contenuto a medio rischio: keyword '{keyword}' trovata")
                    if risk_level == "low":
                        risk_level = "medium"
        
        # Controllo dati sensibili (pattern regex)
        sensitive_patterns = [
            r"\b\d{16}\b",  # Carte di credito
            r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b",  # Codice fiscale IT
            r"\b\d{4}\s?\d{4}\s?\d{4}\s?\d{4}\b",  # Carte formato con spazi
        ]
        
        for pattern in sensitive_patterns:
            if re.search(pattern, combined_text):
                issues.append(f"Dati sensibili rilevati: pattern {pattern}")
                risk_level = "high"
        
        return risk_level, issues
    
    def check(
        self,
        original_data: Dict,
        rewritten_data: Dict
    ) -> Dict:
        """
        Esegue tutti i controlli qualità.
        
        Args:
            original_data: Dati articolo originale (da extract_article)
            rewritten_data: Dati articolo riscritto (da rewrite)
        
        Returns:
            Dict con:
            - ok: bool (True se passa tutti i gate)
            - issues: List[str] (problemi trovati)
            - risk_level: "low" | "medium" | "high"
            - similarity_score: float
            - needs_review: bool (True se richiede revisione umana)
        """
        all_issues = []
        
        # Gate 1: Similarity check
        original_text = original_data.get("text", "")
        rewritten_text = rewritten_data.get("body_markdown", "")
        
        is_too_similar, similarity_score = self._check_similarity(original_text, rewritten_text)
        if is_too_similar:
            all_issues.append(
                f"Testo troppo simile all'originale (similarity: {similarity_score:.2f})"
            )
        
        # Gate 2: Sanity check
        sanity_issues = self._check_sanity(rewritten_data)
        all_issues.extend(sanity_issues)
        
        # Gate 3: Policy check
        risk_level, policy_issues = self._check_policy(rewritten_data, original_data)
        all_issues.extend(policy_issues)
        
        # Determina se passa
        ok = len(all_issues) == 0 and risk_level != "high"
        
        # Determina se serve revisione
        needs_review = (
            risk_level == "medium" or
            risk_level == "high" or
            is_too_similar or
            len(sanity_issues) > 0
        )
        
        result = {
            "ok": ok,
            "issues": all_issues,
            "risk_level": risk_level,
            "similarity_score": similarity_score,
            "needs_review": needs_review
        }
        
        if ok:
            logger.log_info(f"Quality gates PASSED (similarity: {similarity_score:.2f}, risk: {risk_level})")
        else:
            logger.log_warning(
                f"Quality gates FAILED: {len(all_issues)} issues, risk: {risk_level}"
            )
        
        return result
