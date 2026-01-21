# Guida Rapida - Come Usare la Pipeline

## 1. Configurazione Iniziale

### A) File `.env` (nella root del progetto)

Apri `.env` e inserisci le tue credenziali:

```bash
# WordPress (OBBLIGATORIO per creare post)
WORDPRESS_URL=https://tuo-sito.com
WORDPRESS_USERNAME=tuo_username
WORDPRESS_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx

# LLM (OBBLIGATORIO per riscrittura originale)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Opzionali
SIMILARITY_THRESHOLD=0.85
MIN_ARTICLE_LENGTH=200
MAX_ARTICLE_LENGTH=2000
DRY_RUN=false
ARTICLES_LIMIT=20
```

**Come ottenere Application Password WordPress:**
1. Vai su WordPress Admin → Utenti → Il tuo profilo
2. Scorri fino a "Application Passwords"
3. Crea nuova password con nome "News Pipeline"
4. Copia la password generata (formato: `xxxx xxxx xxxx xxxx xxxx xxxx`)

### B) Configura Feed RSS (`config/sources.yaml`)

Modifica i feed RSS che vuoi monitorare:

```yaml
rss_feeds:
  - url: "https://feeds.bbci.co.uk/news/rss.xml"
    name: "BBC News"
    enabled: true
  - url: "https://rss.cnn.com/rss/edition.rss"
    name: "CNN"
    enabled: true
  # Aggiungi altri feed qui
```

## 2. Esecuzione

### Attiva ambiente virtuale

```bash
cd "/Users/gdonnoh/Desktop/News Tracker"
source venv/bin/activate
```

### Test Dry-Run (consigliato per iniziare)

Esegue tutto senza creare post WordPress:

```bash
python -m src.pipeline --dry-run --limit 5
```

Questo ti permette di:
- Verificare che tutto funzioni
- Vedere i log di processing
- Controllare gli output in `data/cache/`

### Esecuzione Normale

```bash
# Processa 20 articoli (default)
python -m src.pipeline

# Limita a 10 articoli
python -m src.pipeline --limit 10

# Processa tutti gli articoli disponibili
python -m src.pipeline --limit 100
```

## 3. Cosa Succede Durante l'Esecuzione

La pipeline esegue questi step per ogni articolo:

1. **FETCH** → Scarica URL da feed RSS
2. **EXTRACT** → Estrae contenuto (titolo, testo, immagini)
3. **DEDUPE** → Verifica duplicati
4. **REWRITE** → Riscrive con LLM (se configurato)
5. **QUALITY** → Controlla qualità e policy
6. **WP_POST** → Crea post WordPress in DRAFT

## 4. Output e Log

### File generati:

- **Cache HTML**: `data/cache/raw_*.html` (HTML originale)
- **Cache JSON**: `data/cache/extracted_*.json` (dati estratti)
- **Log audit**: `data/logs/audit_YYYYMMDD.jsonl` (traccia completa)
- **Report**: `data/logs/report_*.json` (statistiche run)

### Esempio log:

```bash
# Visualizza log in tempo reale
tail -f data/logs/pipeline_*.log

# Visualizza audit trail
cat data/logs/audit_*.jsonl | jq '.'
```

## 5. Esempi Pratici

### Esempio 1: Test completo senza WordPress

```bash
# 1. Configura .env con solo LLM (senza WordPress)
DRY_RUN=true

# 2. Esegui
python -m src.pipeline --dry-run --limit 3

# 3. Controlla output
ls -la data/cache/
cat data/logs/audit_*.jsonl
```

### Esempio 2: Processa solo feed specifico

Modifica `config/sources.yaml` per abilitare solo un feed:

```yaml
rss_feeds:
  - url: "https://feeds.bbci.co.uk/news/rss.xml"
    name: "BBC News"
    enabled: true
  - url: "https://rss.cnn.com/rss/edition.rss"
    name: "CNN"
    enabled: false  # Disabilitato
```

### Esempio 3: Verifica import

```bash
python test_imports.py
```

## 6. Troubleshooting

### Errore: "LLM API key non configurata"

Il sistema funziona in "stub mode" senza riscrittura originale. Configura `OPENAI_API_KEY` nel `.env`.

### Errore: "WordPress authentication failed"

- Verifica `WORDPRESS_URL` (senza trailing slash)
- Verifica Application Password (formato corretto)
- Verifica permessi utente WordPress

### Post non creati in dry-run

Comportamento normale. Rimuovi `--dry-run` o imposta `DRY_RUN=false` nel `.env`.

### Modello sentence-transformers lento al primo uso

Il modello viene scaricato automaticamente (~400MB) al primo utilizzo. Attendi il download.

## 7. Comandi Utili

```bash
# Visualizza help
python -m src.pipeline --help

# Test import moduli
python test_imports.py

# Controlla configurazione
cat config/sources.yaml
cat config/categories.yaml

# Pulisci cache (se necessario)
rm -rf data/cache/*.html data/cache/*.json

# Visualizza statistiche ultima run
cat data/logs/report_*.json | jq '.'
```

## 8. Automazione (Cron Job)

Per eseguire automaticamente ogni ora:

```bash
# Aggiungi a crontab
crontab -e

# Aggiungi questa riga:
0 * * * * cd /Users/gdonnoh/Desktop/News\ Tracker && source venv/bin/activate && python -m src.pipeline --limit 10 >> logs/cron.log 2>&1
```

## 9. Verifica Post WordPress

Dopo l'esecuzione:

1. Vai su WordPress Admin → Post
2. Filtra per "Draft"
3. Verifica i post creati
4. Controlla meta fields custom:
   - `source_url`: URL originale
   - `source_name`: Nome fonte
   - `risk_level`: Livello rischio
   - `needs_review`: Flag revisione

## 10. Prossimi Passi

1. ✅ Configura `.env` con credenziali WordPress e LLM
2. ✅ Modifica `config/sources.yaml` con i tuoi feed RSS
3. ✅ Esegui test dry-run: `python -m src.pipeline --dry-run --limit 5`
4. ✅ Verifica output in `data/cache/` e `data/logs/`
5. ✅ Esegui prima run reale: `python -m src.pipeline --limit 10`
6. ✅ Verifica post creati su WordPress
