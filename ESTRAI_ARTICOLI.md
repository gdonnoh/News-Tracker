# Come Estrarre Altri Articoli

## Comandi Rapidi

### 1. Estrai e Riscrivi 10 Articoli (Dry-Run)
```bash
cd "/Users/gdonnoh/Desktop/News Tracker"
source venv/bin/activate
python -m src.pipeline --dry-run --limit 10
```

### 2. Estrai 20 Articoli (Solo Estrazione)
```bash
python -m src.pipeline --dry-run --limit 20
```

### 3. Processa Tutti gli Articoli Disponibili
```bash
python -m src.pipeline --dry-run
```

### 4. Crea Post WordPress (Senza Dry-Run)
```bash
# Prima configura WORDPRESS_URL, USERNAME, APP_PASSWORD in .env
python -m src.pipeline --limit 5
```

## Opzioni Disponibili

- `--limit N`: Limita il numero di articoli da processare
- `--dry-run`: Non crea post WordPress (solo estrazione e riscrittura)
- `--config-dir PATH`: Specifica directory config (default: ./config)

## Feed RSS Configurati

Attualmente configurati in `config/sources.yaml`:

- **Corriere della Sera**: Homepage, Politica, Cronache, Economia
- **La Repubblica**: Homepage, Politica, Cronaca, Economia  
- **Il Messaggero**: Homepage, Cronaca, Politica
- **Il Giornale**: Homepage, Politica

## Cosa Succede Durante l'Esecuzione

1. **FETCH**: Scarica URL da feed RSS configurati
2. **EXTRACT**: Estrae contenuto (titolo, testo, immagini, metadata)
3. **DEDUPE**: Verifica duplicati (evita riprocessare)
4. **REWRITE**: Riscrive con LLM (se OpenAI API configurata)
5. **QUALITY**: Controlla qualità e policy
6. **WP_POST**: Crea post WordPress (solo se non --dry-run)

## Visualizzare Risultati

Dopo l'esecuzione:

1. **Dashboard Frontend**: Ricarica http://localhost:5000
2. **File Cache**: Controlla `data/cache/extracted_*.json`
3. **File Riscritti**: Controlla `data/cache/rewritten_*.json`
4. **Log**: Controlla `data/logs/pipeline_*.log`

## Esempi Pratici

### Estrai 5 articoli nuovi
```bash
python -m src.pipeline --dry-run --limit 5
```

### Estrai solo da Corriere
Modifica `config/sources.yaml` e disabilita altri feed, poi:
```bash
python -m src.pipeline --dry-run --limit 10
```

### Processa articoli ogni ora (Cron)
Aggiungi a crontab:
```bash
0 * * * * cd /Users/gdonnoh/Desktop/News\ Tracker && source venv/bin/activate && python -m src.pipeline --dry-run --limit 10 >> logs/cron.log 2>&1
```

## Troubleshooting

### Nessun articolo estratto
- Verifica connessione internet
- Controlla feed RSS in `config/sources.yaml`
- Verifica log: `tail -f data/logs/pipeline_*.log`

### Articoli già processati
- Normale: il sistema evita duplicati
- Controlla `data/dedupe.db` per vedere URL processati

### Riscrittura non funziona
- Verifica `OPENAI_API_KEY` in `.env`
- Controlla crediti API OpenAI
- Vedi log per errori specifici
