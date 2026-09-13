#!/usr/bin/env python3
"""Launch Premium UI — run: python app.py  or  python -m main.app"""
import sys
from pathlib import Path

# Ensure project root and main package are importable
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "main"))

try:
    from main.app import run_app
except ModuleNotFoundError:
    # fallback when run as main module
    import importlib.util
    spec = importlib.util.spec_from_file_location("premium_app", BASE / "main" / "app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    run_app = mod.run_app

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="CardForge Premium UI")
    p.add_argument("--port", type=int, default=5000, help="Port (default 5000)")
    p.add_argument("--no-browser", action="store_true", help="Don't auto-open browser")
    args = p.parse_args()
    run_app(port=args.port, open_browser=not args.no_browser)
