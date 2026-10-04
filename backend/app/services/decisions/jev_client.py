"""JEV (TypeSafe AI) decision engine for FastMile.

Uses TypeSafe AI's System One model "Jev" for fast, structured decision-making
in real-time routing scenarios. Unlike LLMs that generate text, Jev returns
typed choices + confidence scores in 70-500ms — ideal for decisions that need
to happen within the routing loop.

Usage in FastMile:
  - Disruption severity classification (new reports)
  - Transfer risk assessment (one batched call per plan request)
  - Reroute strategy for a trip broken by a disruption

API reference: https://docs.typesafe.ai/api
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

from app.config import settings


class QuestionType(StrEnum):
    """The three TypeSafe question types."""

    CHOICE = "choice"  # Pick one option; criteria = {option: description}
    SCORE = "score"  # Rate on ordered levels; criteria = [level0, level1, ...]
    NOUL = "noul"  # Yes/no; returns probability of yes; criteria = {"true": ..., "false": ...}


class JevError(Exception):
    """Raised when the Jev API call fails or returns an unusable response."""


@dataclass
class JevQuestion:
    """A single question for the Jev decision engine."""

    id: str
    instructions: str | dict[str, Any]
    type: QuestionType
    criteria: dict[str, str | None] | list[str] | None = None

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"type": self.type.value, "instructions": self.instructions}
        if self.criteria is not None:
            body["criteria"] = self.criteria
        return body


@dataclass
class JevAnswer:
    """A single typed answer from Jev.

    `value` is the chosen option (choice), the probability-weighted level
    (score, between 0 and len(levels) - 1), or the probability of yes (noul).
    """

    question_id: str
    type: QuestionType
    value: str | float
    confidence: float | None = None  # Choice and Score only
    probabilities: dict[str, float] = field(default_factory=dict)
    legend: dict[str, str] = field(default_factory=dict)  # Score only: level index -> label


@dataclass
class JevDecision:
    """Complete decision response from Jev."""

    answers: dict[str, JevAnswer]
    latency_ms: float
    model: str

    def get_answer(self, question_id: str) -> JevAnswer | None:
        """Get a specific answer by question ID."""
        return self.answers.get(question_id)

    def get_choice(self, question_id: str) -> str | None:
        """Get the selected option for a Choice question."""
        answer = self.get_answer(question_id)
        if answer and answer.type == QuestionType.CHOICE:
            return str(answer.value)
        return None

    def get_score(self, question_id: str, normalized: bool = True) -> float | None:
        """Get a Score answer, by default normalized to 0-1 across its levels."""
        answer = self.get_answer(question_id)
        if not answer or answer.type != QuestionType.SCORE:
            return None
        value = float(answer.value)
        if normalized and len(answer.legend) > 1:
            value /= len(answer.legend) - 1
        return value

    def get_noul(self, question_id: str) -> float | None:
        """Get the probability (0-1) that a yes/no question is answered yes."""
        answer = self.get_answer(question_id)
        if answer and answer.type == QuestionType.NOUL:
            return float(answer.value)
        return None

    def get_bool(self, question_id: str, threshold: float = 0.5) -> bool | None:
        """Get a yes/no answer by thresholding its probability."""
        prob = self.get_noul(question_id)
        return None if prob is None else prob >= threshold


def _parse_answer(question_id: str, raw: dict[str, Any]) -> JevAnswer:
    try:
        answer_type = QuestionType(raw["type"])
        value = raw[answer_type.value]  # Answer field is named after its type
    except (KeyError, ValueError) as exc:
        raise JevError(f"Malformed Jev answer for '{question_id}': {raw}") from exc
    return JevAnswer(
        question_id=question_id,
        type=answer_type,
        value=value,
        confidence=raw.get("confidence"),
        probabilities=raw.get("probabilities", {}),
        legend=raw.get("legend", {}),
    )


class JevClient:
    """Client for the TypeSafe AI Jev decision engine.

    Jev is a System One model — it makes fast, structured decisions
    without generating text. It returns typed values + confidence scores.
    """

    BASE_URL = "https://api.typesafe.ai/v1/systemone"
    RETRY_STATUSES = {429, 529}  # Rate limited / overloaded

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 5.0,
        max_retries: int = 2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_key = api_key if api_key is not None else settings.jev_api_key
        self.model = model or settings.jev_model
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
            transport=transport,
        )

    async def decide(
        self,
        state: dict[str, Any] | list[Any] | str,
        questions: list[JevQuestion],
    ) -> JevDecision:
        """Send a state + questions to Jev and get structured decisions.

        Args:
            state: The context for decision-making — a string, or JSON object/array.
            questions: Questions for Jev to answer; all are evaluated in one call.

        Returns:
            JevDecision with typed answers keyed by question ID.

        Raises:
            JevError: on network failure, non-2xx status, or a malformed response.
        """
        payload = {
            "model": self.model,
            "state": state,
            "questions": {q.id: q.to_payload() for q in questions},
        }

        start = time.monotonic()
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.post(self.BASE_URL, json=payload)
            except httpx.HTTPError as exc:
                raise JevError(f"Jev request failed: {exc}") from exc
            if response.status_code not in self.RETRY_STATUSES or attempt == self.max_retries:
                break
            await asyncio.sleep(0.25 * 2**attempt)
        latency_ms = (time.monotonic() - start) * 1000

        if response.is_error:
            raise JevError(f"Jev returned {response.status_code}: {response.text[:300]}")

        data = response.json()
        answers = {qid: _parse_answer(qid, raw) for qid, raw in data.get("answers", {}).items()}

        return JevDecision(
            answers=answers,
            latency_ms=latency_ms,
            model=data.get("model", self.model),
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


# ── Shared client (one connection pool per process) ──

_shared_client: JevClient | None = None


def get_jev_client() -> JevClient | None:
    """Return the shared Jev client, or None when no JEV_API_KEY is configured."""
    global _shared_client
    if not settings.jev_api_key:
        return None
    if _shared_client is None:
        _shared_client = JevClient()
    return _shared_client


async def close_jev_client() -> None:
    """Close the shared Jev client (called on app shutdown)."""
    global _shared_client
    if _shared_client is not None:
        await _shared_client.close()
        _shared_client = None


# ──────────────────────────────────────────────────────────────────
# Pre-built decision functions for FastMile routing scenarios
# ──────────────────────────────────────────────────────────────────


async def classify_disruption_severity(
    client: JevClient,
    disruption_text: str,
    affected_routes: list[str] | None = None,
) -> JevDecision:
    """Classify disruption severity and whether rerouting is needed.

    Uses Jev for fast severity classification (70-500ms) instead of
    a full LLM call, which is reserved for extracting structured data.

    Answers: severity (choice), needs_reroute (noul), expected_duration (score).
    """
    state = {
        "disruption_report": disruption_text,
        "affected_routes": affected_routes or [],
        "context": "public transport disruption in a German/European transit network",
    }

    questions = [
        JevQuestion(
            id="severity",
            instructions="How severe is the `disruption_report` for commuters?",
            type=QuestionType.CHOICE,
            criteria={
                "low": "Minor delay of a few minutes, no action needed",
                "medium": "Significant delay or diversion, but alternatives exist",
                "high": "A line or route is blocked",
                "critical": "Multiple lines or an entire hub/network are affected",
            },
        ),
        JevQuestion(
            id="needs_reroute",
            instructions="Do affected passengers need to be rerouted immediately?",
            type=QuestionType.NOUL,
            criteria={
                "true": "The current route is unusable and passengers need an alternative",
                "false": "Passengers can stay on their current route",
            },
        ),
        JevQuestion(
            id="expected_duration",
            instructions="How long is this disruption likely to last?",
            type=QuestionType.SCORE,
            criteria=[
                "Under 30 minutes",
                "Up to 2 hours",
                "Around half a day",
                "A full day or longer, or unknown",
            ],
        ),
    ]

    return await client.decide(state, questions)


async def assess_transfer_risks(
    client: JevClient, transfers: dict[str, dict[str, Any]]
) -> JevDecision:
    """Score many transfers in one call; Jev evaluates every question against one state.

    `transfers` maps a question id to the transfer's facts; answers come back per id.
    """
    state = {
        "context": (
            "Connections within German regional public transport journeys. slack_min is the "
            "scheduled time between arriving and the next departure; reported_delay_min is a "
            "delay currently reported for the arriving line."
        ),
        "transfers": transfers,
    }
    questions = [
        JevQuestion(
            id=tid,
            instructions=(
                f"How likely is the passenger to miss the connection described in "
                f"`transfers.{tid}`?"
            ),
            type=QuestionType.SCORE,
            criteria=[
                "Very safe: almost always made",
                "Usually made",
                "Risky",
                "Likely to be missed",
            ],
        )
        for tid in transfers
    ]
    return await client.decide(state, questions)


async def decide_reroute_strategy(
    client: JevClient,
    current_route_legs: list[dict[str, Any]],
    disruption: dict[str, Any],
    alternative_modes: list[str],
    passenger_location: dict[str, float],
) -> JevDecision:
    """Decide the rerouting strategy when a disruption hits an active trip.

    This is the critical decision point — when a disruption affects a
    passenger's current route, Jev quickly decides the best strategy
    before the full routing engine recomputes alternatives.

    Answers: strategy (choice), urgency (score), preferred_alternative_mode (choice).
    """
    state = {
        "current_route": current_route_legs,
        "disruption": disruption,
        "alternative_modes_available": alternative_modes,
        "passenger_location": passenger_location,
    }

    questions = [
        JevQuestion(
            id="strategy",
            instructions=(
                "What is the best rerouting strategy? Choose the one that minimizes "
                "delay while keeping the journey feasible."
            ),
            type=QuestionType.CHOICE,
            criteria={
                "wait_it_out": "Disruption is minor, keep the current route",
                "skip_segment": "Skip the disrupted segment, walk or bike around it",
                "full_reroute": "Recompute the entire remaining journey",
                "switch_mode": "Switch to a different mode for this segment",
                "abort_and_return": "Disruption is too severe, return to origin",
            },
        ),
        JevQuestion(
            id="urgency",
            instructions="How urgently does the passenger need to act?",
            type=QuestionType.SCORE,
            criteria=[
                "Can wait and see",
                "Should start looking at alternatives",
                "Must act immediately",
            ],
        ),
        JevQuestion(
            id="preferred_alternative_mode",
            instructions="If switching modes, which alternative mode is best?",
            type=QuestionType.CHOICE,
            criteria={mode: None for mode in (alternative_modes or ["walk"])},
        ),
    ]

    return await client.decide(state, questions)
