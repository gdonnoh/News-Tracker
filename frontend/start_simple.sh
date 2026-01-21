#!/bin/bash
# Script semplice per avviare il frontend

cd "$(dirname "$0")/.."

# Attiva venv se esiste
if [ -d "venv" ]; then
    source venv/bin/activate
fi

# Vai nella directory frontend
cd frontend

# Avvia server
python3 server.py
