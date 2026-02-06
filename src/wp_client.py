"""
WordPress REST API client.
Handles authentication, post creation, media upload, and meta fields.
"""

import base64
import time
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup
from markdown import markdown

from src.logger import get_logger

logger = get_logger()


class WordPressClient:
    """Client for the WordPress REST API (v2)."""

    def __init__(
        self,
        wp_url: str,
        username: Optional[str] = None,
        app_password: Optional[str] = None,
        jwt_token: Optional[str] = None,
        timeout: int = 60,
    ):
        self.wp_url = wp_url.rstrip("/")
        self.api_base = f"{self.wp_url}/wp-json/wp/v2"
        self.timeout = timeout

        # Authentication
        auth_header = None
        if jwt_token:
            auth_header = {"Authorization": f"Bearer {jwt_token}"}
        elif username and app_password:
            encoded = base64.b64encode(f"{username}:{app_password}".encode()).decode()
            auth_header = {"Authorization": f"Basic {encoded}"}
        else:
            logger.log_warning("No authentication configured — API calls may fail.")

        self.session = requests.Session()
        if auth_header:
            self.session.headers.update(auth_header)
        self.session.headers.update({
            "Content-Type": "application/json",
            "User-Agent": "NewsPipeline/1.0",
        })

    # ------------------------------------------------------------------
    # HTTP helper with retry
    # ------------------------------------------------------------------

    def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        files: Optional[Dict] = None,
        retries: int = 3,
    ) -> requests.Response:
        url = f"{self.api_base}/{endpoint.lstrip('/')}"

        for attempt in range(retries):
            try:
                if method.upper() == "GET":
                    resp = self.session.get(url, timeout=self.timeout)
                elif method.upper() == "POST":
                    if files:
                        headers = {k: v for k, v in self.session.headers.items() if k != "Content-Type"}
                        resp = self.session.post(url, data=data, files=files, headers=headers, timeout=self.timeout)
                    else:
                        resp = self.session.post(url, json=data, timeout=self.timeout)
                elif method.upper() == "PUT":
                    resp = self.session.put(url, json=data, timeout=self.timeout)
                else:
                    raise ValueError(f"Unsupported HTTP method: {method}")

                resp.raise_for_status()
                return resp

            except requests.exceptions.RequestException as e:
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    logger.log_warning(f"Request failed (attempt {attempt + 1}/{retries}): {e}. Retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    logger.log_error(f"Request failed after {retries} attempts: {e}")
                    raise

    # ------------------------------------------------------------------
    # Categories
    # ------------------------------------------------------------------

    def get_categories(self) -> List[Dict]:
        try:
            return self._request("GET", "/categories", retries=1).json()
        except Exception as e:
            logger.log_warning(f"Failed to fetch categories: {e}")
            return []

    def get_or_create_category(self, name: str) -> int:
        for cat in self.get_categories():
            if cat.get("slug") == name.lower():
                return cat["id"]
        try:
            resp = self._request("POST", "/categories", data={"name": name, "slug": name.lower()})
            new_id = resp.json()["id"]
            logger.log_info(f"Category created: {name} (ID: {new_id})")
            return new_id
        except Exception as e:
            logger.log_error(f"Category creation error ({name}): {e}")
            return 0

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------

    def upload_media(self, image_url: str, title: Optional[str] = None) -> Optional[int]:
        try:
            logger.log_info(f"Downloading image: {image_url}")
            img_resp = requests.get(image_url, timeout=30)
            img_resp.raise_for_status()

            filename = image_url.split("/")[-1].split("?")[0]
            if not filename or "." not in filename:
                filename = f"image_{int(time.time())}.jpg"

            content_type = img_resp.headers.get("Content-Type", "image/jpeg")
            files = {"file": (filename, img_resp.content, content_type)}
            data = {"title": title} if title else {}

            resp = self._request("POST", "/media", data=data, files=files)
            media_id = resp.json().get("id")
            logger.log_info(f"Media uploaded: {image_url} -> ID {media_id}")
            return media_id
        except Exception as e:
            logger.log_error(f"Media upload error ({image_url}): {e}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Markdown -> HTML
    # ------------------------------------------------------------------

    @staticmethod
    def markdown_to_html(md_text: str) -> str:
        html = markdown(md_text, extensions=["extra", "codehilite"], output_format="html5")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup.find_all(["script", "iframe", "style"]):
            tag.decompose()
        return str(soup)

    # ------------------------------------------------------------------
    # Post creation
    # ------------------------------------------------------------------

    def create_post(
        self,
        title: str,
        content_markdown: str,
        excerpt: str,
        category_ids: List[int],
        tags: List[str],
        featured_image_id: Optional[int] = None,
        meta_fields: Optional[Dict] = None,
        status: str = "draft",
    ) -> Optional[int]:
        try:
            post_data = {
                "title": title,
                "content": self.markdown_to_html(content_markdown),
                "excerpt": excerpt,
                "status": status,
                "categories": category_ids,
                "tags": tags,
            }
            if featured_image_id:
                post_data["featured_media"] = featured_image_id

            resp = self._request("POST", "/posts", data=post_data)
            post_id = resp.json().get("id")
            logger.log_info(f"Post created: {title} -> ID {post_id}")

            if meta_fields and post_id:
                self._update_meta(post_id, meta_fields)

            return post_id
        except Exception as e:
            logger.log_error(f"Post creation error: {e}", exc_info=True)
            return None

    def _update_meta(self, post_id: int, meta_fields: Dict):
        try:
            self._request("PUT", f"/posts/{post_id}", data={"meta": meta_fields})
            logger.log_info(f"Meta fields updated for post {post_id}")
        except Exception as e:
            logger.log_warning(f"Meta update error for post {post_id}: {e}")

    # ------------------------------------------------------------------
    # Pipeline convenience method
    # ------------------------------------------------------------------

    def create_post_from_pipeline(
        self,
        rewritten_data: Dict,
        original_data: Dict,
        quality_result: Dict,
        category_mapping: Dict[str, str],
    ) -> Optional[int]:
        category = rewritten_data.get("category", "news")
        wp_slug = category_mapping.get(category, category_mapping.get("default", "news"))
        category_id = self.get_or_create_category(wp_slug)

        featured_image_id = None
        images = original_data.get("images", [])
        if images:
            featured_image_id = self.upload_media(images[0], title=rewritten_data.get("headline"))

        meta_fields = {
            "source_name": original_data.get("source_name", ""),
            "source_url": original_data.get("url", ""),
            "source_published_at": original_data.get("published_at", ""),
            "ingest_timestamp": rewritten_data.get("rewritten_at", ""),
            "source_hash": original_data.get("canonical_url", ""),
            "ai_version": "1.0",
            "risk_level": quality_result.get("risk_level", "low"),
            "needs_review": "1" if quality_result.get("needs_review", False) else "0",
            "original_title": original_data.get("title", ""),
        }

        return self.create_post(
            title=rewritten_data.get("headline", ""),
            content_markdown=rewritten_data.get("body_markdown", ""),
            excerpt=rewritten_data.get("lead", ""),
            category_ids=[category_id] if category_id else [],
            tags=rewritten_data.get("tags", []),
            featured_image_id=featured_image_id,
            meta_fields=meta_fields,
            status="draft",
        )
