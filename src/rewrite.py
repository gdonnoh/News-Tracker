"""
LLM-powered article rewriter.
Produces structured JSON output (headline, lead, body, tags, category).
Supports OpenAI and Anthropic providers with stub fallback.
"""

import json
import os
import time
from typing import Dict, Optional

from src.logger import get_logger

logger = get_logger()


class ArticleRewriter:
    """Rewrites articles using LLM with structured JSON output."""

    def __init__(
        self,
        provider: str = "openai",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.provider = provider.lower()
        self.model = model or self._default_model()
        self.api_key = api_key or self._env_api_key()
        self._client = None  # Cached LLM client

        if not self.api_key:
            logger.log_warning("API key not configured — rewrite will use stub mode.")

    # ------------------------------------------------------------------
    # Provider config
    # ------------------------------------------------------------------

    def _default_model(self) -> str:
        if self.provider == "openai":
            return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        if self.provider == "anthropic":
            return os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
        return "gpt-4o-mini"

    def _env_api_key(self) -> Optional[str]:
        if self.provider == "openai":
            return os.getenv("OPENAI_API_KEY")
        if self.provider == "anthropic":
            return os.getenv("ANTHROPIC_API_KEY")
        return None

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(extracted_data: Dict) -> str:
        title = extracted_data.get("title", "")
        text = extracted_data.get("text", "")
        author = extracted_data.get("author", "")
        published_at = extracted_data.get("published_at", "")

        word_count = len(text.split())
        if word_count < 250:
            target_min = max(140, int(word_count * 0.6))
            target_max = max(260, int(word_count * 1.4))
        else:
            target_min = max(180, int(word_count * 0.7))
            target_max = max(300, int(word_count * 1.3))

        return f"""Sei un giornalista professionista. Devi riscrivere questo articolo in modo originale e professionale, mantenendo ESATTAMENTE i fatti verificabili.

ISTRUZIONI CHIAVE:

1. **CAMBIAMENTO STRUTTURALE:**
   - Riorganizza l'ordine delle informazioni
   - Usa una struttura tematica con sottotitoli
   - Evita di replicare la stessa sequenza dei paragrafi originali

2. **RIFORMULAZIONE:**
   - NON copiare frasi
   - Usa sinonimi e perifrasi
   - Cambia attivo/passivo e costruzioni grammaticali
   - Riscrivi ogni concetto con parole diverse

3. **PROSPETTIVA E CONTESTO:**
   - Introduci contesto o implicazioni se gia presenti nel testo
   - Mantieni tono informativo, senza opinioni nuove

4. **STILE E TONO:**
   - Informativo e professionale
   - Frasi di lunghezza variabile
   - Niente enfasi sensazionalistiche

5. **CONTENUTO E FATTI:**
   - USA SOLO fatti presenti nell'articolo originale
   - NON inventare dati, numeri, citazioni o dettagli
   - NOMI PROPRI, DATE E NUMERI devono restare identici
   - Se un'informazione manca, omettila

6. **STRUTTURA OUTPUT:**
   - Lead: 2-3 frasi che catturano l'essenza con approccio diverso
   - Corpo: paragrafi tematici con sottotitoli (##)
   - Evita ripetizioni e liste eccessive
   - Lunghezza target: {target_min}-{target_max} parole (adatta alla fonte)

7. **QUALITÀ:**
   - Il testo deve risultare scritto da zero
   - Coerenza logica e chiarezza informativa

ARTICOLO ORIGINALE (usa solo come fonte di fatti, NON come modello di scrittura):
Titolo: {title}
Autore: {author or "non specificato"}
Data: {published_at or "non specificata"}

Testo originale:
{text[:5000]}

Rispondi SOLO con un JSON valido (nessun testo aggiuntivo):
{{
  "headline": "Titolo completamente nuovo e originale (max 100 caratteri, NON simile all'originale)",
  "lead": "2-3 frasi di introduzione con approccio diverso dall'originale",
  "body_markdown": "Corpo riscritto in Markdown con struttura completamente diversa, paragrafi tematici e sottotitoli (##). Ogni frase deve essere riscritta da zero.",
  "tags": ["tag1", "tag2", "tag3"],
  "category": "categoria principale (tecnologia, politica, economia, sport, cultura, salute, cronaca)",
  "meta_title": "Titolo SEO originale (max 60 caratteri)",
  "meta_description": "Descrizione SEO originale (max 160 caratteri)"
}}
"""

    # ------------------------------------------------------------------
    # Rewrite dispatch
    # ------------------------------------------------------------------

    def rewrite(self, extracted_data: Dict) -> Dict:
        if not self.api_key:
            logger.log_warning("LLM API key not configured — using stub.")
            return self._stub_rewrite(extracted_data)

        try:
            prompt = self._build_prompt(extracted_data)
            if self.provider == "openai":
                return self._rewrite_openai(prompt, extracted_data)
            if self.provider == "anthropic":
                return self._rewrite_anthropic(prompt, extracted_data)
            logger.log_error(f"Unsupported LLM provider: {self.provider}")
            return self._stub_rewrite(extracted_data)
        except Exception as e:
            logger.log_error(f"Rewrite error: {e}", exc_info=True)
            return self._stub_rewrite(extracted_data)

    # ------------------------------------------------------------------
    # Provider implementations
    # ------------------------------------------------------------------

    def _get_openai_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def _get_anthropic_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
        return self._client

    def _rewrite_openai(self, prompt: str, extracted_data: Dict) -> Dict:
        try:
            client = self._get_openai_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Sei un giornalista esperto che riscrive articoli in modo estremamente originale. Rispondi SOLO con JSON valido, senza testo aggiuntivo. La riscrittura deve essere quasi irriconoscibile rispetto all'originale."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.7,
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)
            return self._validate(result, extracted_data)
        except ImportError:
            logger.log_error("OpenAI library not installed. Run: pip install openai")
            return self._stub_rewrite(extracted_data)
        except Exception as e:
            logger.log_error(f"OpenAI call error: {e}", exc_info=True)
            return self._stub_rewrite(extracted_data)

    def _rewrite_anthropic(self, prompt: str, extracted_data: Dict) -> Dict:
        try:
            client = self._get_anthropic_client()
            message = client.messages.create(
                model=self.model,
                max_tokens=4000,
                temperature=0.7,
                messages=[{"role": "user", "content": prompt}],
            )
            content = message.content[0].text
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                result = json.loads(content[json_start:json_end])
            else:
                raise ValueError("No JSON found in Anthropic response")
            return self._validate(result, extracted_data)
        except ImportError:
            logger.log_error("Anthropic library not installed. Run: pip install anthropic")
            return self._stub_rewrite(extracted_data)
        except Exception as e:
            logger.log_error(f"Anthropic call error: {e}", exc_info=True)
            return self._stub_rewrite(extracted_data)

    # ------------------------------------------------------------------
    # Validation / fallback
    # ------------------------------------------------------------------

    @staticmethod
    def _validate(result: Dict, extracted_data: Dict) -> Dict:
        validated = {
            "headline": result.get("headline", extracted_data.get("title", "")),
            "lead": result.get("lead", ""),
            "body_markdown": result.get("body_markdown", ""),
            "tags": result.get("tags", []),
            "category": result.get("category", "news"),
            "meta_title": result.get("meta_title", result.get("headline", "")),
            "meta_description": result.get("meta_description", result.get("lead", "")),
            "rewritten_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        body_text = validated["body_markdown"].replace("#", "").replace("*", "")
        validated["word_count"] = len(body_text.split())
        return validated

    @staticmethod
    def _stub_rewrite(extracted_data: Dict) -> Dict:
        title = extracted_data.get("title", "")
        text = extracted_data.get("text", "")

        sentences = text.split(". ")[:3]
        lead = (". ".join(sentences) + ".") if sentences else text[:200]
        body = text[:2000]

        return {
            "headline": title[:100],
            "lead": lead[:300],
            "body_markdown": body,
            "tags": [],
            "category": "news",
            "meta_title": title[:60],
            "meta_description": lead[:160],
            "word_count": len(body.split()),
            "rewritten_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "stub_mode": True,
        }
