#!/usr/bin/env python3
"""
Script di test per verificare che tutti gli import funzionino correttamente.
Esegui: python test_imports.py
"""

import sys

def test_imports():
    """Testa tutti gli import del progetto."""
    errors = []
    
    print("Testing imports...")
    
    # Test import base
    try:
        import yaml
        print("✅ yaml")
    except ImportError as e:
        errors.append(f"❌ yaml: {e}")
    
    try:
        import requests
        print("✅ requests")
    except ImportError as e:
        errors.append(f"❌ requests: {e}")
    
    try:
        from bs4 import BeautifulSoup
        print("✅ beautifulsoup4")
    except ImportError as e:
        errors.append(f"❌ beautifulsoup4: {e}")
    
    try:
        from readability import Document
        print("✅ readability-lxml")
    except ImportError as e:
        errors.append(f"❌ readability-lxml: {e}")
    
    try:
        import feedparser
        print("✅ feedparser")
    except ImportError as e:
        errors.append(f"❌ feedparser: {e}")
    
    try:
        import markdown
        print("✅ markdown")
    except ImportError as e:
        errors.append(f"❌ markdown: {e}")
    
    try:
        from dateutil import parser
        print("✅ python-dateutil")
    except ImportError as e:
        errors.append(f"❌ python-dateutil: {e}")
    
    # Test import moduli progetto
    try:
        from src import logger
        print("✅ src.logger")
    except ImportError as e:
        errors.append(f"❌ src.logger: {e}")
    
    try:
        from src import fetch_sources
        print("✅ src.fetch_sources")
    except ImportError as e:
        errors.append(f"❌ src.fetch_sources: {e}")
    
    try:
        from src import extract_article
        print("✅ src.extract_article")
    except ImportError as e:
        errors.append(f"❌ src.extract_article: {e}")
    
    try:
        from src import dedupe
        print("✅ src.dedupe")
    except ImportError as e:
        errors.append(f"❌ src.dedupe: {e}")
    
    try:
        from src import rewrite
        print("✅ src.rewrite")
    except ImportError as e:
        errors.append(f"❌ src.rewrite: {e}")
    
    try:
        from src import quality_gates
        print("✅ src.quality_gates")
    except ImportError as e:
        errors.append(f"❌ src.quality_gates: {e}")
    
    try:
        from src import wp_client
        print("✅ src.wp_client")
    except ImportError as e:
        errors.append(f"❌ src.wp_client: {e}")
    
    try:
        from src import pipeline
        print("✅ src.pipeline")
    except ImportError as e:
        errors.append(f"❌ src.pipeline: {e}")
    
    # Test import opzionali (non bloccanti)
    print("\nTesting optional imports...")
    
    try:
        from sentence_transformers import SentenceTransformer
        print("✅ sentence-transformers (opzionale)")
    except ImportError:
        print("⚠️  sentence-transformers non installato (verrà scaricato al primo uso)")
    
    try:
        import openai
        print("✅ openai (opzionale)")
    except ImportError:
        print("⚠️  openai non installato (necessario per rewrite con OpenAI)")
    
    try:
        import anthropic
        print("✅ anthropic (opzionale)")
    except ImportError:
        print("⚠️  anthropic non installato (necessario per rewrite con Anthropic)")
    
    # Risultato finale
    print("\n" + "="*50)
    if errors:
        print("❌ ERRORI TROVATI:")
        for error in errors:
            print(f"  {error}")
        print("\nInstalla le dipendenze mancanti con: pip install -r requirements.txt")
        return 1
    else:
        print("✅ Tutti gli import base funzionano correttamente!")
        return 0

if __name__ == "__main__":
    sys.exit(test_imports())
