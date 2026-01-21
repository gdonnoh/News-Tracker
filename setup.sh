#!/bin/bash
# Script di setup per News Pipeline

set -e

echo "=== News Pipeline Setup ==="
echo ""

# Verifica Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 non trovato. Installa Python 3.8+ e riprova."
    exit 1
fi

echo "✅ Python trovato: $(python3 --version)"

# Crea ambiente virtuale
if [ ! -d "venv" ]; then
    echo "📦 Creazione ambiente virtuale..."
    python3 -m venv venv
else
    echo "✅ Ambiente virtuale già esistente"
fi

# Attiva ambiente virtuale
echo "🔧 Attivazione ambiente virtuale..."
source venv/bin/activate

# Installa dipendenze
echo "📥 Installazione dipendenze..."
pip install --upgrade pip
pip install -r requirements.txt

# Crea directory dati
echo "📁 Creazione directory dati..."
mkdir -p data/cache
mkdir -p data/logs

# Copia file env se non esiste
if [ ! -f ".env" ]; then
    echo "📝 Creazione file .env da template..."
    cp config/env.example .env
    echo "⚠️  IMPORTANTE: Modifica .env con le tue credenziali!"
else
    echo "✅ File .env già esistente"
fi

echo ""
echo "=== Setup completato! ==="
echo ""
echo "Prossimi passi:"
echo "1. Modifica .env con le tue credenziali WordPress e LLM"
echo "2. Configura config/sources.yaml con i feed RSS desiderati"
echo "3. Esegui: python -m src.pipeline --dry-run"
echo ""
