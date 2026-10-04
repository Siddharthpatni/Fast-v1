"""Address search: transit stops from the loaded network, then places across Germany.

Places come from the Transitous geocoder (MOTIS: every German stop plus OpenStreetMap
addresses and places, ~0.2 s) and Photon (komoot's OpenStreetMap geocoder, whose policy
allows search-as-you-type), asked in parallel and ranked by how many typed words each
result matches, so the town someone typed counts. Results are biased toward the region's
centre, kept within Germany, cached, and requests carry an identifying User-Agent. If
both are unreachable, stop matches still come back.
"""

import asyncio
import logging
import re
from collections import OrderedDict
from dataclasses import asdict, dataclass

import httpx

from app.config import settings
from app.services.routing.network import TransitNetwork
from app.utils.geo import haversine_distance

logger = logging.getLogger(__name__)

CACHE_SIZE = 2000
GERMANY_BBOX = "5.8,47.2,15.1,55.1"  # minLon,minLat,maxLon,maxLat
MAX_STOPS = 3


@dataclass
class Place:
    name: str
    label: str  # Second line: street, postcode, city
    lat: float
    lon: float
    kind: str  # "stop" or the OSM type ("street", "house", "city", …)

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def search_stops(net: TransitNetwork | None, query: str, limit: int = MAX_STOPS) -> list[Place]:
    """Stops whose name contains every word of the query, busiest first."""
    if net is None:
        return []
    words = query.lower().split()
    hits = [i for i, name in enumerate(net.stop_names) if all(w in name.lower() for w in words)]
    hits.sort(key=lambda i: -int(net.stop_departures[i]))
    seen: set[str] = set()
    places = []
    for i in hits:
        name = net.stop_names[i]
        if name in seen:  # Platforms of one station share a name
            continue
        seen.add(name)
        lat, lon = net.stop_coords(i)
        places.append(Place(name, "Stop", lat, lon, "stop"))
        if len(places) == limit:
            break
    return places


class Geocoder:
    def __init__(
        self,
        url: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        transitous_url: str | None = None,
    ) -> None:
        self.url = url or settings.geocoder_url
        base = settings.transitous_url if transitous_url is None else transitous_url
        self.transitous_url = f"{base.rstrip('/')}/api/v1/geocode" if base else ""
        self._client = httpx.AsyncClient(
            timeout=8.0,  # Photon can take 6-7 s when busy
            transport=transport,
            headers={"User-Agent": "FastMile/0.1 (TU Clausthal student research project)"},
        )
        self._cache: OrderedDict[tuple[str, int], list[Place]] = OrderedDict()

    async def search(self, query: str, limit: int = 6) -> list[Place]:
        key = (query.strip().lower(), limit)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        # Both in parallel: Transitous knows stops and house numbers, Photon reads town
        # names better ("Hauptstraße 5 Bad Harzburg"). The best word match ranks first.
        lookups = [self._photon(query, limit, PHOTON_TIMEOUT if self.transitous_url else None)]
        if self.transitous_url:
            lookups.insert(0, self._transitous(query, limit))
        answers = await asyncio.gather(*lookups)
        found = [places for places in answers if places is not None]
        if not found:
            return []
        places = rank(query, dedupe([p for places in found for p in places]))[:limit]
        if len(found) == len(answers):  # Cache only complete answers; retry a failed source
            self._cache[key] = places
        if len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)
        return places

    async def _transitous(self, query: str, limit: int) -> list[Place] | None:
        """Places from the Transitous geocoder; None if it failed."""
        lat, lon = settings.geocoder_bias_lat, settings.geocoder_bias_lon
        try:
            response = await self._client.get(
                self.transitous_url,
                params={"text": query, "language": "de", "place": f"{lat},{lon}"},
            )
            response.raise_for_status()
            matches = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Transitous geocoder unavailable: %r", exc)
            return None
        places: list[Place] = []
        for match in matches:
            if match.get("country", "DE") != "DE":
                continue  # FastMile plans within Germany
            place = _transitous_place(match)
            if place and not any(_same(place, p) for p in places):
                places.append(place)
            if len(places) == limit:
                break
        return places

    async def _photon(
        self, query: str, limit: int, timeout: float | None = None
    ) -> list[Place] | None:
        """Places from Photon; None if it failed."""
        lat, lon = settings.geocoder_bias_lat, settings.geocoder_bias_lon
        try:
            response = await self._client.get(
                self.url,
                params={
                    "q": query,
                    "lat": lat,
                    "lon": lon,
                    "location_bias_scale": settings.geocoder_bias_scale,
                    "bbox": GERMANY_BBOX,
                    "limit": limit,
                    "lang": "de",
                },
                **({"timeout": timeout} if timeout else {}),
            )
            response.raise_for_status()
            features = response.json().get("features", [])
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Geocoder unavailable: %r", exc)
            return None
        return [_place(f) for f in features if f.get("geometry", {}).get("coordinates")]

    async def close(self) -> None:
        await self._client.aclose()


SAME_PLACE_KM = 0.3
PHOTON_TIMEOUT = 3.0  # With Transitous answering too, don't wait long for a busy Photon


# German abbreviations people type, mapped to one form: "Hbf" = "Hauptbahnhof"
SHORT = (
    ("hauptbahnhof", "hbf"),
    ("bahnhof", "bf"),
    ("bhf", "bf"),
    ("strasse", "str"),
    ("straße", "str"),
)


def _words(text: str) -> list[str]:
    words = []
    for word in re.findall(r"\w+", text.lower()):
        for long, short in SHORT:
            if word.endswith(long):  # "Marktstraße" -> "marktstr", "Hauptbahnhof" -> "hbf"
                word = word[: -len(long)] + short
                break
        words.append(word)
    return words


def rank(query: str, places: list[Place]) -> list[Place]:
    """Most typed words found in name and town first (stable: source order breaks ties).

    Words match whole ("Berlin" is not "Berliner Platz"), except the last one, which may
    still be half typed. House numbers count half, so the town typed decides. A stop wins
    a tie, since people mostly search for stations.
    """
    typed = _words(query)
    if not typed:
        return places

    def weight(word: str) -> float:
        return 0.5 if word.isdigit() else 1.0  # The town typed matters more than a house number

    def score(place: Place) -> float:
        words = set(_words(f"{place.name} {place.label}"))
        hits = sum(weight(w) for w in typed[:-1] if w in words)
        last = typed[-1]
        if last in words or (len(last) >= 3 and any(w.startswith(last) for w in words)):
            hits += weight(last)
        return hits + (0.25 if place.kind == "stop" else 0)

    return sorted(places, key=lambda p: -score(p))


# Transitous match type → Place.kind ("stop" sorts and renders as a stop in the app)
TRANSITOUS_KINDS = {"STOP": "stop", "ADDRESS": "house", "PLACE": "place"}


def _same(a: Place, b: Place) -> bool:
    """One place listed twice (a station's platforms, an address's entrances)."""
    return a.name == b.name and haversine_distance(a.lat, a.lon, b.lat, b.lon) <= SAME_PLACE_KM


def dedupe(places: list[Place]) -> list[Place]:
    kept: list[Place] = []
    for place in places:
        if not any(_same(place, k) for k in kept):
            kept.append(place)
    return kept


def _transitous_place(match: dict[str, object]) -> Place | None:
    try:
        lat, lon = float(match["lat"]), float(match["lon"])  # type: ignore[arg-type]
    except (KeyError, TypeError, ValueError):
        return None
    areas: list[dict[str, object]] = match.get("areas") or []  # type: ignore[assignment]
    town = next((str(a["name"]) for a in areas if a.get("default")), "")
    place = " ".join(str(p) for p in (match.get("zip"), town) if p)
    kind = TRANSITOUS_KINDS.get(str(match.get("type")), "place")
    label = "Stop" + (f", {town}" if town else "") if kind == "stop" else place
    return Place(str(match.get("name") or "Unnamed place"), label, lat, lon, kind)


def _place(feature: dict[str, object]) -> Place:
    props: dict[str, object] = feature.get("properties", {})  # type: ignore[assignment]
    lon, lat = feature["geometry"]["coordinates"][:2]  # type: ignore[index]
    street = " ".join(str(p) for p in (props.get("street"), props.get("housenumber")) if p)
    place = " ".join(str(p) for p in (props.get("postcode"), props.get("city")) if p)
    name = str(props.get("name") or street or place or "Unnamed place")
    label = ", ".join(p for p in (street if street != name else "", place) if p)
    return Place(name, label, float(lat), float(lon), str(props.get("type", "place")))
