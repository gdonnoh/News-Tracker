# News Pipeline

Pipeline automatizzata per raccogliere notizie dal web, riscriverle in modo originale e pubblicarle su WordPress come draft per revisione umana.

## Caratteristiche

- ✅ **Raccolta automatica** da feed RSS e whitelist domini
- ✅ **Estrazione contenuto** intelligente con readability
- ✅ **Deduplicazione** basata su hash e similarità semantica
- ✅ **Riscrittura originale** con LLM (OpenAI/Anthropic)
- ✅ **Quality gates** per controlli qualità e policy
- ✅ **Integrazione WordPress** via REST API
- ✅ **Logging completo** per audit trail
- ✅ **Nessun banner nel contenuto** - gestione advertising separata

## Struttura Progetto

```
News Tracker/
├── config/
│   ├── sources.yaml          # Configurazione feed RSS e whitelist
│   ├── categories.yaml        # Mapping categorie WordPress
│   └── env.example            # Template variabili ambiente
├── src/
│   ├── __init__.py
│   ├── logger.py              # Sistema logging e audit
│   ├── fetch_sources.py       # Raccolta URL da RSS
│   ├── extract_article.py     # Estrazione contenuto
│   ├── dedupe.py              # Deduplicazione
│   ├── rewrite.py             # Riscrittura con LLM
│   ├── quality_gates.py       # Controlli qualità
│   ├── wp_client.py           # Client WordPress REST API
│   └── pipeline.py            # Orchestrazione end-to-end
├── data/                      # Directory dati (creata automaticamente)
│   ├── cache/                 # HTML raw e JSON estratti
│   ├── logs/                  # Log e audit trail
│   └── dedupe.db              # Database deduplicazione
├── requirements.txt
├── .gitignore
└── README.md
```

## Installazione

### 1. Clona/Scarica il progetto

```bash
cd "News Tracker"
```

### 2. Crea ambiente virtuale (consigliato)

```bash
python3 -m venv venv
source venv/bin/activate  # Su Windows: venv\Scripts\activate
```

### 3. Installa dipendenze

```bash
pip install -r requirements.txt
```

**Nota**: Il modello `sentence-transformers` verrà scaricato automaticamente al primo utilizzo (~400MB).

### 4. Configurazione

#### a) Copia file ambiente

```bash
cp config/env.example .env
```

#### b) Modifica `.env` con le tue credenziali

```bash
# WordPress
WORDPRESS_URL=https://tuo-sito.com
WORDPRESS_USERNAME=tuo_username
WORDPRESS_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx

# LLM (scegli uno)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Oppure
# ANTHROPIC_API_KEY=sk-ant-...
# ANTHROPIC_MODEL=claude-3-haiku-20240307

# Altri settings
SIMILARITY_THRESHOLD=0.85
MIN_ARTICLE_LENGTH=200
MAX_ARTICLE_LENGTH=2000
DRY_RUN=false
ARTICLES_LIMIT=20
```

#### c) Configura feed RSS

Modifica `config/sources.yaml`:

```yaml
rss_feeds:
  - url: "https://feeds.bbci.co.uk/news/rss.xml"
    name: "BBC News"
    enabled: true
  - url: "https://rss.cnn.com/rss/edition.rss"
    name: "CNN"
    enabled: true
```

#### d) Configura categorie WordPress

Modifica `config/categories.yaml` per mappare categorie LLM a categorie WordPress.

### 5. Setup WordPress

#### Application Password

1. Vai su WordPress Admin → Utenti → Il tuo profilo
2. Scorri fino a "Application Passwords"
3. Crea nuova password con nome "News Pipeline"
4. Copia la password generata nel `.env`

**Importante**: L'utente WordPress deve avere permessi per:
- Creare/modificare post (Editor o Author)
- Upload media
- Creare categorie (se necessario)

**Sicurezza**: Per produzione, crea un utente dedicato con permessi minimi (Author) e usa solo Application Password.

## Utilizzo

### Esecuzione base

```bash
python -m src.pipeline
```

### Opzioni CLI

```bash
# Limita numero articoli
python -m src.pipeline --limit 10

# Dry run (non crea post WordPress)
python -m src.pipeline --dry-run

# Specifica directory config
python -m src.pipeline --config-dir ./config
```

### Variabili ambiente

Tutte le opzioni possono essere configurate via `.env`:

- `DRY_RUN=true` - Dry run mode
- `ARTICLES_LIMIT=20` - Limite articoli
- `SIMILARITY_THRESHOLD=0.85` - Soglia similarità (0-1)
- `MIN_ARTICLE_LENGTH=200` - Parole minime
- `MAX_ARTICLE_LENGTH=2000` - Parole massime
- `LOG_LEVEL=INFO` - Livello logging (DEBUG, INFO, WARNING, ERROR)

## Flusso Pipeline

1. **Fetch Sources** → Raccoglie URL da feed RSS
2. **Extract** → Scarica e estrae contenuto (titolo, testo, immagini, metadata)
3. **Dedupe** → Verifica duplicati (hash + similarità semantica)
4. **Rewrite** → Riscrive con LLM (output JSON strutturato)
5. **Quality Gates** → Controlla similarità, sanità, policy/rischio
6. **WordPress Post** → Crea post draft con meta fields

### Output

Ogni articolo processato genera:

- **Cache HTML**: `data/cache/raw_*.html` (HTML originale)
- **Cache JSON**: `data/cache/extracted_*.json` (dati estratti)
- **Log audit**: `data/logs/audit_YYYYMMDD.jsonl` (JSON line-by-line)
- **Report**: `data/logs/report_*.json` (statistiche run)

## Esempio Output JSON

### Articolo estratto (`extracted_*.json`)

```json
{
  "url": "https://example.com/article",
  "canonical_url": "https://example.com/article",
  "title": "Titolo originale articolo",
  "text": "Testo completo estratto...",
  "html": "<article>...</article>",
  "images": [
    "https://example.com/image1.jpg",
    "https://example.com/image2.jpg"
  ],
  "published_at": "2024-01-15T10:30:00",
  "author": "Nome Autore",
  "source_name": "BBC News",
  "extracted_at": "2024-01-15T12:00:00"
}
```

### Articolo riscritto (output LLM)

```json
{
  "headline": "Titolo originale e accattivante",
  "lead": "Due-tre frasi di introduzione che riassumono i punti chiave dell'articolo.",
  "body_markdown": "## Introduzione\n\nParagrafo introduttivo...\n\n## Sviluppo\n\nParagrafi di sviluppo...",
  "tags": ["tecnologia", "innovazione", "AI"],
  "category": "tecnologia",
  "meta_title": "Titolo SEO ottimizzato",
  "meta_description": "Descrizione SEO per risultati di ricerca",
  "word_count": 650,
  "rewritten_at": "2024-01-15T12:05:00"
}
```

### Audit log (`audit_*.jsonl`)

```json
{
  "timestamp": "2024-01-15T12:05:30",
  "operation": "pipeline",
  "url": "https://example.com/article",
  "status": "created",
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

## Meta Fields WordPress

Ogni post creato include meta fields custom:

- `source_name` - Nome fonte (es: "BBC News")
- `source_url` - URL articolo originale
- `source_published_at` - Data pubblicazione originale
- `ingest_timestamp` - Timestamp processing pipeline
- `source_hash` - Hash per deduplicazione
- `ai_version` - Versione pipeline AI
- `risk_level` - Livello rischio ("low", "medium", "high")
- `needs_review` - Flag revisione richiesta ("1" o "0")
- `original_title` - Titolo originale articolo

## Troubleshooting

### Errore: "LLM API key non configurata"

Il sistema funziona in "stub mode" senza LLM. Configura `OPENAI_API_KEY` o `ANTHROPIC_API_KEY` nel `.env`.

### Errore: "WordPress authentication failed"

- Verifica `WORDPRESS_URL` (senza trailing slash)
- Verifica Application Password (formato: `xxxx xxxx xxxx xxxx xxxx xxxx`)
- Verifica permessi utente WordPress

### Errore: "Model not found" (sentence-transformers)

Il modello viene scaricato automaticamente al primo uso. Assicurati di avere ~500MB spazio e connessione internet.

### Post non creati in dry-run

Comportamento atteso. Rimuovi `--dry-run` o imposta `DRY_RUN=false` nel `.env`.

### Rate limiting

Il sistema include rate limiting automatico. Se ricevi errori 429, aumenta `delay_between_requests` in `config/sources.yaml`.

## Sicurezza

- ✅ Nessun banner/script nel contenuto (gestione advertising separata)
- ✅ Contenuto riscritto originale (no copy-paste)
- ✅ Permessi WordPress minimi (solo draft, no publish)
- ✅ Logging completo per audit
- ✅ Validazione input e sanitizzazione HTML

## Sviluppi Futuri

- [ ] Auto-publish su categorie "low risk"
- [ ] Supporto più provider LLM
- [ ] Dashboard web per monitoraggio
- [ ] Notifiche email per articoli ad alto rischio
- [ ] Integrazione con fact-checking API

## Licenza

Progetto interno - uso riservato.

## Supporto

Per problemi o domande, consulta i log in `data/logs/` o contatta il team.
