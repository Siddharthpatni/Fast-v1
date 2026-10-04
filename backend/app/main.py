"""FastMile — Unified Mobility Platform for LLM-Guided Routing.

Main FastAPI application entry point.
"""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.config import Config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.exc import SQLAlchemyError

from alembic import command
from app.api import router
from app.config import settings
from app.database import async_session, engine
from app.ratelimit import limiter
from app.services.decisions import close_jev_client
from app.services.ingestion.db_sync import sync_reference_data
from app.state import state

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def _migrate() -> None:
    """Bring the database schema up to date (runs Alembic's own event loop, so in a thread)."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(ALEMBIC_INI.parent / "alembic"))
    command.upgrade(config, "head")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: create tables, load feeds and models, start live refresh. Shutdown: clean up."""
    await asyncio.to_thread(state.load_static)
    state.init_clients()
    # Key and model check in the background: /health shows "ready" or the error at once,
    # instead of "not used yet" until the first report (it costs no request quota)
    probe = asyncio.create_task(state.llm.probe()) if state.llm is not None else None
    await state.refresh_gbfs()
    # Realtime (a ~50 MB nationwide feed) loads in the background loop, so startup is fast
    try:
        # Schema comes from Alembic migrations (alembic/versions), applied on every start
        await asyncio.to_thread(_migrate)
        if state.feed is not None:
            async with async_session() as session:
                stops, routes = await sync_reference_data(session, state.feed)
                logger.info("Synced %d stops and %d routes to PostGIS", stops, routes)
    except (SQLAlchemyError, OSError) as exc:
        logger.error("Database unavailable; routing works, disruptions are not stored: %s", exc)

    refresh = asyncio.create_task(state.run_refresh_loop())
    yield
    for task in (refresh, probe):
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    await state.close()
    await close_jev_client()
    await limiter.close()
    await engine.dispose()


app = FastAPI(
    title="FastMile API",
    description=(
        "Unified Mobility Platform for LLM-Guided Routing. "
        "Provides intermodal route planning with real-time disruption handling."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router, prefix="/api")
