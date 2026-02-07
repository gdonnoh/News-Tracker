"""Vercel serverless function entry point."""
import sys
from pathlib import Path

# Add project root and frontend directory to import path
_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_root / "frontend"))

from server import app  # noqa: E402
