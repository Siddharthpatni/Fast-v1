"""Disruption report extraction: free text → structured, network-matched facts.

"Detection: the report is read from unstructured text" (brief). The LLM pulls
out affected lines, stops, the time window and type; the result is then matched
against the loaded network so routing can act on it. It also proposes the lines
the report recommends instead, which the optimisation check prefers. A rule-based extractor
covers the same fields when no LLM is configured or the call fails, and serves
as the baseline in the evaluation script.
"""

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.services.llm.client import FallbackLLMClient, LLMClient, LLMError

logger = logging.getLogger(__name__)

Effect = Literal[
    "suspended", "stops_not_served", "station_closed", "delayed", "reduced_availability", "other"
]
DisruptionType = Literal[
    "accident", "technical", "construction", "event", "weather", "strike", "overcrowding", "other"
]


class DisruptionExtraction(BaseModel):
    summary: str = Field(description="One English sentence: what is affected and how")
    affected_lines: list[str] = Field(
        description="Line names exactly as written, e.g. '830', 'S1', 'RE 10'"
    )
    affected_stops: list[str] = Field(
        description="Stop or station names that are closed, skipped or blocked"
    )
    start_time: str | None = Field(description="HH:MM (24h) when it starts, if stated")
    end_time: str | None = Field(description="HH:MM (24h) when it is expected to end, if stated")
    duration_unknown: bool = Field(
        description="True if the report says the end is unknown or gives none"
    )
    disruption_type: DisruptionType
    effect: Effect = Field(description="What happens to service; decides what routing blocks")
    delay_minutes: int | None = Field(description="Typical delay in minutes, for effect 'delayed'")
    alternatives: list[str] = Field(description="Alternatives the report itself recommends")
    # The language model's candidate proposal: the optimisation check prefers chains on these
    alternative_lines: list[str] = Field(
        default_factory=list, description="Lines the report recommends using instead"
    )
    method: str = "llm"  # "llm" or "rules" — not part of the LLM schema

    @field_validator("start_time", "end_time", mode="before")
    @classmethod
    def _normalize_time(cls, value: object) -> str | None:
        """Accept 8:15 / 08:15 / 08.15; anything unparseable becomes null rather than an error."""
        if not isinstance(value, str):
            return None
        match = re.fullmatch(r"\s*([01]?\d|2[0-3])[:.]([0-5]\d)\s*", value)
        return f"{int(match.group(1)):02d}:{match.group(2)}" if match else None


SYSTEM_PROMPT = """You extract facts from public transport disruption reports (German or English).
Return only what the report states. Never invent lines, stops or times.
- affected_lines: every line that is closed, delayed, diverted or cancelled, as written in the
  report ("Buslinie 830" -> "830", "S-Bahn S1" -> "S1", "RE1" -> "RE1").
- affected_stops: stops or stations that are not served, closed, or where the line is blocked.
  Do not list replacement stops or alternative routes here.
- start_time / end_time: 24h HH:MM, or null if not stated. "bis ca. 10:00" -> end_time "10:00".
- duration_unknown: true when the report says the end is unknown ("Dauer unbekannt") or gives none.
- effect: "suspended" if the lines do not run (gesperrt, eingestellt, entfällt, fällt aus);
  "stops_not_served" if lines run but skip some stops (umgeleitet, nicht bedient);
  "station_closed" if a station or stop is closed for all lines; "delayed" if service runs late;
  "reduced_availability" for shared vehicles or on-demand capacity; otherwise "other".
- delay_minutes: the stated delay for "delayed" (upper bound of a range), else null.
- disruption_type: the cause the report states (Unfall -> accident, Stellwerk/Signal/Störung ->
  technical, Bauarbeiten -> construction, Veranstaltung/Demo -> event, Unwetter/Schnee ->
  weather, Streik -> strike, Fahrgastaufkommen -> overcrowding). Use "other" if no cause is given.
- summary: one short English sentence.
- alternatives: replacement services or detours the report recommends, in English.
- alternative_lines: lines the report tells travellers to use instead, as written ("nutzen Sie
  die Linie 831" -> "831", "Ersatzverkehr mit Bus SEV 1" -> "SEV 1"). Never an affected line;
  [] if the report names none."""


def _llm_schema() -> dict[str, object]:
    """JSON schema for constrained decoding (OpenAI strict mode: all fields required)."""
    # No "pattern": Ollama's grammar converter can't handle it (\d becomes a literal "d").
    # Times are normalized by DisruptionExtraction's validator instead.
    nullable_time = {"type": ["string", "null"]}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "summary",
            "affected_lines",
            "affected_stops",
            "start_time",
            "end_time",
            "duration_unknown",
            "disruption_type",
            "effect",
            "delay_minutes",
            "alternatives",
            "alternative_lines",
        ],
        "properties": {
            "summary": {"type": "string"},
            "affected_lines": {"type": "array", "items": {"type": "string"}},
            "affected_stops": {"type": "array", "items": {"type": "string"}},
            "start_time": nullable_time,
            "end_time": nullable_time,
            "duration_unknown": {"type": "boolean"},
            "disruption_type": {"type": "string", "enum": list(DisruptionType.__args__)},  # type: ignore[attr-defined]
            "effect": {"type": "string", "enum": list(Effect.__args__)},  # type: ignore[attr-defined]
            "delay_minutes": {"type": ["integer", "null"]},
            "alternatives": {"type": "array", "items": {"type": "string"}},
            "alternative_lines": {"type": "array", "items": {"type": "string"}},
        },
    }


# ── Rule-based extraction (fallback and evaluation baseline) ──

_LINE_PREFIX = r"(?:ICE|IC|EC|RE|RB|S|U|STR|Tram|N)"
_RAIL_LINE = re.compile(rf"\b({_LINE_PREFIX})\s?(\d{{1,3}})\b")
_BUS_LIST = re.compile(
    r"(?i)\b(?:bus\s*)?(?:linie[n]?|lines?|buslinie[n]?)\s+((?:\d{1,4}[a-z]?(?:\s*,?\s*(?:und|and|&)?\s*))+)"
)
# "08:15" or "8.15 Uhr" — a dot only counts with "Uhr", so dates like "05.10." aren't times
_TIME = re.compile(r"\b([01]?\d|2[0-3])(?::([0-5]\d)\b|\.([0-5]\d)\s*Uhr\b)")
# "bis 10:00", "bis ca. 10:00", "bis voraussichtlich 18:00", "until approximately 16:00"
_END_CUE = re.compile(r"(?i)\b(bis|until|till|by)\b[^\d]{0,28}$")
_TYPE_KEYWORDS: list[tuple[DisruptionType, tuple[str, ...]]] = [
    ("accident", ("unfall", "accident", "kollision", "collision")),
    (
        "technical",
        ("stellwerk", "störung", "stromausfall", "signal", "technical", "defekt", "power"),
    ),
    ("construction", ("bauarbeiten", "baustelle", "construction", "works")),
    ("event", ("veranstaltung", "event", "fest", "markt", "demonstration", "konzert")),
    ("weather", ("sturm", "schnee", "glätte", "hochwasser", "storm", "snow", "flood", "ice")),
    ("strike", ("streik", "strike")),
    ("overcrowding", ("fahrgastaufkommen", "überfüllt", "crowd", "overcrowd")),
]


def extract_with_rules(text: str, known_stops: list[str] | None = None) -> DisruptionExtraction:
    lines: list[str] = []
    for prefix, number in _RAIL_LINE.findall(text):
        lines.append(f"{prefix.upper() if prefix.lower() != 'tram' else 'Tram '}{number}")
    for group in _BUS_LIST.findall(text):
        lines.extend(re.findall(r"\d{1,4}[a-z]?", group))
    lines = list(dict.fromkeys(lines))

    stops: list[str] = []
    lowered = text.lower()
    for name in known_stops or []:
        # Full stop names from the network that appear verbatim in the report
        if name.lower() in lowered and name not in stops:
            stops.append(name)
    stops += [
        q
        for q in re.findall(r"['\"„“]([^'\"„“”]{3,40})['\"“”]", text)
        if q not in stops and not re.search(r"(?i)replacement|ersatz", text.split(q)[0][-40:])
    ]

    found = [
        (f"{int(m.group(1)):02d}:{m.group(2) or m.group(3)}", m.start())
        for m in _TIME.finditer(text)
    ]
    times = [t for t, _ in found]
    start = times[0] if times else None
    end = times[1] if len(times) > 1 else None
    if len(found) == 1 and _END_CUE.search(text[max(0, found[0][1] - 30) : found[0][1]]):
        start, end = None, times[0]  # "bis ca. 10:00", "back online by 16:00"

    dtype: DisruptionType = "other"
    for candidate, keywords in _TYPE_KEYWORDS:
        if any(k in lowered for k in keywords):
            dtype = candidate
            break

    effect: Effect = "other"
    delay = None
    if re.search(r"(?i)scooter|e-bike|fahrrad|bike|sharing|fleet|flotte", text) and not lines:
        effect = "reduced_availability"
    elif re.search(r"(?i)verzögerung|verspätung|delay", text):
        effect = "delayed"
        minutes = re.findall(
            r"(\d{1,3})\s*(?:-\s*(\d{1,3}))?\s*(?:min|minuten|minutes)", text, re.I
        )
        if minutes:
            low, high = minutes[0]
            delay = int(high or low)
    elif re.search(r"(?i)nicht bedient|not be served|umgeleitet|rerouted|diverted", text):
        effect = "stops_not_served"
    elif re.search(
        # German splits "ausfallen": "fällt wegen eines Unfalls bis 23:00 Uhr aus"
        r"(?i)gesperrt|eingestellt|entfällt|entfallen|\bf[äa]ll(t|en)\b[^.]*?\baus\b"
        r"|suspended|cancel",
        text,
    ):
        effect = "suspended" if lines else "station_closed"

    return DisruptionExtraction(
        summary=text if len(text) <= 160 else text[:157] + "...",
        affected_lines=lines,
        affected_stops=stops,
        start_time=start,
        end_time=end,
        duration_unknown=end is None,
        disruption_type=dtype,
        effect=effect,
        delay_minutes=delay,
        alternatives=[],
        method="rules",
    )


async def extract_disruption(
    client: LLMClient | FallbackLLMClient | None,
    text: str,
    known_stops: list[str] | None = None,
    fallback: bool = True,
) -> DisruptionExtraction:
    """Extract with the LLM; fall back to rules if no LLM is configured or the call fails.

    With fallback=False the LLM's error is raised instead (used by the benchmark, so a
    failed call is never scored as if the model had answered).
    """
    if client is not None:
        try:
            data = await client.extract_json(SYSTEM_PROMPT, f"Report:\n{text}", _llm_schema())
            data.pop("method", None)
            return DisruptionExtraction(**data, method="llm")
        except (LLMError, ValueError, TypeError) as exc:
            if not fallback:
                raise
            logger.warning("LLM extraction failed, using rules: %s", exc)
    return extract_with_rules(text, known_stops)
