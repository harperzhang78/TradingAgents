"""FastAPI application entrypoint for the TradingAgents Dashboard."""
from __future__ import annotations

import argparse
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from webapp.api import router as api_router
from webapp.config import DEFAULT_HOST, DEFAULT_PORT
from webapp.db import init_db
from webapp.scheduler import scheduler

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager: initialize database and background scheduler on startup."""
    init_db()
    scheduler.start()
    yield
    scheduler.stop()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="TradingAgents Dashboard",
        description="Autonomous multi-agent stock analysis and auto-trading platform",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Enable CORS for local development flexibility
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # API routes
    app.include_router(api_router)

    # Static files and root SPA route
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        index_file = STATIC_DIR / "index.html"
        if index_file.exists():
            return FileResponse(index_file)
        return {"message": "TradingAgents Dashboard API is running. UI not found."}

    return app


app = create_app()


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description="TradingAgents Web Dashboard Server")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"Host to bind (default: {DEFAULT_HOST})")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port to bind (default: {DEFAULT_PORT})")
    parser.add_argument("--reload", action="store_true", help="Enable auto-reload for development")
    args = parser.parse_args()

    print(f"🚀 Starting TradingAgents Dashboard on http://{args.host}:{args.port}")
    uvicorn.run("webapp.app:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
