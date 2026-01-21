"""
Modulo client per WordPress REST API.
Gestisce autenticazione, creazione post, upload media e meta fields.
"""

import os
import time
import base64
from typing import Dict, Optional, List
import requests
from markdown import markdown
from src.logger import get_logger

logger = get_logger()


class WordPressClient:
    """Client per WordPress REST API."""
    
    def __init__(
        self,
        wp_url: str,
        username: Optional[str] = None,
        app_password: Optional[str] = None,
        jwt_token: Optional[str] = None,
        timeout: int = 60
    ):
        """
        Inizializza client WordPress.
        
        Args:
            wp_url: URL base del sito WordPress (es: https://example.com)
            username: Username WordPress (per Application Password)
            app_password: Application Password WordPress
            jwt_token: JWT token alternativo (se usi plugin JWT)
            timeout: Timeout richieste in secondi
        """
        # Normalizza URL (rimuovi trailing slash)
        self.wp_url = wp_url.rstrip("/")
        self.api_base = f"{self.wp_url}/wp-json/wp/v2"
        self.timeout = timeout
        
        # Autenticazione
        self.auth_header = None
        
        if jwt_token:
            self.auth_header = {"Authorization": f"Bearer {jwt_token}"}
        elif username and app_password:
            # Application Password: base64(username:password)
            credentials = f"{username}:{app_password}"
            encoded = base64.b64encode(credentials.encode()).decode()
            self.auth_header = {"Authorization": f"Basic {encoded}"}
        else:
            logger.log_warning("Nessuna autenticazione configurata. Le chiamate potrebbero fallire.")
        
        self.session = requests.Session()
        if self.auth_header:
            self.session.headers.update(self.auth_header)
        
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "NewsPipeline/1.0"
        })
    
    def _make_request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        files: Optional[Dict] = None,
        retries: int = 3
    ) -> requests.Response:
        """
        Esegue richiesta HTTP con retry.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            endpoint: Endpoint API (relativo a api_base)
            data: Dati JSON (se POST/PUT)
            files: File da uploadare (se multipart)
            retries: Numero di retry
        
        Returns:
            Response object
        """
        url = f"{self.api_base}/{endpoint.lstrip('/')}"
        
        for attempt in range(retries):
            try:
                if method.upper() == "GET":
                    response = self.session.get(url, timeout=self.timeout)
                elif method.upper() == "POST":
                    if files:
                        # Upload file: non includere Content-Type JSON
                        headers = {k: v for k, v in self.session.headers.items() if k != "Content-Type"}
                        response = self.session.post(
                            url,
                            data=data,
                            files=files,
                            headers=headers,
                            timeout=self.timeout
                        )
                    else:
                        response = self.session.post(
                            url,
                            json=data,
                            timeout=self.timeout
                        )
                elif method.upper() == "PUT":
                    response = self.session.put(url, json=data, timeout=self.timeout)
                else:
                    raise ValueError(f"Method non supportato: {method}")
                
                response.raise_for_status()
                return response
                
            except requests.exceptions.RequestException as e:
                if attempt < retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.log_warning(
                        f"Richiesta fallita (tentativo {attempt + 1}/{retries}): {e}. "
                        f"Riprovo tra {wait_time}s..."
                    )
                    time.sleep(wait_time)
                else:
                    logger.log_error(f"Richiesta fallita dopo {retries} tentativi: {e}")
                    raise
    
    def get_categories(self) -> List[Dict]:
        """Ottiene lista categorie WordPress."""
        try:
            response = self._make_request("GET", "/categories", retries=1)
            return response.json()
        except Exception as e:
            logger.log_warning(f"Errore nel fetch categorie: {e}")
            return []
    
    def get_or_create_category(self, category_name: str) -> int:
        """
        Ottiene o crea una categoria WordPress.
        
        Args:
            category_name: Nome categoria (slug)
        
        Returns:
            ID categoria WordPress
        """
        # Cerca categoria esistente
        categories = self.get_categories()
        for cat in categories:
            if cat.get("slug") == category_name.lower():
                return cat["id"]
        
        # Crea nuova categoria
        try:
            response = self._make_request(
                "POST",
                "/categories",
                data={"name": category_name, "slug": category_name.lower()}
            )
            new_cat = response.json()
            logger.log_info(f"Categoria creata: {category_name} (ID: {new_cat['id']})")
            return new_cat["id"]
        except Exception as e:
            logger.log_error(f"Errore creazione categoria {category_name}: {e}")
            return 0  # Fallback
    
    def upload_media(self, image_url: str, title: Optional[str] = None) -> Optional[int]:
        """
        Carica un'immagine da URL come media WordPress.
        
        Args:
            image_url: URL dell'immagine
            title: Titolo per il media (opzionale)
        
        Returns:
            ID del media WordPress o None se fallisce
        """
        try:
            # Download immagine
            logger.log_info(f"Download immagine: {image_url}")
            img_response = requests.get(image_url, timeout=30)
            img_response.raise_for_status()
            
            # Determina filename e content type
            filename = image_url.split("/")[-1].split("?")[0]
            if not filename or "." not in filename:
                filename = f"image_{int(time.time())}.jpg"
            
            content_type = img_response.headers.get("Content-Type", "image/jpeg")
            
            # Upload a WordPress
            files = {
                "file": (filename, img_response.content, content_type)
            }
            
            data = {}
            if title:
                data["title"] = title
            
            response = self._make_request(
                "POST",
                "/media",
                data=data,
                files=files
            )
            
            media = response.json()
            media_id = media.get("id")
            logger.log_info(f"Media caricato: {image_url} -> ID {media_id}")
            return media_id
            
        except Exception as e:
            logger.log_error(f"Errore upload media {image_url}: {e}", exc_info=True)
            return None
    
    def markdown_to_html(self, markdown_text: str) -> str:
        """
        Converte Markdown a HTML pulito (senza script).
        
        Args:
            markdown_text: Testo in Markdown
        
        Returns:
            HTML pulito
        """
        html = markdown(
            markdown_text,
            extensions=["extra", "codehilite"],
            output_format="html5"
        )
        
        # Rimuovi eventuali script o iframe (safety check)
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        
        # Rimuovi script, iframe, style tags
        for tag in soup.find_all(["script", "iframe", "style"]):
            tag.decompose()
        
        return str(soup)
    
    def create_post(
        self,
        title: str,
        content_markdown: str,
        excerpt: str,
        category_ids: List[int],
        tags: List[str],
        featured_image_id: Optional[int] = None,
        meta_fields: Optional[Dict] = None,
        status: str = "draft"
    ) -> Optional[int]:
        """
        Crea un post WordPress.
        
        Args:
            title: Titolo del post
            content_markdown: Contenuto in Markdown
            excerpt: Excerpt/lead
            category_ids: Lista ID categorie
            tags: Lista tag (nomi)
            featured_image_id: ID media per featured image
            meta_fields: Campi meta custom
            status: Status post ("draft", "publish", etc.)
        
        Returns:
            ID del post creato o None se fallisce
        """
        try:
            # Converti Markdown a HTML
            content_html = self.markdown_to_html(content_markdown)
            
            # Prepara dati post
            post_data = {
                "title": title,
                "content": content_html,
                "excerpt": excerpt,
                "status": status,
                "categories": category_ids,
                "tags": tags  # WordPress creerà tag se non esistono
            }
            
            if featured_image_id:
                post_data["featured_media"] = featured_image_id
            
            # Crea post
            response = self._make_request("POST", "/posts", data=post_data)
            post = response.json()
            post_id = post.get("id")
            
            logger.log_info(f"Post creato: {title} -> ID {post_id}")
            
            # Aggiorna meta fields se presenti
            if meta_fields and post_id:
                self.update_post_meta(post_id, meta_fields)
            
            return post_id
            
        except Exception as e:
            logger.log_error(f"Errore creazione post: {e}", exc_info=True)
            return None
    
    def update_post_meta(self, post_id: int, meta_fields: Dict):
        """
        Aggiorna meta fields custom di un post.
        
        Args:
            post_id: ID post WordPress
            meta_fields: Dict con meta fields da aggiornare
        """
        try:
            # WordPress REST API richiede meta fields tramite campo "meta"
            response = self._make_request(
                "PUT",
                f"/posts/{post_id}",
                data={"meta": meta_fields}
            )
            logger.log_info(f"Meta fields aggiornati per post {post_id}")
        except Exception as e:
            logger.log_warning(f"Errore aggiornamento meta fields per post {post_id}: {e}")
            # Nota: alcuni WordPress potrebbero richiedere plugin per meta custom
            # In tal caso, usa ACF o Custom Fields plugin
    
    def create_post_from_pipeline(
        self,
        rewritten_data: Dict,
        original_data: Dict,
        quality_result: Dict,
        category_mapping: Dict[str, str]
    ) -> Optional[int]:
        """
        Crea post WordPress completo da dati pipeline.
        
        Args:
            rewritten_data: Dati articolo riscritto
            original_data: Dati articolo originale
            quality_result: Risultato quality gates
            category_mapping: Mapping categoria -> slug WordPress
        
        Returns:
            ID post WordPress o None
        """
        # Mappa categoria
        category = rewritten_data.get("category", "news")
        wp_category_slug = category_mapping.get(category, category_mapping.get("default", "news"))
        category_id = self.get_or_create_category(wp_category_slug)
        
        # Upload featured image se disponibile
        featured_image_id = None
        images = original_data.get("images", [])
        if images:
            featured_image_id = self.upload_media(images[0], title=rewritten_data.get("headline"))
        
        # Prepara meta fields
        meta_fields = {
            "source_name": original_data.get("source_name", ""),
            "source_url": original_data.get("url", ""),
            "source_published_at": original_data.get("published_at", ""),
            "ingest_timestamp": rewritten_data.get("rewritten_at", ""),
            "source_hash": original_data.get("canonical_url", ""),  # Usa canonical_url come hash
            "ai_version": "1.0",
            "risk_level": quality_result.get("risk_level", "low"),
            "needs_review": "1" if quality_result.get("needs_review", False) else "0",
            "original_title": original_data.get("title", "")
        }
        
        # Crea post
        post_id = self.create_post(
            title=rewritten_data.get("headline", ""),
            content_markdown=rewritten_data.get("body_markdown", ""),
            excerpt=rewritten_data.get("lead", ""),
            category_ids=[category_id] if category_id else [],
            tags=rewritten_data.get("tags", []),
            featured_image_id=featured_image_id,
            meta_fields=meta_fields,
            status="draft"  # Sempre draft inizialmente
        )
        
        return post_id
