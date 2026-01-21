"""
Modulo per riscrittura articoli con LLM.
Produce output strutturato JSON con headline, lead, body, tags, etc.
Include guardrail per evitare invenzioni e mantenere solo informazioni originali.
"""

import json
import os
from typing import Dict, Optional
from src.logger import get_logger

logger = get_logger()


class ArticleRewriter:
    """Riscrive articoli usando LLM con output strutturato."""
    
    def __init__(
        self,
        provider: str = "openai",  # "openai" o "anthropic"
        model: Optional[str] = None,
        api_key: Optional[str] = None
    ):
        self.provider = provider.lower()
        self.model = model or self._get_default_model()
        self.api_key = api_key or self._get_api_key()
        
        if not self.api_key:
            logger.log_warning("API key non configurata. Il rewrite sarà uno stub.")
    
    def _get_default_model(self) -> str:
        """Ottiene modello di default dal provider."""
        if self.provider == "openai":
            return os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        elif self.provider == "anthropic":
            return os.getenv("ANTHROPIC_MODEL", "claude-3-haiku-20240307")
        return "gpt-4o-mini"
    
    def _get_api_key(self) -> Optional[str]:
        """Ottiene API key da environment."""
        if self.provider == "openai":
            return os.getenv("OPENAI_API_KEY")
        elif self.provider == "anthropic":
            return os.getenv("ANTHROPIC_API_KEY")
        return None
    
    def _build_prompt(self, extracted_data: Dict) -> str:
        """
        Costruisce prompt per LLM con guardrail.
        
        Args:
            extracted_data: Dati estratti dall'articolo originale
        
        Returns:
            Prompt completo per LLM
        """
        title = extracted_data.get("title", "")
        text = extracted_data.get("text", "")
        author = extracted_data.get("author", "")
        published_at = extracted_data.get("published_at", "")
        
        prompt = f"""Sei un giornalista professionista. DEVI riscrivere questo articolo in modo ESTREMAMENTE originale e diverso dall'originale, mantenendo solo i fatti verificabili.

⚠️ REGOLA FONDAMENTALE: La riscrittura DEVE essere quasi irriconoscibile rispetto all'originale, ma contenere gli stessi fatti.

ISTRUZIONI PER RISCITTURA RADICALE:

1. **CAMBIAMENTO STRUTTURALE OBBLIGATORIO:**
   - Se l'originale inizia con un fatto, tu inizia con il contesto o le conseguenze
   - Se l'originale usa cronologia diretta, usa flashback o analisi tematica
   - Riorganizza completamente l'ordine delle informazioni
   - Crea una nuova struttura narrativa completamente diversa

2. **RIFORMULAZIONE ESTREMA:**
   - NON copiare frasi, nemmeno parzialmente
   - Usa sinonimi, perifrasi, costruzioni grammaticali diverse
   - Cambia attivo/passivo, ordine soggetto-verbo-oggetto
   - Riscrivi ogni concetto con parole completamente diverse
   - Esempio: "Il presidente ha annunciato" → "È stato reso noto dall'ufficio presidenziale"

3. **PROSPETTIVA DIVERSA:**
   - Se l'originale è in terza persona, considera un approccio più analitico
   - Cambia il punto di vista: da macro a micro o viceversa
   - Inserisci collegamenti con contesto più ampio quando possibile

4. **STILE E TONO:**
   - Mantieni informativo e professionale
   - Usa un registro linguistico leggermente diverso (più formale o più accessibile)
   - Varia la lunghezza delle frasi rispetto all'originale
   - Usa figure retoriche diverse (metafore, analogie, esempi concreti)

5. **CONTENUTO:**
   - USA SOLO fatti presenti nell'articolo originale
   - NON inventare dati, numeri, citazioni o dettagli
   - Se un'informazione manca, ometti completamente
   - Mantieni la veridicità ma cambia completamente la presentazione

6. **STRUTTURA OUTPUT:**
   - Lead: 2-3 frasi che catturano l'essenza con approccio diverso
   - Corpo: paragrafi tematici con sottotitoli (##) che organizzano diversamente
   - Evita ripetizioni e liste eccessive
   - Lunghezza: 400-800 parole (adatta se fonte più corta)

7. **QUALITÀ:**
   - Il testo deve risultare scritto da zero, non una parafrasi
   - Un lettore non deve riconoscere che proviene dallo stesso articolo
   - Mantieni coerenza logica e chiarezza informativa

ARTICOLO ORIGINALE (usa solo come fonte di fatti, NON come modello di scrittura):
Titolo: {title}
Autore: {author if author else "non specificato"}
Data: {published_at if published_at else "non specificata"}

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
        return prompt
    
    def rewrite(self, extracted_data: Dict) -> Dict:
        """
        Riscrive un articolo usando LLM.
        
        Args:
            extracted_data: Dati estratti dall'articolo originale
        
        Returns:
            Dict con struttura:
            {
                "headline": str,
                "lead": str,
                "body_markdown": str,
                "tags": List[str],
                "category": str,
                "meta_title": str,
                "meta_description": str,
                "word_count": int,
                "rewritten_at": str
            }
        """
        if not self.api_key:
            # Stub mode: restituisce struttura vuota
            logger.log_warning("LLM API key non configurata. Usando stub.")
            return self._stub_rewrite(extracted_data)
        
        try:
            prompt = self._build_prompt(extracted_data)
            
            if self.provider == "openai":
                return self._rewrite_openai(prompt, extracted_data)
            elif self.provider == "anthropic":
                return self._rewrite_anthropic(prompt, extracted_data)
            else:
                logger.log_error(f"Provider LLM non supportato: {self.provider}")
                return self._stub_rewrite(extracted_data)
                
        except Exception as e:
            logger.log_error(f"Errore nella riscrittura: {e}", exc_info=True)
            # Fallback a stub in caso di errore
            return self._stub_rewrite(extracted_data)
    
    def _rewrite_openai(self, prompt: str, extracted_data: Dict) -> Dict:
        """Riscrive usando OpenAI API."""
        try:
            from openai import OpenAI
            client = OpenAI(api_key=self.api_key)
            
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Sei un giornalista esperto che riscrive articoli in modo estremamente originale. Rispondi SOLO con JSON valido, senza testo aggiuntivo. La riscrittura deve essere quasi irriconoscibile rispetto all'originale."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.9,  # Aumentato per maggiore creatività e originalità
                response_format={"type": "json_object"}
            )
            
            content = response.choices[0].message.content
            result = json.loads(content)
            
            # Valida e completa struttura
            return self._validate_and_complete(result, extracted_data)
            
        except ImportError:
            logger.log_error("OpenAI library non installata. Installa con: pip install openai")
            return self._stub_rewrite(extracted_data)
        except Exception as e:
            logger.log_error(f"Errore chiamata OpenAI: {e}", exc_info=True)
            return self._stub_rewrite(extracted_data)
    
    def _rewrite_anthropic(self, prompt: str, extracted_data: Dict) -> Dict:
        """Riscrive usando Anthropic Claude API."""
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self.api_key)
            
            message = client.messages.create(
                model=self.model,
                max_tokens=4000,
                temperature=0.9,  # Aumentato per maggiore creatività e originalità
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )
            
            content = message.content[0].text
            # Estrai JSON dal contenuto (potrebbe avere testo prima/dopo)
            json_start = content.find("{")
            json_end = content.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                json_str = content[json_start:json_end]
                result = json.loads(json_str)
            else:
                raise ValueError("Nessun JSON trovato nella risposta")
            
            return self._validate_and_complete(result, extracted_data)
            
        except ImportError:
            logger.log_error("Anthropic library non installata. Installa con: pip install anthropic")
            return self._stub_rewrite(extracted_data)
        except Exception as e:
            logger.log_error(f"Errore chiamata Anthropic: {e}", exc_info=True)
            return self._stub_rewrite(extracted_data)
    
    def _validate_and_complete(self, result: Dict, extracted_data: Dict) -> Dict:
        """Valida e completa struttura risultato."""
        import time
        
        # Assicura che tutti i campi richiesti siano presenti
        validated = {
            "headline": result.get("headline", extracted_data.get("title", "")),
            "lead": result.get("lead", ""),
            "body_markdown": result.get("body_markdown", ""),
            "tags": result.get("tags", []),
            "category": result.get("category", "news"),
            "meta_title": result.get("meta_title", result.get("headline", "")),
            "meta_description": result.get("meta_description", result.get("lead", "")),
            "rewritten_at": time.strftime("%Y-%m-%dT%H:%M:%S")
        }
        
        # Calcola word count
        body_text = validated["body_markdown"].replace("#", "").replace("*", "")
        validated["word_count"] = len(body_text.split())
        
        return validated
    
    def _stub_rewrite(self, extracted_data: Dict) -> Dict:
        """
        Stub per riscrittura quando LLM non disponibile.
        Restituisce struttura valida ma con contenuto minimo.
        """
        import time
        
        title = extracted_data.get("title", "")
        text = extracted_data.get("text", "")
        
        # Estrai prime frasi come lead
        sentences = text.split(". ")[:3]
        lead = ". ".join(sentences) + "." if sentences else text[:200]
        
        # Usa testo originale come body (limitato)
        body = text[:2000] if len(text) > 2000 else text
        
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
            "stub_mode": True  # Flag per indicare che è uno stub
        }
