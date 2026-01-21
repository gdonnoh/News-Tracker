# Deploy su Vercel

Questa guida spiega come deployare il News Pipeline su Vercel con notifiche email.

## Prerequisiti

1. Account Vercel (gratuito)
2. Account Resend (per email) - https://resend.com
3. Repository GitHub/GitLab/Bitbucket

## Setup Vercel

### 1. Installa Vercel CLI

```bash
npm i -g vercel
```

### 2. Login su Vercel

```bash
vercel login
```

### 3. Deploy

```bash
vercel
```

Oppure collega il repository direttamente dal dashboard Vercel.

## Configurazione Variabili d'Ambiente

Nel dashboard Vercel, vai su **Settings > Environment Variables** e aggiungi:

### Configurazione Base
```
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

### Notifiche Email (Resend - Consigliato)
```
EMAIL_NOTIFICATIONS_ENABLED=true
EMAIL_PROVIDER=resend
EMAIL_RECIPIENT=your-email@example.com
EMAIL_FROM=noreply@yourdomain.com
RESEND_API_KEY=re_...
```

### Notifiche Email (SendGrid - Alternativa)
```
EMAIL_NOTIFICATIONS_ENABLED=true
EMAIL_PROVIDER=sendgrid
EMAIL_RECIPIENT=your-email@example.com
EMAIL_FROM=noreply@yourdomain.com
SENDGRID_API_KEY=SG....
```

### Notifiche Email (SMTP - Alternativa)
```
EMAIL_NOTIFICATIONS_ENABLED=true
EMAIL_PROVIDER=smtp
EMAIL_RECIPIENT=your-email@example.com
EMAIL_FROM=noreply@yourdomain.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password
```

## Setup Resend (Consigliato per Vercel)

1. Vai su https://resend.com
2. Crea un account gratuito
3. Ottieni API Key dalla dashboard
4. Verifica il dominio (opzionale ma consigliato)
5. Aggiungi `RESEND_API_KEY` alle variabili d'ambiente Vercel

## Limitazioni Vercel

⚠️ **Importante**: Vercel ha alcune limitazioni:

1. **Serverless Functions**: Le funzioni hanno timeout di 10 secondi (Hobby) o 60 secondi (Pro)
2. **Monitoraggio Continuo**: Il monitoraggio continuo potrebbe non funzionare su Vercel perché:
   - Le funzioni serverless non mantengono stato persistente
   - Non c'è un processo in background continuo

### Soluzioni Alternative

Per il monitoraggio continuo su Vercel, considera:

1. **Vercel Cron Jobs**: Usa `vercel.json` per schedulare controlli periodici
2. **Servizi esterni**: Usa servizi come:
   - **Cron-job.org** (gratuito)
   - **EasyCron** (gratuito)
   - **GitHub Actions** (gratuito)
   - **Railway** o **Render** per il monitoraggio continuo

### Esempio: Cron Job Vercel

Aggiungi a `vercel.json`:

```json
{
  "crons": [
    {
      "path": "/api/cron-check-feeds",
      "schedule": "*/5 * * * *"
    }
  ]
}
```

Crea endpoint `/api/cron-check-feeds` che esegue un controllo singolo.

## Test Email

Dopo il deploy, puoi testare le email con:

```bash
curl -X POST https://your-app.vercel.app/api/test-email
```

## Monitoraggio

Il monitoraggio continuo funziona meglio su:
- **Railway** (https://railway.app) - Supporta processi continui
- **Render** (https://render.com) - Supporta servizi web continui
- **DigitalOcean App Platform** - Supporta processi continui
- **AWS Lambda + EventBridge** - Per cron jobs serverless

## Note

- Le email vengono inviate quando il monitor trova nuovi articoli
- Massimo 10 articoli per email (per evitare email troppo lunghe)
- Le notifiche sono opzionali e possono essere disabilitate
