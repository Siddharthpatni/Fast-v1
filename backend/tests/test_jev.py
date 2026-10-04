"""Tests for the Jev (TypeSafe AI) client against the documented API shapes.

See https://docs.typesafe.ai/api — requests send `questions` as a map keyed by
question id; answers come back under the same ids, with the value in a field
named after the answer type (`choice`, `score`, `noul`).
"""

import json

import httpx
import pytest

from app.config import settings
from app.services.decisions import (
    JevClient,
    JevError,
    classify_disruption_severity,
    get_jev_client,
)

SEVERITY_RESPONSE = {
    "model": "jev-1.13.0",
    "answers": {
        "severity": {
            "type": "choice",
            "choice": "high",
            "probabilities": {"low": 0.0, "medium": 0.1, "high": 0.85, "critical": 0.05},
            "confidence": 0.81,
        },
        "needs_reroute": {"type": "noul", "noul": 0.93},
        "expected_duration": {
            "type": "score",
            "score": 1.5,
            "legend": {"0": "a", "1": "b", "2": "c", "3": "d"},
            "probabilities": {"0": 0.0, "1": 0.5, "2": 0.5, "3": 0.0},
            "confidence": 0.6,
        },
    },
    "usage": {"input_tokens": 300, "output_tokens": 30},
}


def make_client(handler) -> JevClient:
    return JevClient(api_key="test-key", transport=httpx.MockTransport(handler))


async def test_request_matches_api_shape():
    """Questions are sent as a map with instructions/criteria and the bearer key."""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers["Authorization"]
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=SEVERITY_RESPONSE)

    client = make_client(handler)
    await classify_disruption_severity(client, "Linie 830 gesperrt")
    await client.close()

    assert captured["url"] == "https://api.typesafe.ai/v1/systemone"
    assert captured["auth"] == "Bearer test-key"
    body = captured["body"]
    assert body["model"] == settings.jev_model
    assert body["state"]["disruption_report"] == "Linie 830 gesperrt"
    questions = body["questions"]
    assert set(questions) == {"severity", "needs_reroute", "expected_duration"}
    assert questions["severity"]["type"] == "choice"
    assert set(questions["severity"]["criteria"]) == {"low", "medium", "high", "critical"}
    assert questions["needs_reroute"]["type"] == "noul"
    assert questions["expected_duration"]["type"] == "score"
    assert isinstance(questions["expected_duration"]["criteria"], list)


async def test_parses_typed_answers():
    client = make_client(lambda request: httpx.Response(200, json=SEVERITY_RESPONSE))
    decision = await classify_disruption_severity(client, "Linie 830 gesperrt")
    await client.close()

    assert decision.model == "jev-1.13.0"
    assert decision.get_choice("severity") == "high"
    assert decision.get_answer("severity").confidence == 0.81
    assert decision.get_noul("needs_reroute") == 0.93
    assert decision.get_bool("needs_reroute") is True
    assert decision.get_score("expected_duration", normalized=False) == 1.5
    assert decision.get_score("expected_duration") == pytest.approx(0.5)
    # Wrong-type lookups return None rather than a misleading value
    assert decision.get_score("needs_reroute") is None
    assert decision.get_choice("missing") is None


async def test_http_error_raises_jev_error():
    client = make_client(lambda request: httpx.Response(401, json={"error": "bad key"}))
    with pytest.raises(JevError, match="401"):
        await classify_disruption_severity(client, "Linie 830 gesperrt")
    await client.close()


async def test_retries_when_overloaded():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(529)
        return httpx.Response(200, json=SEVERITY_RESPONSE)

    client = make_client(handler)
    decision = await classify_disruption_severity(client, "Linie 830 gesperrt")
    await client.close()

    assert calls["n"] == 2
    assert decision.get_choice("severity") == "high"


async def test_malformed_answer_raises_jev_error():
    bad = {"model": "jev-1.13.0", "answers": {"severity": {"type": "choice"}}}
    client = make_client(lambda request: httpx.Response(200, json=bad))
    with pytest.raises(JevError, match="Malformed"):
        await classify_disruption_severity(client, "Linie 830 gesperrt")
    await client.close()


def test_no_shared_client_without_api_key(monkeypatch):
    monkeypatch.setattr(settings, "jev_api_key", "")
    assert get_jev_client() is None


@pytest.mark.parametrize(
    ("jev_handler", "expected_severity", "expected_confidence"),
    [
        (lambda request: httpx.Response(200, json=SEVERITY_RESPONSE), "high", 0.81),
        # Jev down: severity falls back to the extracted effect ("gesperrt" → suspended → high)
        (lambda request: httpx.Response(500), "high", None),
    ],
)
async def test_create_disruption_uses_jev(
    monkeypatch, client, jev_handler, expected_severity, expected_confidence
):
    import app.services.disruptions as disruptions_module

    jev = make_client(jev_handler)
    monkeypatch.setattr(disruptions_module, "get_jev_client", lambda: jev)
    try:
        response = await client.post(
            "/api/disruptions",
            json={"raw_text": "Buslinie 830 gesperrt", "source": "test"},
        )
    finally:
        await jev.close()

    assert response.status_code == 200
    data = response.json()
    assert data["severity"] == expected_severity
    assert data["confidence"] == expected_confidence
    if expected_confidence is not None:
        assert data["needs_reroute"] == 0.93
