# Frontend Mock - News Pipeline Dashboard

Dashboard web semplice per visualizzare i risultati della pipeline senza bisogno di WordPress.

## Caratteristiche

- 📊 **Statistiche in tempo reale**: Totale articoli, estratti, creati, saltati, falliti
- 📰 **Visualizzazione articoli**: Card con immagini, titolo, excerpt, fonte
- 📝 **Log viewer**: Visualizzazione log pipeline in tempo reale
- 📈 **Statistiche dettagliate**: Report JSON formattato

## Installazione

```bash
# Installa dipendenze Flask (se non già installate)
pip install flask flask-cors
```

## Utilizzo

### Avvia il server

```bash
cd frontend
python server.py
```

Il server si avvierà su `http://localhost:5000`

### Apri nel browser

Apri `http://localhost:5000` nel tuo browser per vedere la dashboard.

## API Endpoints

- `GET /` - Pagina HTML principale
- `GET /api/articles` - Lista articoli estratti
- `GET /api/logs` - Log recenti (ultimi 100)
- `GET /api/stats` - Statistiche pipeline
- `GET /api/article/<filename>` - Singolo articolo

## Struttura

```
frontend/
├── index.html      # Interfaccia web
├── server.py       # Server Flask
└── README.md       # Questa guida
```

## Note

- Il server legge i file JSON dalla directory `data/cache/`
- I log vengono letti da `data/logs/audit_*.jsonl`
- Le statistiche vengono lette da `data/logs/report_*.json`

## Sviluppi Futuri

- [ ] Filtri per fonte, data, categoria
- [ ] Ricerca articoli
- [ ] Visualizzazione dettaglio articolo completo
- [ ] Grafici statistiche
- [ ] Export dati
