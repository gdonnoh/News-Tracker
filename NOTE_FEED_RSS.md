# Note sui Feed RSS dei Siti Italiani

## Feed Confermati e Funzionanti

### ✅ Corriere della Sera
- **Homepage**: `https://www.corriere.it/rss/homepage.xml`
- **Politica**: `https://www.corriere.it/rss/politica.xml`
- **Cronache**: `https://www.corriere.it/rss/cronache.xml`
- **Economia**: `https://www.corriere.it/rss/economia.xml`
- **Altri feed disponibili**: Sport, Cultura, Spettacoli, Tecnologia, etc.
- **Pagina feed**: https://www.corriere.it/rss

### ✅ La Repubblica
- **Homepage**: `https://www.repubblica.it/rss/homepage/rss2.0.xml`
- **Politica**: `https://www.repubblica.it/rss/politica/rss2.0.xml`
- **Cronaca**: `https://www.repubblica.it/rss/cronaca/rss2.0.xml`
- **Economia**: `https://www.repubblica.it/rss/economia/rss2.0.xml`
- **Pagina feed**: https://www.repubblica.it/static/servizi/rss/index.html

## Feed da Verificare

### ⚠️ Il Messaggero
Gli URL dei feed potrebbero variare. Pattern comune:
- `https://www.ilmessaggero.it/rss/home.xml`
- `https://www.ilmessaggero.it/rss/cronaca.xml`
- `https://www.ilmessaggero.it/rss/politica.xml`

**Nota**: Verifica manualmente visitando il sito o controllando il codice HTML per tag `<link rel="alternate" type="application/rss+xml">`

### ⚠️ Sky TG24
Pattern possibili:
- `https://tg24.sky.it/rss/home.xml`
- `https://tg24.sky.it/rss/cronaca.xml`
- `https://www.skytg24.it/rss.xml`

**Nota**: Sky TG24 potrebbe non avere feed RSS pubblici. In tal caso, considera:
1. Scraping diretto delle pagine principali
2. Verifica se esiste una sezione "RSS" o "Feed" nel sito
3. Controlla se offrono API alternative

### ⚠️ Il Giornale
Pattern possibili:
- `https://www.ilgiornale.it/rss.xml`
- `https://www.ilgiornale.it/rss/politica.xml`

**Nota**: Verifica tramite aggregatori RSS o controllando direttamente il sito.

## Come Verificare un Feed RSS

1. **Test diretto**: Apri l'URL del feed nel browser. Dovresti vedere XML strutturato.

2. **Test con feedparser**:
   ```python
   import feedparser
   feed = feedparser.parse("https://www.corriere.it/rss/homepage.xml")
   print(feed.entries[0].title)  # Dovrebbe stampare un titolo
   ```

3. **Controlla HTML sorgente**: Cerca nel codice HTML della homepage:
   ```html
   <link rel="alternate" type="application/rss+xml" href="...">
   ```

## Se un Feed Non Funziona

1. **Disabilita temporaneamente** nel file `config/sources.yaml`:
   ```yaml
   - url: "..."
     name: "..."
     enabled: false  # Disabilitato fino a verifica
   ```

2. **Verifica manualmente** visitando il sito e cercando sezione "RSS" o "Feed"

3. **Considera scraping diretto** come alternativa (rispettando termini di servizio)

4. **Controlla log** della pipeline per vedere errori specifici:
   ```bash
   tail -f data/logs/pipeline_*.log
   ```

## Test Rapido Feed

Esegui questo comando per testare tutti i feed configurati:

```bash
python -m src.pipeline --dry-run --limit 1
```

Controlla i log per vedere quali feed funzionano e quali danno errore.

## Aggiornamento Feed

I feed RSS possono cambiare URL o struttura. Se noti che un feed smette di funzionare:

1. Verifica sul sito originale se l'URL è cambiato
2. Controlla se ci sono nuovi feed disponibili
3. Aggiorna `config/sources.yaml` con i nuovi URL
4. Testa con `--dry-run` prima di riabilitare
