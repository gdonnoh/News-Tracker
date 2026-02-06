# 🏗️ Architettura Tecnica - News Tracker Pipeline

## 📋 Indice
1. [Panoramica Generale](#panoramica-generale)
2. [Architettura del Sistema](#architettura-del-sistema)
3. [Flusso di Elaborazione](#flusso-di-elaborazione)
4. [Componenti Tecnici](#componenti-tecnici)
5. [Dettagli Implementativi](#dettagli-implementativi)
6. [Deploy e Infrastruttura](#deploy-e-infrastruttura)

---

## 🎯 Panoramica Generale

**News Tracker** è una pipeline automatizzata che:
- **Raccoglie** articoli da feed RSS di siti di notizie italiani
- **Estrae** contenuto pulito (senza pubblicità/banner)
- **Riscrive** con LLM per creare contenuto originale
- **Pubblica** su WordPress come draft per revisione umana

### Stack Tecnologico

```
Backend: Python 3.11+
├── Web Scraping: requests + BeautifulSoup4 + readability-lxml
├── Feed RSS: feedparser
├── ML/AI: sentence-transformers (opzionale), OpenAI/Anthropic API
├── Database: SQLite (deduplicazione e tracking)
├── Web Framework: Flask (frontend mock)
└── Deploy: Vercel Serverless Functions

Frontend: HTML/CSS/JavaScript vanilla
└── Dashboard per visualizzazione e controllo

Infrastruttura:
├── Locale: File system (data/cache, data/logs)
└── Vercel: /tmp (filesystem temporaneo serverless)
```

---

## 🏛️ Architettura del Sistema

### Diagramma Architetturale

```
┌─────────────────────────────────────────────────────────────┐
│                    NEWS TRACKER PIPELINE                     │
└─────────────────────────────────────────────────────────────┘

┌──────────────┐      ┌──────────────┐      ┌──────────────┐
│   FEED RSS   │─────▶│  FETCHER     │─────▶│  EXTRACTOR  │
│  (Sources)   │      │ (URLs)       │      │  (Content)   │
└──────────────┘      └──────────────┘      └──────────────┘
                            │                      │
                            ▼                      ▼
                    ┌──────────────┐      ┌──────────────┐
                    │  DEDUPE DB   │      │   CACHE      │
                    │  (SQLite)    │      │  (JSON/HTML) │
                    └──────────────┘      └──────────────┘
                            │                      │
                            ▼                      ▼
                    ┌──────────────────────────────────┐
                    │      PIPELINE ORCHESTRATOR       │
                    └──────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  DEDUPE      │    │   REWRITE    │    │   QUALITY    │
│  (Hash/ML)   │    │   (LLM API)  │    │   GATES      │
└──────────────┘    └──────────────┘    └──────────────┘
        │                   │                   │
        └───────────────────┼───────────────────┘
                            │
                            ▼
                    ┌──────────────┐
                    │  WORDPRESS   │
                    │  REST API    │
                    └──────────────┘
```

### Componenti Principali

#### 1. **SourceFetcher** (`src/fetch_sources.py`)
**Scopo**: Raccoglie URL da feed RSS

**Tecnicismi**:
- **feedparser**: Parsing feed RSS/Atom (gestisce vari formati)
- **Round-robin distribution**: Distribuisce articoli da diverse fonti quando c'è un limite
- **Date filtering**: Filtra articoli più vecchi di 2 giorni usando `dateutil.parser`
- **SQLite tracking**: Tabella `processed_urls` per evitare riprocessare URL
- **Rate limiting**: Delay configurabile tra richieste (default 6s)
- **User-Agent**: Header personalizzato per identificare il bot

**Database Schema**:
```sql
processed_urls (
    url_hash TEXT PRIMARY KEY,  -- SHA256 dell'URL
    url TEXT UNIQUE NOT NULL,
    first_seen_at TEXT,         -- ISO timestamp
    last_seen_at TEXT,
    processed BOOLEAN DEFAULT 0 -- Flag se già processato
)
```

#### 2. **ArticleExtractor** (`src/extract_article.py`)
**Scopo**: Estrae contenuto pulito da HTML

**Tecnicismi**:
- **readability-lxml**: Algoritmo Mozilla Readability per estrarre contenuto principale
- **BeautifulSoup4**: Parsing HTML avanzato per metadata
- **URL normalization**: Rimuove parametri tracking (utm_, fbclid, etc.)
- **Image extraction**: Priorità a meta tags (og:image) poi immagini nel contenuto
- **Caching**: Salva HTML raw e JSON estratto per debug

**Algoritmo Readability**:
```
1. Scarica HTML con requests
2. Passa a Document() (readability-lxml)
3. Estrae:
   - title: Titolo principale
   - content: HTML pulito del contenuto
   - short_title: Titolo breve
4. BeautifulSoup per metadata aggiuntivi:
   - published_at (meta tags, JSON-LD)
   - author (meta tags, schema.org)
   - images (og:image, meta property="image")
```

#### 3. **Deduplicator** (`src/dedupe.py`)
**Scopo**: Evita duplicati usando hash e similarità semantica

**Tecnicismi**:
- **Hash-based deduplication**: SHA256 su URL+title normalizzato
- **Semantic similarity**: sentence-transformers per embeddings (opzionale)
- **Multi-level check**:
  1. Hash esatto (URL+title identici)
  2. Stesso URL canonico
  3. Titolo simile semanticamente (se ML deps disponibili)
- **Fallback**: Se ML deps non disponibili, usa solo hash

**Normalizzazione Titolo**:
```python
def _normalize_title(title: str) -> str:
    # Lowercase
    normalized = title.lower().strip()
    # Rimuovi punteggiatura
    for char in ".,;:!?-_":
        normalized = normalized.replace(char, " ")
    # Normalizza spazi multipli
    normalized = " ".join(normalized.split())
    return normalized
```

**Embeddings** (se disponibili):
- Modello: `paraphrase-multilingual-MiniLM-L12-v2` (384 dimensioni)
- Cosine similarity: `dot(emb1, emb2)` (già normalizzati)
- Threshold: 0.85 (configurabile)

#### 4. **ArticleRewriter** (`src/rewrite.py`)
**Scopo**: Riscrive articoli con LLM per creare contenuto originale

**Tecnicismi**:
- **Multi-provider**: Supporta OpenAI e Anthropic
- **Structured output**: JSON obbligatorio con schema fisso
- **Temperature**: 0.9 (alta creatività)
- **Prompt engineering**: Prompt dettagliato con guardrail

**Prompt Structure**:
```
1. System message: Ruolo giornalista + vincoli JSON
2. User prompt:
   - Istruzioni riscrittura estrema
   - Dati originali (title, text limitato a 5000 char)
   - Schema JSON richiesto
3. Response format: JSON object (OpenAI) o parsing manuale (Anthropic)
```

**Output JSON Schema**:
```json
{
  "headline": "string (max 100 char)",
  "lead": "string (2-3 frasi)",
  "body_markdown": "string (Markdown con ## sottotitoli)",
  "tags": ["tag1", "tag2", "tag3"],
  "category": "string (categoria principale)",
  "meta_title": "string (max 60 char, SEO)",
  "meta_description": "string (max 160 char, SEO)"
}
```

**Guardrail**:
- "USA SOLO informazioni presenti nell'articolo"
- "NON inventare dati, numeri, citazioni"
- "Se manca un dato, ometti completamente"

#### 5. **QualityGates** (`src/quality_gates.py`)
**Scopo**: Controlli qualità prima della pubblicazione

**Tecnicismi**:
- **Similarity check**: Confronta originale vs riscritto
  - Con ML: embeddings cosine similarity
  - Senza ML: Jaccard similarity (parole comuni)
- **Sanity check**: 
  - Lunghezza (min/max parole)
  - Presenza contenuto (title, lead, body)
  - Ripetizioni eccessive (parole >10% del testo)
  - Pattern pericolosi (script, iframe, javascript:)
- **Policy check**:
  - Keyword ad alto rischio (diffamazione, hate speech)
  - Keyword a medio rischio (gossip, scandalo)
  - Pattern dati sensibili (regex per CF, carte di credito)

**Risk Levels**:
- **low**: Nessun problema, può essere pubblicato
- **medium**: Richiede revisione umana (`needs_review=true`)
- **high**: Non pubblicare, scarta

#### 6. **WordPressClient** (`src/wp_client.py`)
**Scopo**: Integrazione con WordPress REST API

**Tecnicismi**:
- **Authentication**: Application Password (WordPress 5.6+)
- **REST API endpoints**:
  - `POST /wp-json/wp/v2/posts` - Crea post
  - `POST /wp-json/wp/v2/media` - Upload immagine
  - `PUT /wp-json/wp/v2/posts/{id}` - Aggiorna post (featured image)
- **Meta fields**: Custom fields per tracciabilità
- **Status**: Sempre "draft" (non pubblica automaticamente)

**Meta Fields Custom**:
```python
meta_fields = {
    "source_name": "Corriere della Sera",
    "source_url": "https://...",
    "source_published_at": "2024-01-15T10:30:00",
    "ingest_timestamp": "2024-01-15T12:00:00",
    "source_hash": "abc123...",
    "ai_version": "1.0",
    "risk_level": "low",
    "needs_review": "0",
    "original_title": "Titolo originale"
}
```

#### 7. **NewsPipeline** (`src/pipeline.py`)
**Scopo**: Orchestratore principale end-to-end

**Tecnicismi**:
- **Sequential processing**: Processa articoli uno alla volta
- **Error handling**: Try/except per ogni step, continua con prossimo articolo
- **Status tracking**: Salva stato in `pipeline_status.json` per frontend
- **Timing**: Traccia tempo per ogni step (extract, dedupe, rewrite, etc.)
- **Audit logging**: Log JSONL per ogni operazione

**Flusso Step-by-Step**:
```python
for candidate in candidates:
    1. Extract → ArticleExtractor.extract(url)
       - Salva: raw_*.html, extracted_*.json
       - Timing: extract_time
    
    2. Content Validation → Controlla lunghezza title/text
       - Skip se troppo corto
    
    3. Dedupe → Deduplicator.check_duplicate()
       - Skip se duplicato
       - Timing: dedupe_time
    
    4. Rewrite → ArticleRewriter.rewrite()
       - Chiama LLM API
       - Salva: rewritten_*.json
       - Timing: rewrite_time
    
    5. Quality Gates → QualityGates.check()
       - Calcola similarity_score
       - Determina risk_level
       - Skip se high risk o troppo simile
       - Timing: quality_time
    
    6. WordPress Post → WordPressClient.create_post()
       - Upload media (se disponibile)
       - Crea post draft
       - Set featured image
       - Timing: wp_post_time
    
    7. Register → Deduplicator.register_article()
       - Salva hash nel DB
       - Timing: register_time
    
    8. Audit Log → AuditLogger.log()
       - JSONL con tutti i dettagli
```

---

## 🔄 Flusso di Elaborazione

### 1. Raccolta URL (Fetch)

```
Feed RSS → feedparser → Lista entry
    ↓
Filtro 1: Whitelist domain (se attiva)
    ↓
Filtro 2: Data pubblicazione (< 2 giorni)
    ↓
Filtro 3: Già processato? (SQLite check)
    ↓
Round-robin distribution (se limit applicato)
    ↓
Lista candidati ordinata per data
```

**Esempio Round-Robin**:
```
Limit: 6 articoli
Fonti: [Corriere, Repubblica, Messaggero]

Round 1: Corriere[0], Repubblica[0], Messaggero[0]
Round 2: Corriere[1], Repubblica[1], Messaggero[1]
Risultato: 6 articoli distribuiti equamente
```

### 2. Estrazione Contenuto (Extract)

```
URL → requests.get() → HTML raw
    ↓
readability.Document() → Contenuto principale
    ↓
BeautifulSoup → Metadata (date, author, images)
    ↓
Normalizzazione URL (rimuovi tracking params)
    ↓
Salva: raw_*.html + extracted_*.json
```

**readability-lxml Algorithm**:
- Analizza struttura DOM
- Calcola score per ogni elemento (lunghezza, link density, etc.)
- Seleziona elemento con score più alto
- Estrae contenuto pulito

### 3. Deduplicazione (Dedupe)

```
Articolo → Normalizza title + URL
    ↓
Calcola hash_id = SHA256(url + normalized_title)
    ↓
Check DB:
    ├─ Hash esatto? → DUPLICATO
    ├─ Stesso URL? → DUPLICATO
    └─ Titolo simile? → Calcola embeddings
        ├─ Similarity > threshold? → DUPLICATO
        └─ Similarity < threshold? → NUOVO
```

### 4. Riscrittura (Rewrite)

```
Articolo estratto → Build prompt
    ↓
LLM API call (OpenAI/Anthropic)
    ├─ OpenAI: response_format={"type": "json_object"}
    └─ Anthropic: Parsing manuale JSON
    ↓
Validazione JSON + Schema check
    ↓
Calcola word_count
    ↓
Salva: rewritten_*.json
```

**Prompt Engineering**:
- System message: Ruolo + vincoli
- User prompt: Istruzioni dettagliate + dati originali
- Temperature: 0.9 (alta creatività)
- Max tokens: 4000 (Anthropic), auto (OpenAI)

### 5. Quality Gates

```
Originale + Riscritto → QualityGates.check()
    ↓
├─ Similarity Check
│   ├─ Con ML: embeddings cosine similarity
│   └─ Senza ML: Jaccard similarity (parole)
│   └─ Score > 0.85? → FAIL
    ↓
├─ Sanity Check
│   ├─ Lunghezza OK? (200-2000 parole)
│   ├─ Contenuto presente? (title, lead, body)
│   ├─ Ripetizioni eccessive? (<10% stessa parola)
│   └─ Pattern pericolosi? (script, iframe)
    ↓
└─ Policy Check
    ├─ Keyword alto rischio? → risk_level="high"
    ├─ Keyword medio rischio? → risk_level="medium"
    └─ Dati sensibili? → risk_level="high"
    ↓
Result: {ok: bool, similarity_score: float, risk_level: str}
```

### 6. WordPress Posting

```
Dati riscritti → WordPressClient.create_post()
    ↓
├─ Upload Media (se immagine disponibile)
│   └─ POST /wp-json/wp/v2/media
│   └─ Ritorna media_id
    ↓
├─ Crea Post Draft
│   └─ POST /wp-json/wp/v2/posts
│   └─ Body: title, content (HTML da Markdown), excerpt, status="draft"
│   └─ Ritorna post_id
    ↓
├─ Set Featured Image (se media_id disponibile)
│   └─ PUT /wp-json/wp/v2/posts/{post_id}
│   └─ Body: featured_media=media_id
    ↓
└─ Set Meta Fields
    └─ PUT /wp-json/wp/v2/posts/{post_id}
    └─ Body: meta={source_name, source_url, ...}
```

---

## 🔧 Componenti Tecnici

### Database SQLite

**File**: `data/dedupe.db`

**Tabelle**:

1. **processed_urls**
```sql
CREATE TABLE processed_urls (
    url_hash TEXT PRIMARY KEY,      -- SHA256(URL)
    url TEXT UNIQUE NOT NULL,
    first_seen_at TEXT NOT NULL,    -- ISO timestamp
    last_seen_at TEXT NOT NULL,
    processed BOOLEAN DEFAULT 0     -- 1 se processato completamente
);
CREATE INDEX idx_url ON processed_urls(url);
```

2. **article_hashes**
```sql
CREATE TABLE article_hashes (
    hash_id TEXT PRIMARY KEY,           -- SHA256(url + normalized_title)
    canonical_url TEXT NOT NULL,
    normalized_title TEXT NOT NULL,
    title_hash TEXT NOT NULL,           -- SHA256(normalized_title)
    body_hash TEXT,                     -- SHA256(body) opzionale
    created_at TEXT NOT NULL,
    wp_post_id INTEGER                  -- ID post WordPress se creato
);
CREATE INDEX idx_canonical_url ON article_hashes(canonical_url);
CREATE INDEX idx_title_hash ON article_hashes(title_hash);
```

3. **article_embeddings** (opzionale, se ML deps disponibili)
```sql
CREATE TABLE article_embeddings (
    hash_id TEXT PRIMARY KEY,
    title_embedding BLOB,              -- numpy array serializzato
    body_embedding BLOB,
    FOREIGN KEY (hash_id) REFERENCES article_hashes(hash_id)
);
```

4. **deleted_articles** (frontend)
```sql
CREATE TABLE deleted_articles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    original_data TEXT NOT NULL,       -- JSON serializzato
    rewritten_data TEXT,
    quality_gate_data TEXT,
    source_name TEXT,
    deleted_at TEXT NOT NULL,
    deleted_reason TEXT
);
```

### File System Cache

**Struttura**:
```
data/
├── cache/
│   ├── raw_*.html              # HTML originale scaricato
│   ├── extracted_*.json        # Dati estratti (title, text, images, etc.)
│   └── rewritten_*.json        # Dati riscritti + quality gate
├── logs/
│   ├── audit_YYYYMMDD.jsonl    # Log audit (JSON Lines)
│   ├── pipeline_YYYYMMDD.log   # Log testuale
│   └── report_*.json           # Report statistiche run
├── dedupe.db                    # Database SQLite
└── pipeline_status.json         # Stato corrente pipeline (per frontend)
```

**Naming Convention**:
- `raw_`: URL normalizzato con caratteri speciali sostituiti
- `extracted_`: Stesso hash dell'URL
- `rewritten_`: Stesso hash dell'URL

**Esempio**:
```
URL: https://www.corriere.it/politica/articolo.html?utm_source=twitter
Hash: corriere_it_politica_articolo_html_utm_source_twitter
Files:
  - raw_corriere_it_politica_articolo_html_utm_source_twitter.html
  - extracted_corriere_it_politica_articolo_html_utm_source_twitter.json
  - rewritten_corriere_it_politica_articolo_html_utm_source_twitter.json
```

### Logging System

**Audit Logger** (`src/logger.py`):
- Formato: JSON Lines (`.jsonl`) - una riga = un evento
- Struttura:
```json
{
  "timestamp": "2024-01-15T12:05:30",
  "operation": "pipeline",
  "url": "https://...",
  "status": "created|skipped|failed",
  "post_id": 123,
  "timing": {
    "extract": 2.5,
    "dedupe": 0.3,
    "rewrite": 8.2,
    "quality": 1.1,
    "wp_post": 3.4,
    "total": 15.5
  },
  "details": {}
}
```

**File Logger**:
- Formato: Testo semplice con timestamp
- Livelli: DEBUG, INFO, WARNING, ERROR
- Rotazione: Un file per giorno

### Frontend Mock (`frontend/`)

**Architettura**:
- **Flask**: Server Python per API REST
- **Static files**: HTML/CSS/JS serviti direttamente
- **API endpoints**:
  - `GET /api/articles` - Lista articoli
  - `GET /api/article-by-url?url=...` - Singolo articolo
  - `POST /api/extract-articles` - Trigger pipeline
  - `POST /api/rewrite-article` - Riscrivi singolo articolo
  - `GET /api/pipeline-status` - Stato pipeline
  - `POST /api/delete-article` - Elimina articolo
  - `GET /api/deleted-articles` - Lista eliminati
  - `POST /api/monitor/start` - Avvia monitoraggio
  - `POST /api/monitor/stop` - Ferma monitoraggio
  - `GET /api/monitor/status` - Stato monitoraggio

**Real-time Updates**:
- Polling ogni 2 secondi per `pipeline-status`
- Aggiornamento progress bar e log in tempo reale
- WebSocket non necessario (polling sufficiente)

---

## 🚀 Deploy e Infrastruttura

### Vercel Serverless Functions

**Configurazione** (`vercel.json`):
```json
{
  "version": 2,
  "routes": [
    {
      "src": "/(.*)",
      "dest": "frontend/server.py"
    }
  ],
  "env": {
    "PYTHON_VERSION": "3.11",
    "VERCEL": "1"
  },
  "functions": {
    "frontend/server.py": {
      "maxDuration": 30,
      "memory": 2048
    }
  }
}
```

**Caratteristiche Vercel**:
- **Filesystem**: Read-only tranne `/tmp`
- **Cold start**: ~1-2 secondi per prima richiesta
- **Warm start**: <100ms per richieste successive
- **Timeout**: 30 secondi max (Hobby plan)
- **Memory**: 2048MB max (Hobby plan)

**Path Handling**:
```python
IS_VERCEL = os.getenv("VERCEL") == "1" or os.getenv("VERCEL_ENV")
if IS_VERCEL:
    CACHE_DIR = Path("/tmp") / "cache"
    LOGS_DIR = Path("/tmp") / "logs"
    DEDUPE_DB = Path("/tmp") / "dedupe.db"
else:
    CACHE_DIR = BASE_DIR / "data" / "cache"
    LOGS_DIR = BASE_DIR / "data" / "logs"
    DEDUPE_DB = BASE_DIR / "data" / "dedupe.db"
```

**Limitazioni Vercel**:
- `/tmp` viene svuotato tra invocazioni (non persistente)
- Database SQLite funziona ma si resetta
- Cache non persistente tra deploy
- **Soluzione**: Usare database esterno (PostgreSQL, MongoDB) per produzione

### Monitoraggio Continuo (`src/monitor.py`)

**Architettura**:
- **Threading**: Thread separato per loop di monitoraggio
- **Polling**: Controlla feed ogni N secondi (default 300 = 5 minuti)
- **Singleton pattern**: Una sola istanza del monitor
- **Status file**: `data/monitor_status.json` per stato persistente

**Flusso Monitor**:
```
Start Monitor
    ↓
Loop infinito:
    1. Fetch feed RSS (SourceFetcher)
    2. Filtra nuovi articoli (non in processed_urls)
    3. Se nuovi trovati:
       ├─ Esegui pipeline (NewsPipeline.run())
       ├─ Aggiorna statistiche
       └─ Invia email notification (se configurato)
    4. Attendi poll_interval secondi
    5. Ripeti
```

**Nota Vercel**:
- Threading non funziona su Vercel (serverless)
- Monitor deve essere eseguito esternamente (cron job, Vercel Cron, etc.)

### Email Notifications (`src/email_notifier.py`)

**Provider Supportati**:
1. **Resend** (consigliato per Vercel)
   - API semplice
   - Buona integrazione serverless
   - Free tier generoso

2. **SendGrid**
   - Enterprise-grade
   - API robusta
   - Free tier limitato

3. **SMTP** (standard)
   - Qualsiasi server SMTP
   - Gmail, Outlook, etc.
   - Richiede credenziali email

**Formato Email**:
- HTML con tabella articoli
- Link diretti agli articoli
- Statistiche (quanti nuovi, quanti processati)

---

## 🔐 Sicurezza e Best Practices

### 1. **Nessun Banner nel Contenuto**
- Readability estrae solo contenuto principale
- Filtri HTML rimuovono script/iframe
- Validazione finale controlla pattern pericolosi

### 2. **Permessi WordPress Minimizzati**
- Utente dedicato con ruolo "Author"
- Solo Application Password (non password principale)
- Status sempre "draft" (non può pubblicare)

### 3. **Rate Limiting**
- Delay tra richieste RSS (6s default)
- Delay tra estrazioni (2s default)
- Evita ban da siti sorgente

### 4. **Error Handling**
- Try/except per ogni operazione
- Logging completo di errori
- Continua processing anche se un articolo fallisce

### 5. **Audit Trail**
- Ogni operazione loggata in JSONL
- Timestamp precisi
- Tracciabilità completa (URL → post_id)

---

## 📊 Performance e Ottimizzazioni

### Ottimizzazioni Applicate

1. **Lazy Loading ML Models**
   - sentence-transformers caricato solo quando necessario
   - Fallback se non disponibile

2. **Caching**
   - HTML raw salvato per debug
   - JSON estratti per evitare ri-estrazione

3. **Database Indexing**
   - Indici su `url`, `canonical_url`, `title_hash`
   - Query veloci per deduplicazione

4. **Batch Processing**
   - Processa articoli sequenzialmente (evita OOM)
   - Status tracking per monitoraggio

5. **Vercel Optimizations**
   - Rimossi ML dependencies pesanti da requirements.txt
   - Path handling per ambiente serverless
   - Lazy initialization database

### Metriche Tipiche

- **Extract**: 2-5 secondi per articolo
- **Dedupe**: 0.1-0.5 secondi (hash), 1-3 secondi (con ML)
- **Rewrite**: 5-15 secondi (dipende da LLM API)
- **Quality**: 0.5-2 secondi (con ML), <0.1s (senza ML)
- **WordPress**: 2-5 secondi (upload media + post)

**Totale per articolo**: ~10-30 secondi

---

## 🐛 Troubleshooting Tecnico

### Problema: "unable to open database file" su Vercel
**Causa**: Directory `/tmp` non esiste o non scrivibile
**Soluzione**: 
```python
DEDUPE_DB.parent.mkdir(parents=True, exist_ok=True)
```

### Problema: ML dependencies troppo pesanti
**Causa**: sentence-transformers ~400MB
**Soluzione**: 
- Rimossi da requirements.txt
- Fallback senza ML deps
- Funziona ma con qualità inferiore

### Problema: Timeout su Vercel
**Causa**: Operazioni troppo lunghe (>30s)
**Soluzione**:
- Limita articoli processati per run
- Usa async processing per operazioni lunghe
- Considera Vercel Cron per batch processing

### Problema: Rate limiting da siti sorgente
**Causa**: Troppe richieste veloci
**Soluzione**:
- Aumenta `delay_between_requests` in `sources.yaml`
- Usa User-Agent identificabile
- Rispetta robots.txt (opzionale)

---

## 🔮 Estensioni Future

1. **Database Esterno**: PostgreSQL/MongoDB per persistenza su Vercel
2. **Queue System**: Redis/RabbitMQ per processing asincrono
3. **WebSocket**: Real-time updates invece di polling
4. **Multi-language**: Supporto per altre lingue oltre italiano
5. **Fact-checking**: Integrazione con API fact-checking
6. **Image Processing**: Download e ottimizzazione immagini
7. **SEO Optimization**: Analisi keyword e ottimizzazione automatica

---

## 📚 Riferimenti Tecnici

- **readability-lxml**: https://github.com/buriy/python-readability
- **sentence-transformers**: https://www.sbert.net/
- **WordPress REST API**: https://developer.wordpress.org/rest-api/
- **Vercel Python**: https://vercel.com/docs/runtimes/python
- **feedparser**: https://pythonhosted.org/feedparser/

---

*Documento aggiornato: Gennaio 2024*
