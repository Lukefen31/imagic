"""Launch the imagic website (FastAPI + Uvicorn).

Usage:
    python run_website.py                 # http://localhost:8000
    python run_website.py --port 3000     # http://localhost:3000
    python run_website.py --host 0.0.0.0  # accessible on LAN
"""

from __future__ import annotations

import argparse


def main() -> None:
    parser = argparse.ArgumentParser(description="Launch the imagic website")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on code changes (dev mode)")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(
        "website.api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
