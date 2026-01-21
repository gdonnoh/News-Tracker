#!/bin/bash
# Script per avviare il frontend mock

cd "$(dirname "$0")"
cd ..

echo "🚀 Avvio frontend mock server..."
echo ""
echo "📁 Directory: $(pwd)"
echo "🌐 URL: http://localhost:5000"
echo ""
echo "Premi Ctrl+C per fermare"
echo ""

source venv/bin/activate
python frontend/server.py
