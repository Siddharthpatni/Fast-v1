"""Jev as decision-maker: transfer review in the optimisation check, reroute advice."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.services.decisions import JevClient
from app.services.optimisation import CheckContext, check_plan
from app.services.optimisation.jev_review import review_transfers
from app.services.routing.types import Leg, Plan, StopCall
from tests.conftest import MONDAY_0710

TZ = ZoneInfo("Europe/Berlin")
T0 = datetime(2026, 10, 5, 7, 0, tzinfo=TZ)
LEVELS = {"0": "a", "1": "b", "2": "c", "3": "d"}


def _leg(mode, start, end, line, route_id):
    dep, arr = T0 + timedelta(minutes=start), T0 + timedelta(minutes=end)
    stops = [StopCall("A", "A", 0, 0, dep), StopCall("B", "Bad Harzburg", 0, 0, arr)]
    return Leg(
        mode, "A", "B", 0, 0, 0, 0, dep, arr, 1.0, line_name=line, route_id=route_id, stops=stops
    )


def _transfer_plan():
    return check_plan(
        Plan([_leg("bus", 0, 20, "810", "r810"), _leg("bus", 26, 50, "840", "r840")]),
        CheckContext(),
    )


def jev_answering(answers: dict, seen: dict | None = None) -> JevClient:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if seen is not None:
            seen.update(body)
        return httpx.Response(200, json={"model": "jev-1.13.0", "answers": answers(body)})

    return JevClient(api_key="k", transport=httpx.MockTransport(handler))


async def test_jev_review_batches_transfers_and_blends_confident_answers():
    plans = [_transfer_plan(), _transfer_plan()]
    before = plans[0].reliability
    seen: dict = {}
    jev = jev_answering(
        lambda body: {
            qid: {"type": "score", "score": 3.0, "legend": LEVELS, "confidence": 0.9}
            for qid in body["questions"]
        },
        seen,
    )
    adjusted = await review_transfers(jev, plans)
    await jev.close()

    assert adjusted == 2
    assert set(seen["questions"]) == {"p0t0", "p1t0"}  # One call for every transfer
    facts = seen["state"]["transfers"]["p0t0"]
    assert facts["arriving"] == "bus 810" and facts["departing"] == "bus 840"
    assert facts["slack_min"] == 6.0
    assert plans[0].reliability < before  # "Likely to be missed" pulls reliability down
    assert any("Jev rates the transfer" in i.message for i in plans[0].issues)


async def test_jev_review_ignores_unconfident_answers():
    plan = _transfer_plan()
    before = plan.reliability
    jev = jev_answering(
        lambda body: {
            qid: {"type": "score", "score": 3.0, "legend": LEVELS, "confidence": 0.2}
            for qid in body["questions"]
        }
    )
    assert await review_transfers(jev, [plan]) == 0
    await jev.close()
    assert plan.reliability == before


async def test_jev_review_survives_jev_outage():
    plan = _transfer_plan()
    jev = JevClient(
        api_key="k", max_retries=0, transport=httpx.MockTransport(lambda r: httpx.Response(500))
    )
    assert await review_transfers(jev, [plan]) == 0
    await jev.close()


@pytest.mark.parametrize(("confidence", "applied"), [(0.9, True), (0.3, False)])
async def test_replan_acts_on_confident_jev_advice(monkeypatch, client, confidence, applied):
    import app.api as api_module

    payload = {
        "origin_lat": 51.905,
        "origin_lon": 10.427,
        "destination_lat": 51.804,
        "destination_lon": 10.339,
        "departure_time": MONDAY_0710,
    }
    routes = (await client.post("/api/routes/plan", json=payload)).json()["routes"]
    trip = next(r for r in routes if any(leg["line_name"] == "830" for leg in r["legs"]))
    await client.post(
        "/api/disruptions",
        json={
            "raw_text": "Buslinie 830 seit 07:00 Uhr gesperrt, bis 10:00 Uhr.",
            "source": "t",
            "reported_at": "2026-10-05T07:05:00",
        },
    )

    def answers(body):
        out = {}
        for qid, q in body["questions"].items():
            if qid == "strategy":
                out[qid] = {
                    "type": "choice",
                    "choice": "switch_mode",
                    "confidence": confidence,
                    "probabilities": {},
                }
            elif qid == "preferred_alternative_mode":
                out[qid] = {
                    "type": "choice",
                    "choice": "on_demand",
                    "confidence": confidence,
                    "probabilities": {},
                }
            elif q["type"] == "score":
                out[qid] = {
                    "type": "score",
                    "score": 1.0,
                    "legend": {"0": "a", "1": "b", "2": "c"},
                    "confidence": 0.9,
                }
        return out

    jev = jev_answering(answers)
    monkeypatch.setattr(api_module, "get_jev_client", lambda: jev)
    result = (
        await client.post("/api/routes/replan", json={"route": trip, "at_time": MONDAY_0710})
    ).json()
    await jev.close()

    assert result["decision"]["strategy"] == "switch_mode"
    assert result["decision"]["applied"] is applied
    if applied:
        assert "on_demand" in {leg["mode"] for leg in result["routes"][0]["legs"]}
