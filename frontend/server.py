#!/usr/bin/env python3
"""
Server Flask semplice per servire il frontend mock.
Serve i dati JSON dalla pipeline senza bisogno di WordPress.
"""

import os
import json
import glob
from pathlib import Path
from flask import Flask, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder='.')
CORS(app)

# Path handling per Vercel (serverless) e locale
BASE_DIR = Path(__file__).parent.parent

# Su Vercel, usa /tmp per file scrivibili (unico path scrivibile)
# In locale, usa la directory data normale
IS_VERCEL = os.getenv("VERCEL") == "1" or os.getenv("VERCEL_ENV")
if IS_VERCEL:
    # Vercel environment - usa /tmp (unico path scrivibile)
    CACHE_DIR = Path("/tmp") / "cache"
    LOGS_DIR = Path("/tmp") / "logs"
    DEDUPE_DB = Path("/tmp") / "dedupe.db"
else:
    # Local environment
    CACHE_DIR = BASE_DIR / "data" / "cache"
    LOGS_DIR = BASE_DIR / "data" / "logs"
    DEDUPE_DB = BASE_DIR / "data" / "dedupe.db"

# Crea directory se non esistono
try:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    # Assicurati che la directory del database esista
    DEDUPE_DB.parent.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"Warning: Errore creazione directory: {e}")

# Inizializza tabella per articoli eliminati (lazy initialization)
_db_initialized = False

def init_deleted_articles_table():
    """Inizializza tabella per articoli eliminati nel database (lazy)."""
    global _db_initialized
    if _db_initialized:
        return
    
    try:
        import sqlite3
        # Assicurati che la directory esista
        DEDUPE_DB.parent.mkdir(parents=True, exist_ok=True)
        # Crea database vuoto se non esiste
        conn = sqlite3.connect(str(DEDUPE_DB))
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS deleted_articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT UNIQUE NOT NULL,
                original_data TEXT NOT NULL,
                rewritten_data TEXT,
                quality_gate_data TEXT,
                source_name TEXT,
                deleted_at TEXT NOT NULL,
                deleted_reason TEXT
            )
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_deleted_url ON deleted_articles(url)
        """)
        conn.commit()
        conn.close()
        _db_initialized = True
    except Exception as e:
        # Log errore ma non bloccare startup
        print(f"Warning: Errore inizializzazione database: {e}")
        # Non bloccare l'applicazione, il database verrà inizializzato quando necessario


@app.route('/')
def index():
    """Serve la pagina HTML principale."""
    return send_from_directory('.', 'index.html')


@app.route('/api/articles')
def get_articles():
    """Restituisce lista articoli estratti e riscritti."""
    articles = []
    
    # Prima: leggi file riscritti (contengono sia originale che riscritto)
    rewritten_files = glob.glob(str(CACHE_DIR / "rewritten_*.json"))
    processed_urls = set()
    
    for json_file in sorted(rewritten_files, reverse=True):  # Più recenti prima
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # Combina dati originali e riscritti
                article = {
                    **data.get("original", {}),
                    "rewritten": data.get("rewritten", {}),
                    "has_rewritten": True,
                    "processed_at": data.get("processed_at"),
                    "quality_gate": data.get("quality_gate", {})  # Aggiungi info quality gate
                }
                articles.append(article)
                processed_urls.add(data.get("url", ""))
        except Exception as e:
            print(f"Errore lettura {json_file}: {e}")
            continue
    
    # Poi: aggiungi articoli solo estratti (non ancora riscritti)
    extracted_files = glob.glob(str(CACHE_DIR / "extracted_*.json"))
    
    for json_file in sorted(extracted_files, reverse=True):
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                article = json.load(f)
                # Aggiungi solo se non già presente nei riscritti
                if article.get("url") not in processed_urls:
                    article["has_rewritten"] = False
                    articles.append(article)
        except Exception as e:
            print(f"Errore lettura {json_file}: {e}")
            continue
    
    return jsonify(articles)


@app.route('/api/logs')
def get_logs():
    """Restituisce log recenti."""
    logs = []
    
    # Leggi audit log
    audit_files = glob.glob(str(LOGS_DIR / "audit_*.jsonl"))
    
    if audit_files:
        latest_audit = max(audit_files, key=os.path.getmtime)
        try:
            with open(latest_audit, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        log_entry = json.loads(line)
                        logs.append({
                            "timestamp": log_entry.get("timestamp"),
                            "level": "info" if log_entry.get("status") == "created" else 
                                     "warning" if log_entry.get("status") == "skipped" else 
                                     "error" if log_entry.get("status") == "failed" else "info",
                            "message": f"[{log_entry.get('operation', 'unknown')}] {log_entry.get('url', '')} - {log_entry.get('status', 'unknown')}"
                        })
        except Exception as e:
            print(f"Errore lettura log: {e}")
    
    # Limita a ultimi 100
    return jsonify(logs[-100:])


@app.route('/api/stats')
def get_stats():
    """Restituisce statistiche pipeline."""
    stats = {
        "total_articles": 0,
        "extracted": 0,
        "created": 0,
        "skipped": 0,
        "failed": 0
    }
    
    # Conta articoli estratti
    json_files = glob.glob(str(CACHE_DIR / "extracted_*.json"))
    stats["total_articles"] = len(json_files)
    stats["extracted"] = len(json_files)
    
    # Leggi report più recente
    report_files = glob.glob(str(LOGS_DIR / "report_*.json"))
    
    if report_files:
        latest_report = max(report_files, key=os.path.getmtime)
        try:
            with open(latest_report, 'r', encoding='utf-8') as f:
                report = json.load(f)
                if "stats" in report:
                    stats.update(report["stats"])
        except Exception as e:
            print(f"Errore lettura report: {e}")
    
    return jsonify(stats)


@app.route('/api/article/<path:filename>')
def get_article(filename):
    """Restituisce un singolo articolo."""
    json_file = CACHE_DIR / filename
    
    if json_file.exists():
        with open(json_file, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    
    return jsonify({"error": "Articolo non trovato"}), 404


@app.route('/api/article-by-url')
def get_article_by_url():
    """Restituisce un articolo per URL."""
    from flask import request
    
    url = request.args.get('url')
    if not url:
        return jsonify({"error": "URL non specificato"}), 400
    
    # Cerca prima nei file rewritten
    url_hash = url.replace("://", "_").replace("/", "_").replace("?", "_")[:100]
    rewritten_file = CACHE_DIR / f"rewritten_{url_hash}.json"
    
    if rewritten_file.exists():
        with open(rewritten_file, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    
    # Fallback: cerca nei file extracted
    extracted_file = CACHE_DIR / f"extracted_{url_hash}.json"
    
    if extracted_file.exists():
        with open(extracted_file, 'r', encoding='utf-8') as f:
            article = json.load(f)
            return jsonify({
                "original": article,
                "rewritten": {},
                "url": article.get("url"),
                "source_name": article.get("source_name")
            })
    
    return jsonify({"error": "Articolo non trovato"}), 404


@app.route('/article.html')
def article_page():
    """Serve la pagina HTML dell'articolo."""
    return send_from_directory('.', 'article.html')


@app.route('/api/extract-articles', methods=['POST'])
def extract_articles():
    """Esegue pipeline per estrarre nuovi articoli."""
    from flask import request
    
    try:
        data = request.get_json() or {}
        limit = data.get('limit', 10)
        
        # Su Vercel, esegui direttamente (no threading per serverless)
        import sys
        from pathlib import Path
        sys.path.insert(0, str(BASE_DIR))
        
        from src.pipeline import NewsPipeline
        
        # Configurazione per Vercel
        config_dir = str(BASE_DIR / "config")
        pipeline = NewsPipeline(config_dir=config_dir, dry_run=True)
        
        # Esegui pipeline (sincrono su Vercel)
        pipeline.run(limit=limit)
        
        return jsonify({
            "success": True,
            "message": f"Pipeline completata: {limit} articoli processati",
            "status": "completed"
        })
        
    except Exception as e:
        import traceback
        error_trace = traceback.format_exc()
        print(f"Errore pipeline: {e}\n{error_trace}")
        return jsonify({
            "error": str(e),
            "traceback": error_trace
        }), 500


@app.route('/api/pipeline-status')
def get_pipeline_status():
    """Restituisce stato corrente della pipeline."""
    status_file = LOGS_DIR / "pipeline_status.json" if IS_VERCEL else BASE_DIR / "data" / "pipeline_status.json"
    
    if status_file.exists():
        try:
            with open(status_file, 'r', encoding='utf-8') as f:
                status = json.load(f)
                return jsonify(status)
        except Exception as e:
            return jsonify({
                "status": "error",
                "error": str(e)
            }), 500
    
    return jsonify({
        "status": "idle",
        "message": "Nessuna pipeline in esecuzione"
    })


@app.route('/api/rewrite-article', methods=['POST'])
def rewrite_article():
    """Riscrive un singolo articolo."""
    from flask import request
    
    try:
        data = request.get_json()
        article_url = data.get('url')
        
        if not article_url:
            return jsonify({"error": "URL non specificato"}), 400
        
        # Importa moduli pipeline
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        
        from src.extract_article import ArticleExtractor
        from src.rewrite import ArticleRewriter
        import os
        from dotenv import load_dotenv
        import time
        import json
        
        load_dotenv()
        
        # Estrai articolo se non già estratto
        extractor = ArticleExtractor(cache_dir=str(BASE_DIR / "data" / "cache"))
        
        # Cerca se già estratto
        url_hash = article_url.replace("://", "_").replace("/", "_").replace("?", "_")[:100]
        extracted_file = CACHE_DIR / f"extracted_{url_hash}.json"
        
        if extracted_file.exists():
            with open(extracted_file, 'r', encoding='utf-8') as f:
                extracted_data = json.load(f)
        else:
            # Estrai ora
            extracted_data = extractor.extract(article_url)
        
        # Controlla contenuto
        text = extracted_data.get("text", "").strip()
        title = extracted_data.get("title", "").strip()
        
        if not text or len(text) < 100:
            return jsonify({
                "error": "Contenuto vuoto o troppo corto",
                "details": f"Solo {len(text)} caratteri estratti"
            }), 400
        
        if not title or len(title) < 10:
            return jsonify({
                "error": "Titolo vuoto o troppo corto",
                "details": f"Solo {len(title)} caratteri"
            }), 400
        
        # Riscrivi
        llm_provider = os.getenv("LLM_PROVIDER", "openai").lower()
        rewriter = ArticleRewriter(
            provider=llm_provider,
            model=os.getenv("OPENAI_MODEL") or os.getenv("ANTHROPIC_MODEL"),
            api_key=os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")
        )
        
        rewritten_data = rewriter.rewrite(extracted_data)
        
        # Calcola quality gate
        from src.quality_gates import QualityGates
        quality_gates = QualityGates()
        quality_result = quality_gates.check(extracted_data, rewritten_data)
        
        # Salva dati riscritti con quality gate
        rewritten_file = CACHE_DIR / f"rewritten_{url_hash}.json"
        combined_data = {
            "original": extracted_data,
            "rewritten": rewritten_data,
            "url": extracted_data.get("url"),
            "source_name": extracted_data.get("source_name"),
            "processed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "quality_gate": {
                "passed": quality_result.get("ok", False),
                "similarity_score": quality_result.get("similarity_score", 0.0),
                "risk_level": quality_result.get("risk_level", "low"),
                "issues": quality_result.get("issues", [])
            }
        }
        
        with open(rewritten_file, "w", encoding="utf-8") as f:
            json.dump(combined_data, f, indent=2, ensure_ascii=False)
        
        return jsonify({
            "success": True,
            "rewritten": rewritten_data,
            "article_url": f"/article.html?url={article_url}"
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@app.route('/api/delete-article', methods=['POST'])
def delete_article():
    """Elimina un articolo e lo salva nel database."""
    from flask import request
    import sqlite3
    import json
    import time
    
    # Inizializza database se necessario
    init_deleted_articles_table()
    
    try:
        data = request.get_json()
        article_url = data.get('url')
        
        if not article_url:
            return jsonify({"error": "URL non specificato"}), 400
        
        # Cerca l'articolo nei file rewritten o extracted
        url_hash = article_url.replace("://", "_").replace("/", "_").replace("?", "_")[:100]
        rewritten_file = CACHE_DIR / f"rewritten_{url_hash}.json"
        extracted_file = CACHE_DIR / f"extracted_{url_hash}.json"
        
        article_data = None
        source_name = None
        
        # Leggi dati dall'articolo
        if rewritten_file.exists():
            with open(rewritten_file, 'r', encoding='utf-8') as f:
                article_data = json.load(f)
                source_name = article_data.get('source_name', 'Unknown')
        elif extracted_file.exists():
            with open(extracted_file, 'r', encoding='utf-8') as f:
                article_data = json.load(f)
                source_name = article_data.get('source_name', 'Unknown')
        else:
            return jsonify({"error": "Articolo non trovato"}), 404
        
        # Salva nel database prima di eliminare
        conn = sqlite3.connect(str(DEDUPE_DB))
        cursor = conn.cursor()
        
        original_data = json.dumps(article_data.get('original', article_data), ensure_ascii=False)
        rewritten_data = json.dumps(article_data.get('rewritten', {}), ensure_ascii=False) if article_data.get('rewritten') else None
        quality_gate_data = json.dumps(article_data.get('quality_gate', {}), ensure_ascii=False) if article_data.get('quality_gate') else None
        
        cursor.execute("""
            INSERT OR REPLACE INTO deleted_articles 
            (url, original_data, rewritten_data, quality_gate_data, source_name, deleted_at, deleted_reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            article_url,
            original_data,
            rewritten_data,
            quality_gate_data,
            source_name,
            time.strftime("%Y-%m-%dT%H:%M:%S"),
            data.get('reason', 'Eliminato dall\'utente')
        ))
        
        conn.commit()
        conn.close()
        
        # Elimina file dalla cache
        if rewritten_file.exists():
            rewritten_file.unlink()
        if extracted_file.exists():
            extracted_file.unlink()
        
        return jsonify({
            "success": True,
            "message": "Articolo eliminato e salvato nel database"
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@app.route('/api/deleted-articles')
def get_deleted_articles():
    """Restituisce lista articoli eliminati dal database."""
    import sqlite3
    import json
    
    # Inizializza database se necessario
    init_deleted_articles_table()
    
    try:
        conn = sqlite3.connect(str(DEDUPE_DB))
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT url, original_data, rewritten_data, quality_gate_data, 
                   source_name, deleted_at, deleted_reason
            FROM deleted_articles
            ORDER BY deleted_at DESC
            LIMIT 100
        """)
        
        deleted = []
        for row in cursor.fetchall():
            url, orig_json, rew_json, qg_json, source, deleted_at, reason = row
            
            article = {
                "url": url,
                "source_name": source,
                "deleted_at": deleted_at,
                "deleted_reason": reason
            }
            
            # Parse JSON data
            if orig_json:
                article["original"] = json.loads(orig_json)
            if rew_json:
                article["rewritten"] = json.loads(rew_json)
            if qg_json:
                article["quality_gate"] = json.loads(qg_json)
            
            deleted.append(article)
        
        conn.close()
        return jsonify(deleted)
        
    except Exception as e:
        return jsonify({
            "error": str(e)
        }), 500


@app.route('/api/monitor/start', methods=['POST'])
def start_monitor():
    """Avvia monitoraggio continuo dei feed."""
    from flask import request
    
    try:
        data = request.get_json() or {}
        poll_interval = data.get('poll_interval', 300)  # Default 5 minuti
        
        from src.monitor import get_monitor
        monitor = get_monitor(poll_interval=poll_interval, dry_run=True)
        
        if monitor.is_running:
            return jsonify({
                "success": False,
                "message": "Monitor già in esecuzione"
            }), 400
        
        monitor.start()
        
        return jsonify({
            "success": True,
            "message": f"Monitor avviato (controllo ogni {poll_interval} secondi)",
            "poll_interval": poll_interval
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@app.route('/api/monitor/stop', methods=['POST'])
def stop_monitor():
    """Ferma monitoraggio continuo."""
    try:
        from src.monitor import get_monitor
        monitor = get_monitor()
        
        if not monitor.is_running:
            return jsonify({
                "success": False,
                "message": "Monitor non in esecuzione"
            }), 400
        
        monitor.stop()
        
        return jsonify({
            "success": True,
            "message": "Monitor fermato"
        })
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


@app.route('/api/monitor/status')
def get_monitor_status():
    """Restituisce stato del monitoraggio."""
    try:
        from src.monitor import get_monitor
        monitor = get_monitor()
        stats = monitor.get_stats()
        
        return jsonify(stats)
        
    except Exception as e:
        return jsonify({
            "error": str(e),
            "running": False
        }), 500


@app.route('/api/test-email', methods=['POST'])
def test_email():
    """Test invio email di notifica."""
    try:
        from src.email_notifier import get_email_notifier
        email_notifier = get_email_notifier()
        
        if not email_notifier.enabled:
            return jsonify({
                "success": False,
                "message": "Notifiche email non abilitate. Configura EMAIL_NOTIFICATIONS_ENABLED=true e le credenziali."
            }), 400
        
        # Crea articolo di test
        test_articles = [{
            "url": "https://example.com/test-article",
            "title": "Articolo di Test - Notifica Email",
            "source": "Test Source"
        }]
        
        success = email_notifier.send_new_articles_notification(test_articles)
        
        if success:
            return jsonify({
                "success": True,
                "message": f"Email di test inviata a {email_notifier.recipient}"
            })
        else:
            return jsonify({
                "success": False,
                "message": "Errore durante invio email. Controlla i log."
            }), 500
        
    except Exception as e:
        import traceback
        return jsonify({
            "error": str(e),
            "traceback": traceback.format_exc()
        }), 500


# Handler per Vercel serverless
def handler(request):
    """Handler per Vercel serverless functions."""
    return app(request.environ, lambda status, headers: None)

# Export per Vercel
if __name__ == '__main__':
    import sys
    
    print("🚀 Avvio server frontend mock...")
    print(f"📁 Cache dir: {CACHE_DIR}")
    print(f"📁 Logs dir: {LOGS_DIR}")
    print(f"📁 Database: {DEDUPE_DB}")
    
    # Prova porta 5000, se occupata usa 5001
    port = 5000
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(('127.0.0.1', port))
        sock.close()
    except OSError:
        print(f"⚠️  Porta {port} occupata, uso porta 5001")
        port = 5001
    
    print(f"\n🌐 Apri http://localhost:{port} nel browser")
    print("   Premi Ctrl+C per fermare il server\n")
    
    try:
        app.run(debug=True, host='127.0.0.1', port=port)
    except PermissionError:
        print(f"\n❌ Errore: Nessun permesso per usare la porta {port}")
        print("   Prova con una porta diversa (es: 8080)")
        print("   Oppure esegui con: sudo python server.py")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Errore: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
