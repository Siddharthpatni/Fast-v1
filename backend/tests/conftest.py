"""Shared fixtures: the dev GTFS/GBFS fixtures loaded into app state, and a fake DB session."""

import asyncio
from datetime import datetime

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import DATA_DIR, settings
from app.database import get_db
from app.main import app
from app.state import state

# A Monday inside the dev feed's calendar, morning peak
MONDAY_0710 = "2026-10-05T07:10:00"


@pytest.fixture(scope="session", autouse=True)
def loaded_state():
    """Load the dev fixtures once (the app lifespan isn't run by ASGITransport).

    Tests always use the dev fixtures, even when the environment points at real feeds.
    """
    settings.gtfs_path = str(DATA_DIR / "gtfs_dev")
    settings.gbfs_feeds = str(DATA_DIR / "gbfs_dev")
    settings.on_demand_zones_path = str(DATA_DIR / "on_demand_zones.json")
    settings.gtfs_rt_url = ""
    settings.rate_limit_enabled = False  # test_ratelimit turns it on explicitly
    state.load_static()
    asyncio.run(state.refresh_gbfs())
    # Hermetic: never call real models, even when keys are set (e.g. inside Docker).
    # Tests that exercise Jev or an LLM inject a client with a mock transport.
    state.llm = None
    settings.jev_api_key = ""
    assert state.feed is not None, state.feed_error
    return state


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class FakeSession:
    """Just enough of AsyncSession for the endpoints, so tests run without PostGIS."""

    def __init__(self):
        self.rows = []

    def add(self, obj) -> None:
        self.rows.append(obj)

    async def flush(self) -> None:
        pass

    async def refresh(self, obj) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = len(self.rows)
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime(2026, 10, 5, 7, 0)

    async def execute(self, _query):
        return _Result(r for r in self.rows if getattr(r, "status", "active") == "active")

    async def get(self, _model, ident):
        return next((r for r in self.rows if r.id == ident), None)

    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


@pytest.fixture
def fake_db():
    session = FakeSession()

    async def override():
        yield session

    app.dependency_overrides[get_db] = override
    yield session
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def client(fake_db):
    """Async test client backed by the fake DB."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
