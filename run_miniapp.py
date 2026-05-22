#!/usr/bin/env python3
"""Запуск HTTP-сервера Mini App (FastAPI + uvicorn)."""
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("MINIAPP_HOST", "0.0.0.0")
    port = int(os.environ.get("MINIAPP_PORT", "8080"))
    uvicorn.run(
        "miniapp.api:app",
        host=host,
        port=port,
        reload=os.environ.get("MINIAPP_RELOAD", "").lower() in ("1", "true", "yes"),
    )
