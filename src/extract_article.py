"""
Article content extractor using readability-lxml.
Downloads HTML, extracts main text and metadata, optionally caches raw/extracted data.
"""

import json
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from readability import Document
from dateutil import parser as date_parser

from src.logger import get_logger
from src.utils import url_to_hash

logger = get_logger()


class ArticleExtractor:
    """Extracts content and metadata from article URLs."""

    def __init__(
        self,
        cache_dir: str = "./data/cache",
        timeout: int = 30,
        rate_limit_delay: float = 2.0,
        save_raw_html: bool = True,
        save_extracted_json: bool = True,
    ):
        self.cache_dir = Path(cache_dir)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            self.cache_dir = Path("/tmp/cache")
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self.save_raw_html = save_raw_html
        self.save_extracted_json = save_extracted_json

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })

    # ------------------------------------------------------------------
    # URL normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Strip common tracking parameters from a URL."""
        parsed = urlparse(url)
        tracking_prefixes = ("utm_", "ref=", "source=", "fbclid=")
        clean_params = [
            p for p in parsed.query.split("&")
            if p and not any(t in p.lower() for t in tracking_prefixes)
        ]
        normalised = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if clean_params:
            normalised += "?" + "&".join(clean_params)
        return normalised

    # ------------------------------------------------------------------
    # Metadata extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_images(soup: BeautifulSoup, base_url: str) -> List[str]:
        """Return up to 5 candidate images, preferring og:image and hero images."""
        images: List[str] = []

        # Meta image tags
        for meta in soup.find_all("meta", property=lambda x: x and "image" in x.lower()):
            content = meta.get("content")
            if content:
                images.append(urljoin(base_url, content))

        og_image = soup.find("meta", property="og:image")
        if og_image and og_image.get("content"):
            images.append(urljoin(base_url, og_image["content"]))

        # Content images (skip small / decorative)
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if not src:
                continue
            full_url = urljoin(base_url, src)

            # Skip images declared as tiny
            width, height = img.get("width"), img.get("height")
            if width and height:
                try:
                    if int(width) < 200 or int(height) < 200:
                        continue
                except ValueError:
                    pass

            alt = (img.get("alt") or "").lower()
            if any(skip in alt for skip in ("icon", "logo", "avatar", "button")):
                continue

            images.append(full_url)

        # Deduplicate preserving order
        seen: set = set()
        unique = []
        for url in images:
            if url not in seen:
                seen.add(url)
                unique.append(url)
        return unique[:5]

    @staticmethod
    def _extract_publish_date(soup: BeautifulSoup) -> Optional[str]:
        selectors = [
            ("meta", {"property": "article:published_time"}),
            ("meta", {"property": "og:published_time"}),
            ("meta", {"name": "date"}),
            ("meta", {"name": "publishdate"}),
            ("time", {"itemprop": "datePublished"}),
            ("time", {"datetime": True}),
        ]
        for tag_name, attrs in selectors:
            for elem in soup.find_all(tag_name, attrs):
                raw = elem.get("content") or elem.get("datetime") or elem.get_text()
                if raw:
                    try:
                        return date_parser.parse(raw).isoformat()
                    except (ValueError, TypeError):
                        continue
        return None

    @staticmethod
    def _extract_author(soup: BeautifulSoup) -> Optional[str]:
        selectors = [
            ("meta", {"name": "author"}),
            ("meta", {"property": "article:author"}),
            ("span", {"class": lambda x: x and "author" in x.lower()}),
            ("a", {"rel": "author"}),
        ]
        for tag_name, attrs in selectors:
            elem = soup.find(tag_name, attrs)
            if elem:
                author = (elem.get("content") or elem.get_text() or "").strip()
                if author:
                    return author
        return None

    @staticmethod
    def _detect_paywall(html: str, soup: BeautifulSoup) -> bool:
        paywall_keywords = [
            "contenuto per gli abbonati", "contenuto riservato agli abbonati",
            "abbonati per continuare a leggere", "abbonati per leggere",
            "abbonati per continuare", "accedi per continuare",
            "solo per abbonati", "contenuto premium",
            "articolo riservato", "abbonamento necessario", "iscriviti per leggere",
        ]
        lower_html = html.lower()
        if any(kw in lower_html for kw in paywall_keywords):
            return True

        paywall_selectors = [
            ("div", {"class": lambda x: x and "paywall" in x.lower()}),
            ("div", {"id": lambda x: x and "paywall" in x.lower()}),
            ("section", {"class": lambda x: x and "paywall" in x.lower()}),
        ]
        return any(soup.find(tag, attrs) for tag, attrs in paywall_selectors)

    # ------------------------------------------------------------------
    # Main extraction
    # ------------------------------------------------------------------

    def extract(self, url: str, source_name: Optional[str] = None) -> Dict:
        """
        Download and extract content + metadata from an article URL.

        Returns a dict with keys: url, canonical_url, title, text, html, images,
        published_at, author, is_paywalled, source_name, raw_html_path,
        extracted_json_path, extracted_at.
        """
        start_time = time.time()

        try:
            time.sleep(self.rate_limit_delay)

            logger.log_info(f"Downloading: {url}")
            response = self.session.get(url, timeout=self.timeout)
            response.raise_for_status()

            html_content = response.text
            base_url = response.url
            file_hash = url_to_hash(url)

            # Save raw HTML
            raw_html_path = self.cache_dir / f"raw_{file_hash}.html"
            if self.save_raw_html:
                raw_html_path.write_text(html_content, encoding="utf-8")

            soup = BeautifulSoup(html_content, "lxml")

            # Title extraction (prefer og:title > h1 > <title>)
            title = None
            if soup.title:
                title = soup.title.get_text().strip()
            if not title or len(title) < 10:
                h1 = soup.find("h1")
                if h1:
                    title = h1.get_text().strip()
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                title = og_title["content"].strip()

            # Main content via readability
            doc = Document(html_content)
            main_html = doc.summary()
            main_soup = BeautifulSoup(main_html, "lxml")
            text = main_soup.get_text(separator="\n", strip=True)

            result = {
                "url": url,
                "canonical_url": self._normalize_url(url),
                "title": title or "",
                "text": text,
                "html": main_html,
                "images": self._extract_images(soup, base_url),
                "published_at": self._extract_publish_date(soup),
                "author": self._extract_author(soup),
                "is_paywalled": self._detect_paywall(html_content, soup),
                "source_name": source_name,
                "raw_html_path": str(raw_html_path) if self.save_raw_html else None,
                "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }

            # Save extracted JSON
            if self.save_extracted_json:
                extracted_path = self.cache_dir / f"extracted_{file_hash}.json"
                extracted_path.write_text(
                    json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8"
                )
                result["extracted_json_path"] = str(extracted_path)
            else:
                result["extracted_json_path"] = None

            elapsed = time.time() - start_time
            logger.log_info(f"Extracted: {url} ({len(text)} chars, {elapsed:.2f}s)")
            return result

        except Exception as e:
            logger.log_error(f"Extraction error {url}: {e}", exc_info=True)
            raise
