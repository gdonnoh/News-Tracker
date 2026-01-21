"""
Modulo per estrazione contenuto da URL.
Usa readability-lxml per estrarre testo principale e metadata.
Salva HTML raw e JSON estratto per debug.
"""

import json
import time
from pathlib import Path
from typing import Dict, Optional, List
from urllib.parse import urljoin, urlparse
import requests
from bs4 import BeautifulSoup
from readability import Document
from dateutil import parser as date_parser
from src.logger import get_logger

logger = get_logger()


class ArticleExtractor:
    """Estrae contenuto e metadata da URL."""
    
    def __init__(
        self,
        cache_dir: str = "./data/cache",
        timeout: int = 30,
        rate_limit_delay: float = 2.0
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
    
    def _normalize_url(self, url: str) -> str:
        """Normalizza URL rimuovendo parametri comuni."""
        parsed = urlparse(url)
        # Rimuovi parametri tracking comuni
        query_params = []
        for param in parsed.query.split("&"):
            if param and not any(track in param.lower() for track in ["utm_", "ref=", "source=", "fbclid="]):
                query_params.append(param)
        
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if query_params:
            normalized += "?" + "&".join(query_params)
        return normalized
    
    def _extract_images(self, soup: BeautifulSoup, base_url: str) -> List[str]:
        """Estrae immagini candidate, preferendo hero/featured."""
        images = []
        
        # Cerca meta tags per immagine principale
        for meta in soup.find_all("meta", property=lambda x: x and "image" in x.lower()):
            content = meta.get("content")
            if content:
                images.append(urljoin(base_url, content))
        
        # Cerca og:image
        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            images.append(urljoin(base_url, og_image["content"]))
        
        # Cerca immagini nel contenuto principale
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if src:
                full_url = urljoin(base_url, src)
                # Filtra immagini troppo piccole o decorative
                width = img.get("width")
                height = img.get("height")
                if width and height:
                    try:
                        if int(width) < 200 or int(height) < 200:
                            continue
                    except ValueError:
                        pass
                
                # Filtra icone, logo, etc.
                alt = (img.get("alt") or "").lower()
                if any(skip in alt for skip in ["icon", "logo", "avatar", "button"]):
                    continue
                
                images.append(full_url)
        
        # Rimuovi duplicati mantenendo ordine
        seen = set()
        unique_images = []
        for img in images:
            if img not in seen:
                seen.add(img)
                unique_images.append(img)
        
        return unique_images[:5]  # Max 5 immagini
    
    def _extract_publish_date(self, soup: BeautifulSoup) -> Optional[str]:
        """Estrae data di pubblicazione da vari meta tag."""
        # Cerca meta tags comuni
        date_selectors = [
            ('meta', {'property': 'article:published_time'}),
            ('meta', {'property': 'og:published_time'}),
            ('meta', {'name': 'date'}),
            ('meta', {'name': 'publishdate'}),
            ('time', {'itemprop': 'datePublished'}),
            ('time', {'datetime': True}),
        ]
        
        for tag_name, attrs in date_selectors:
            elements = soup.find_all(tag_name, attrs)
            for elem in elements:
                date_str = elem.get("content") or elem.get("datetime") or elem.get_text()
                if date_str:
                    try:
                        parsed_date = date_parser.parse(date_str)
                        return parsed_date.isoformat()
                    except (ValueError, TypeError):
                        continue
        
        return None
    
    def extract(self, url: str, source_name: Optional[str] = None) -> Dict:
        """
        Estrae contenuto e metadata da un URL.
        
        Args:
            url: URL dell'articolo
            source_name: Nome della fonte (opzionale)
        
        Returns:
            Dict con:
            - url: URL originale
            - canonical_url: URL canonico normalizzato
            - title: Titolo originale
            - text: Testo principale pulito
            - html: HTML estratto (solo contenuto principale)
            - images: Lista URL immagini candidate
            - published_at: Data pubblicazione ISO
            - author: Autore se presente
            - raw_html_path: Path del file HTML raw salvato
            - extracted_json_path: Path del file JSON estratto
        """
        start_time = time.time()
        
        try:
            # Rate limiting
            time.sleep(self.rate_limit_delay)
            
            # Download HTML
            logger.log_info(f"Downloading: {url}")
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()
            
            html_content = response.text
            base_url = response.url
            
            # Salva HTML raw per debug
            url_hash = url.replace("://", "_").replace("/", "_").replace("?", "_")[:100]
            raw_html_path = self.cache_dir / f"raw_{url_hash}.html"
            raw_html_path.write_text(html_content, encoding="utf-8")
            
            # Parse con BeautifulSoup
            soup = BeautifulSoup(html_content, "lxml")
            
            # Estrai titolo
            title = None
            if soup.title:
                title = soup.title.get_text().strip()
            
            # Cerca anche h1 o og:title
            if not title or len(title) < 10:
                h1 = soup.find("h1")
                if h1:
                    title = h1.get_text().strip()
            
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()
            
            # Usa readability per estrarre contenuto principale
            doc = Document(html_content)
            main_html = doc.summary()
            main_soup = BeautifulSoup(main_html, "lxml")
            
            # Estrai testo pulito
            text = main_soup.get_text(separator="\n", strip=True)
            
            # Estrai immagini
            images = self._extract_images(soup, base_url)
            
            # Estrai data pubblicazione
            published_at = self._extract_publish_date(soup)
            
            # Estrai autore (se presente)
            author = None
            author_selectors = [
                ('meta', {'name': 'author'}),
                ('meta', {'property': 'article:author'}),
                ('span', {'class': lambda x: x and 'author' in x.lower()}),
                ('a', {'rel': 'author'}),
            ]
            
            for tag_name, attrs in author_selectors:
                elem = soup.find(tag_name, attrs)
                if elem:
                    author = elem.get("content") or elem.get_text()
                    if author:
                        author = author.strip()
                        break
            
            # Normalizza URL
            canonical_url = self._normalize_url(url)
            
            # Prepara risultato
            result = {
                "url": url,
                "canonical_url": canonical_url,
                "title": title or "",
                "text": text,
                "html": main_html,
                "images": images,
                "published_at": published_at,
                "author": author,
                "source_name": source_name,
                "raw_html_path": str(raw_html_path),
                "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S")
            }
            
            # Salva JSON estratto per debug
            extracted_json_path = self.cache_dir / f"extracted_{url_hash}.json"
            extracted_json_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
            result["extracted_json_path"] = str(extracted_json_path)
            
            elapsed = time.time() - start_time
            logger.log_info(f"Estratto: {url} ({len(text)} caratteri, {elapsed:.2f}s)")
            
            return result
            
        except Exception as e:
            logger.log_error(f"Errore estrazione {url}: {e}", exc_info=True)
            raise
