"""
Modulo per invio notifiche email quando vengono trovati nuovi articoli.
Supporta Resend (consigliato per Vercel), SendGrid e SMTP standard.
"""

import os
from typing import List, Dict, Optional
from src.logger import get_logger

logger = get_logger()


class EmailNotifier:
    """Gestisce invio notifiche email."""
    
    def __init__(self):
        """Inizializza notificatore email."""
        self.enabled = os.getenv("EMAIL_NOTIFICATIONS_ENABLED", "false").lower() == "true"
        self.provider = os.getenv("EMAIL_PROVIDER", "resend").lower()
        self.recipient = os.getenv("EMAIL_RECIPIENT", "")
        self.from_email = os.getenv("EMAIL_FROM", "noreply@example.com")
        
        if not self.enabled:
            logger.log_info("Notifiche email disabilitate")
            return
        
        if not self.recipient:
            logger.log_warning("EMAIL_RECIPIENT non configurato, notifiche email disabilitate")
            self.enabled = False
            return
        
        # Inizializza provider specifico
        if self.provider == "resend":
            self.api_key = os.getenv("RESEND_API_KEY")
            if not self.api_key:
                logger.log_warning("RESEND_API_KEY non configurato")
                self.enabled = False
        elif self.provider == "sendgrid":
            self.api_key = os.getenv("SENDGRID_API_KEY")
            if not self.api_key:
                logger.log_warning("SENDGRID_API_KEY non configurato")
                self.enabled = False
        elif self.provider == "smtp":
            self.smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
            self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
            self.smtp_user = os.getenv("SMTP_USER")
            self.smtp_password = os.getenv("SMTP_PASSWORD")
            if not self.smtp_user or not self.smtp_password:
                logger.log_warning("SMTP_USER o SMTP_PASSWORD non configurati")
                self.enabled = False
        else:
            logger.log_warning(f"Provider email non supportato: {self.provider}")
            self.enabled = False
    
    def send_new_articles_notification(self, articles: List[Dict]) -> bool:
        """
        Invia notifica email con nuovi articoli trovati.
        
        Args:
            articles: Lista di articoli trovati [{"url": "...", "title": "...", "source": "..."}]
        
        Returns:
            True se inviata con successo, False altrimenti
        """
        if not self.enabled or not articles:
            return False
        
        try:
            subject = f"📰 {len(articles)} nuovo/i articolo/i trovato/i"
            
            # Costruisci corpo email HTML
            html_body = self._build_email_html(articles)
            text_body = self._build_email_text(articles)
            
            if self.provider == "resend":
                return self._send_resend(subject, html_body, text_body)
            elif self.provider == "sendgrid":
                return self._send_sendgrid(subject, html_body, text_body)
            elif self.provider == "smtp":
                return self._send_smtp(subject, html_body, text_body)
            else:
                return False
                
        except Exception as e:
            logger.log_error(f"Errore invio email: {e}", exc_info=True)
            return False
    
    def _build_email_html(self, articles: List[Dict]) -> str:
        """Costruisce corpo email HTML."""
        articles_html = ""
        for article in articles:
            title = article.get("title", "Nessun titolo")
            url = article.get("url", "#")
            source = article.get("source", "Unknown")
            
            articles_html += f"""
            <div style="margin-bottom: 1.5rem; padding: 1rem; background: #f5f5f5; border-radius: 6px; border-left: 3px solid #667eea;">
                <h3 style="margin: 0 0 0.5rem 0; color: #1f2937;">
                    <a href="{url}" style="color: #667eea; text-decoration: none;">{title}</a>
                </h3>
                <div style="color: #6b7280; font-size: 0.9rem;">
                    <strong>Fonte:</strong> {source}<br>
                    <a href="{url}" style="color: #667eea; text-decoration: none;">Leggi articolo →</a>
                </div>
            </div>
            """
        
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <style>
                body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6; color: #333; }}
            </style>
        </head>
        <body>
            <div style="max-width: 600px; margin: 0 auto; padding: 2rem;">
                <h1 style="color: #667eea; margin-bottom: 1rem;">📰 Nuovi Articoli Trovati</h1>
                <p style="color: #6b7280; margin-bottom: 2rem;">
                    Il sistema di monitoraggio ha trovato <strong>{len(articles)}</strong> nuovo/i articolo/i.
                </p>
                {articles_html}
                <div style="margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #e5e7eb; color: #6b7280; font-size: 0.85rem;">
                    Questa è una notifica automatica dal News Pipeline Monitor.
                </div>
            </div>
        </body>
        </html>
        """
    
    def _build_email_text(self, articles: List[Dict]) -> str:
        """Costruisce corpo email in testo semplice."""
        text = f"Nuovi Articoli Trovati\n\n"
        text += f"Il sistema di monitoraggio ha trovato {len(articles)} nuovo/i articolo/i.\n\n"
        
        for i, article in enumerate(articles, 1):
            title = article.get("title", "Nessun titolo")
            url = article.get("url", "#")
            source = article.get("source", "Unknown")
            text += f"{i}. {title}\n"
            text += f"   Fonte: {source}\n"
            text += f"   URL: {url}\n\n"
        
        return text
    
    def _send_resend(self, subject: str, html_body: str, text_body: str) -> bool:
        """Invia email usando Resend API."""
        try:
            import requests
            
            response = requests.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "from": self.from_email,
                    "to": [self.recipient],
                    "subject": subject,
                    "html": html_body,
                    "text": text_body
                },
                timeout=10
            )
            
            response.raise_for_status()
            logger.log_info(f"Email inviata con successo via Resend a {self.recipient}")
            return True
            
        except Exception as e:
            logger.log_error(f"Errore invio email Resend: {e}")
            return False
    
    def _send_sendgrid(self, subject: str, html_body: str, text_body: str) -> bool:
        """Invia email usando SendGrid API."""
        try:
            import requests
            
            response = requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "personalizations": [{
                        "to": [{"email": self.recipient}]
                    }],
                    "from": {"email": self.from_email},
                    "subject": subject,
                    "content": [
                        {"type": "text/plain", "value": text_body},
                        {"type": "text/html", "value": html_body}
                    ]
                },
                timeout=10
            )
            
            response.raise_for_status()
            logger.log_info(f"Email inviata con successo via SendGrid a {self.recipient}")
            return True
            
        except Exception as e:
            logger.log_error(f"Errore invio email SendGrid: {e}")
            return False
    
    def _send_smtp(self, subject: str, html_body: str, text_body: str) -> bool:
        """Invia email usando SMTP standard."""
        try:
            import smtplib
            from email.mime.multipart import MIMEMultipart
            from email.mime.text import MIMEText
            
            msg = MIMEMultipart('alternative')
            msg['Subject'] = subject
            msg['From'] = self.from_email
            msg['To'] = self.recipient
            
            msg.attach(MIMEText(text_body, 'plain'))
            msg.attach(MIMEText(html_body, 'html'))
            
            server = smtplib.SMTP(self.smtp_host, self.smtp_port)
            server.starttls()
            server.login(self.smtp_user, self.smtp_password)
            server.send_message(msg)
            server.quit()
            
            logger.log_info(f"Email inviata con successo via SMTP a {self.recipient}")
            return True
            
        except Exception as e:
            logger.log_error(f"Errore invio email SMTP: {e}")
            return False


# Istanza globale
_email_notifier_instance: Optional[EmailNotifier] = None


def get_email_notifier() -> EmailNotifier:
    """Ottiene o crea istanza globale del notificatore email."""
    global _email_notifier_instance
    if _email_notifier_instance is None:
        _email_notifier_instance = EmailNotifier()
    return _email_notifier_instance
