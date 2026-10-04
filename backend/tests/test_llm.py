"""Tests for LLM extraction (mocked providers), the rule-based fallback, and network matching."""

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.config import DATA_DIR, settings
from app.services.disruptions import match_lines, match_stops, normalize_stop, window
from app.services.ingestion.gbfs import _feed_urls
from app.services.llm import FallbackLLMClient, LLMClient, extract_disruption, extract_with_rules
from app.state import state

SAMPLES = json.loads(Path(DATA_DIR / "sample_disruptions" / "disruptions.json").read_text())
EXTRACTION = {
    "summary": "Bus 830 is blocked after an accident.",
    "affected_lines": ["830"],
    "affected_stops": ["Goslar Bahnhof"],
    "start_time": "08:15",
    "end_time": "10:00",
    "duration_unknown": False,
    "disruption_type": "accident",
    "effect": "suspended",
    "delay_minutes": None,
    "alternatives": ["via Bad Harzburg"],
}


async def test_ollama_request_and_parse():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": json.dumps(EXTRACTION)}})

    client = LLMClient(
        "ollama",
        "qwen2.5:7b-instruct",
        "http://ollama:11434",
        transport=httpx.MockTransport(handler),
    )
    result = await extract_disruption(client, SAMPLES[0]["text"])
    await client.close()

    assert seen["url"] == "http://ollama:11434/api/chat"
    assert seen["body"]["model"] == "qwen2.5:7b-instruct"
    assert seen["body"]["stream"] is False
    assert seen["body"]["format"]["required"][0] == "summary"  # Schema-constrained decoding
    assert result.method == "llm" and result.affected_lines == ["830"]


async def test_openai_request_uses_strict_json_schema():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["auth"] = str(request.url), request.headers["Authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(EXTRACTION)}}]}
        )

    client = LLMClient(
        "openai",
        "gpt-4o-mini",
        "https://api.example/v1",
        api_key="sk-test",
        transport=httpx.MockTransport(handler),
    )
    result = await extract_disruption(client, SAMPLES[0]["text"])
    await client.close()

    assert seen["url"] == "https://api.example/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    fmt = seen["body"]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    schema = fmt["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])  # Strict mode requires all
    assert result.effect == "suspended"


async def test_llm_failure_falls_back_to_rules():
    client = LLMClient("ollama", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    result = await extract_disruption(client, SAMPLES[0]["text"])
    await client.close()
    assert result.method == "rules" and result.affected_lines == ["830"]


@pytest.mark.parametrize(
    ("index", "effect", "lines"),
    [
        (0, "suspended", ["830"]),
        (1, "stops_not_served", ["11", "12", "15"]),
        (2, "delayed", ["S1"]),
        (3, "suspended", ["RE1", "RE30", "RB46", "S1", "S2"]),
        (4, "reduced_availability", []),
    ],
)
def test_rules_on_sample_reports(index, effect, lines):
    result = extract_with_rules(SAMPLES[index]["text"])
    assert result.effect == effect
    assert result.affected_lines == lines


def test_rules_delay_and_times():
    result = extract_with_rules(SAMPLES[2]["text"])
    assert result.delay_minutes == 5
    accident = extract_with_rules(SAMPLES[0]["text"])
    assert (accident.start_time, accident.end_time) == ("08:15", "10:00")


def test_matching_to_network():
    net = state.network_for(datetime(2026, 10, 5).date())
    assert match_lines(net, ["Linie 830", "RE10", "unknown"]) == ["r830", "rRE10"]
    assert normalize_stop("Braunschweig Hauptbahnhof") == normalize_stop("Braunschweig Hbf")
    assert match_stops(net, ["Braunschweig Hauptbahnhof"]) == ["BS_HBF"]
    assert match_stops(net, ["Marktplatz", "Rathaus"]) == ["GS_MARKT", "GS_RATHAUS"]
    assert match_stops(net, ["Clausthal-Zellerfeld ZOB"]) == ["CZ_ZOB"]
    assert match_stops(net, ["Nowhere"]) == []


def test_window_spans_midnight():
    tz = ZoneInfo("Europe/Berlin")
    start, end = window(datetime(2026, 10, 5, 21, 0, tzinfo=tz), "22:00", "02:00")
    assert end.day == 6 and end.hour == 2 and start.hour == 22
    start, end = window(datetime(2026, 10, 5, 9, 0, tzinfo=tz), None, None)
    assert start.hour == 9 and end is None


def test_gbfs_discovery_v2_and_v3():
    v2 = {"data": {"de": {"feeds": [{"name": "station_status", "url": "https://x/ss.json"}]}}}
    v3 = {"data": {"feeds": [{"name": "vehicle_status", "url": "https://x/vs.json"}]}}
    assert _feed_urls(v2) == {"station_status": "https://x/ss.json"}
    assert _feed_urls(v3) == {"vehicle_status": "https://x/vs.json"}


def test_gbfs_dev_snapshot():
    found = state.gbfs.nearest_available(51.9086, 10.4283, "bike", 0.5)
    assert found is not None and found[0].name == "Goslar Bahnhof"
    assert state.gbfs.nearest_available(51.9086, 10.4283, "car_share", 0.5) is not None
    assert state.gbfs.nearest_available(52.5, 13.4, "bike", 0.5) is None  # Berlin: nothing


async def test_openrouter_request_uses_free_model_fallbacks_and_parses_fenced_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["headers"] = str(request.url), request.headers
        seen["body"] = json.loads(request.content)
        content = "```json\n" + json.dumps(EXTRACTION) + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    client = LLMClient(
        "openrouter",
        "a/model-one:free,b/model-two:free",
        api_key="or-key",
        transport=httpx.MockTransport(handler),
    )
    result = await extract_disruption(client, SAMPLES[0]["text"])
    await client.close()

    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer or-key"
    body = seen["body"]
    assert body["model"] == "a/model-one:free"
    assert body["models"] == ["a/model-one:free", "b/model-two:free"]
    assert body["provider"] == {"require_parameters": True}
    assert body["response_format"]["type"] == "json_schema"
    assert result.method == "llm" and result.affected_lines == ["830"]


def _openrouter_catalog(key_status: int = 200):
    """Mock /key and /models; any chat call fails the test (the probe must cost no quota)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/key"):
            return httpx.Response(key_status, json={"data": {"limit_remaining": 4.9}})
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "a/one:free"}, {"id": "paid/x"}]})
        raise AssertionError(f"unexpected call {request.url}")

    return httpx.MockTransport(handler)


async def test_probe_reports_ready_and_drops_missing_models():
    client = LLMClient("openrouter", "gone/model:free,a/one:free", transport=_openrouter_catalog())
    assert client.describe()["status"] == "untested"
    await client.probe()
    await client.close()
    assert client.describe()["status"] == "ready"
    assert client.models == ["a/one:free"] and client.model == "a/one:free"


async def test_probe_surfaces_a_rejected_key():
    client = LLMClient("openrouter", "a/one:free", transport=_openrouter_catalog(401))
    await client.probe()
    await client.close()
    info = client.describe()
    assert info["status"] == "error" and "rejected the API key" in info["last_error"]


async def test_chain_is_ready_when_one_link_passes_the_probe():
    chain = FallbackLLMClient(
        [
            LLMClient("openrouter", "gone/model:free", transport=_openrouter_catalog()),
            LLMClient("openrouter", "paid/x", transport=_openrouter_catalog()),
        ]
    )
    await chain.probe()
    await chain.close()
    assert chain.describe()["status"] == "ready"


async def test_structured_output_is_retried_after_a_404():
    """A 404 (no endpoint with structured outputs) falls back for that call only."""
    bodies = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body and len(bodies) == 1:
            return httpx.Response(404, json={"error": {"message": "No endpoints found"}})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(EXTRACTION)}}]}
        )

    client = LLMClient("openrouter", "a/one:free", transport=httpx.MockTransport(handler))
    await extract_disruption(client, SAMPLES[0]["text"], fallback=False)
    await extract_disruption(client, SAMPLES[0]["text"], fallback=False)
    await client.close()
    assert ["response_format" in b for b in bodies] == [True, False, True]


async def test_openrouter_retries_rate_limit(monkeypatch):
    import app.services.llm.client as client_module

    async def no_sleep(_):
        return None

    monkeypatch.setattr(client_module.asyncio, "sleep", no_sleep)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(EXTRACTION)}}]}
        )

    client = LLMClient("openrouter", api_key="k", transport=httpx.MockTransport(handler))
    result = await extract_disruption(client, SAMPLES[0]["text"])
    await client.close()
    assert calls["n"] == 2 and result.method == "llm"


async def test_fallback_chain_uses_next_provider():
    from app.services.llm.client import FallbackLLMClient

    down = LLMClient(
        "openrouter", api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(503))
    )
    local = LLMClient(
        "ollama",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"message": {"content": json.dumps(EXTRACTION)}})
        ),
    )
    chain = FallbackLLMClient([down, local])
    result = await extract_disruption(chain, SAMPLES[0]["text"])
    await chain.close()
    assert result.method == "llm"
    assert chain.describe()["provider"] == "openrouter,ollama"


def test_make_llm_client_parses_provider_chain(monkeypatch):
    from app.config import settings
    from app.services.llm.client import FallbackLLMClient, make_llm_client

    monkeypatch.setattr(settings, "llm_provider", "openrouter,ollama")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_paid_model", "")  # No paid link in this chain
    chain = make_llm_client()
    assert isinstance(chain, FallbackLLMClient)
    assert chain.clients[0].models[0].endswith(":free")
    assert chain.clients[1].model == "qwen2.5:7b-instruct"
    monkeypatch.setattr(settings, "llm_provider", "none")
    assert make_llm_client() is None


async def test_daily_quota_is_not_retried_and_blocks_until_reset():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            429,
            headers={"x-ratelimit-reset": "4102444800000"},  # 2100-01-01
            json={"error": {"message": "Rate limit exceeded: free-models-per-day"}},
        )

    from app.services.llm import QuotaExhaustedError

    client = LLMClient("openrouter", api_key="k", transport=httpx.MockTransport(handler))
    with pytest.raises(QuotaExhaustedError):
        await extract_disruption(client, SAMPLES[0]["text"], fallback=False)
    assert calls["n"] == 1  # No retries
    # Later calls skip the network entirely and fall back to rules at once
    result = await extract_disruption(client, SAMPLES[0]["text"])
    assert calls["n"] == 1 and result.method == "rules"
    assert "quota_exhausted_until" in client.describe()
    await client.close()


async def test_benchmark_never_scores_fallback_as_the_model():
    import app.services.llm.evaluate as ev
    from app.services.llm.evaluate import evaluate

    ev.MIN_INTERVAL_S = 0
    client_calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        client_calls["n"] += 1
        if client_calls["n"] == 1:
            return httpx.Response(
                200, json={"choices": [{"message": {"content": json.dumps(EXTRACTION)}}]}
            )
        return httpx.Response(429, json={"error": {"message": "free-models-per-day"}})

    orig = ev.LLMClient

    def fake_client(provider, model):
        return orig(provider, model=model, api_key="k", transport=httpx.MockTransport(handler))

    ev.LLMClient = fake_client
    try:
        result = await evaluate(SAMPLES[:3], models=["m:free"])
    finally:
        ev.LLMClient = orig
    model_row = next(r for r in result["systems"] if r["system"] == "m:free")
    assert model_row["reports"] == 1 and model_row["quota_exhausted"] is True
    assert model_row["success_ratio"] == 1.0


def test_rules_read_separable_german_verb():
    text = "Die Buslinie 830 fällt wegen eines Unfalls bis 23:00 Uhr aus."
    assert extract_with_rules(text).effect == "suspended"
    assert extract_with_rules("Linie 810: Ausfall heute.").effect == "other"  # No line-level verb


def test_rules_read_until_with_qualifier():
    text = "Buslinie 830 bis voraussichtlich 18:00 Uhr gesperrt."
    result = extract_with_rules(text)
    assert (result.start_time, result.end_time) == (None, "18:00")
    assert extract_with_rules("Line 11 diverted until approximately 16:30.").end_time == "16:30"


async def test_paid_model_answers_after_free_quota_and_respects_its_cap(monkeypatch):
    from app.services.llm.client import FallbackLLMClient, make_llm_client

    def free(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="Rate limit exceeded: free-models-per-day")

    def paid(request: httpx.Request) -> httpx.Response:
        content = json.dumps(EXTRACTION)
        return httpx.Response(
            200, json={"model": "cheap/model", "choices": [{"message": {"content": content}}]}
        )

    free_client = LLMClient("openrouter", api_key="k", transport=httpx.MockTransport(free))
    paid_client = LLMClient(
        "openrouter", "cheap/model", api_key="k", transport=httpx.MockTransport(paid), daily_limit=1
    )
    chain = FallbackLLMClient([free_client, paid_client])

    first = await extract_disruption(chain, SAMPLES[0]["text"])
    assert first.method == "llm"
    assert chain.describe()["status"] == "ok" and chain.describe()["answered_by"] == "cheap/model"

    second = await extract_disruption(chain, SAMPLES[0]["text"])  # Paid cap of 1 reached
    assert second.method == "rules"
    assert chain.describe()["status"] == "error"
    assert chain.clients[1].describe()["calls_today"] == "1 of 1"
    await chain.close()

    monkeypatch.setattr(settings, "llm_provider", "openrouter")
    monkeypatch.setattr(settings, "llm_model", "")
    monkeypatch.setattr(settings, "llm_paid_model", "cheap/model")
    built = make_llm_client()
    assert isinstance(built, FallbackLLMClient)
    assert (
        built.clients[1].model == "cheap/model"
        and built.clients[1].daily_limit == settings.llm_paid_daily_limit
    )
    await built.close()
