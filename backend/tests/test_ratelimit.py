"""Rate limits on endpoints that spend LLM/Jev/geocoder quota."""

from app.config import settings
from app.ratelimit import RateLimiter


async def test_limit_per_client_with_redis_down_falls_back_to_memory(monkeypatch, client):
    import app.ratelimit as ratelimit_module

    # Nothing listens on port 1: the limiter must fall back to counting in-process
    monkeypatch.setattr(ratelimit_module, "limiter", RateLimiter("redis://127.0.0.1:1/0"))
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_geocode_per_min", 2)

    codes = [(await client.get("/api/geocode", params={"q": "Go"})).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    blocked = await client.get("/api/geocode", params={"q": "Go"})
    assert int(blocked.headers["Retry-After"]) > 0
    assert "Too many requests" in blocked.json()["detail"]
    # Other endpoint groups have their own budget
    assert (await client.get("/api/health")).status_code == 200
    await ratelimit_module.limiter.close()
