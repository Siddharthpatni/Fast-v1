"""Tests for place search: Transitous first, Photon as the fallback."""

import httpx

from app.services.geocoding import Geocoder, Place, dedupe

TRANSITOUS = [
    {
        "type": "STOP",
        "name": "Berlin Hbf",
        "lat": 52.5251,
        "lon": 13.3694,
        "zip": "10557",
        "areas": [{"name": "Deutschland", "default": False}, {"name": "Berlin", "default": True}],
    },
    # The same address twice (two entrances), as Transitous returns it
    {
        "type": "ADDRESS",
        "name": "Europaplatz 1",
        "lat": 52.5250,
        "lon": 13.3690,
        "zip": "10557",
        "areas": [],
    },
    {
        "type": "ADDRESS",
        "name": "Europaplatz 1",
        "lat": 52.5252,
        "lon": 13.3692,
        "zip": "10557",
        "areas": [],
    },
]
PHOTON = {
    "features": [
        {
            "geometry": {"coordinates": [13.3694, 52.5251]},
            "properties": {"name": "Berlin Hauptbahnhof", "type": "house"},
        }
    ]
}


def _geocoder(handler) -> Geocoder:
    return Geocoder(
        "https://photon.test/api/",
        transport=httpx.MockTransport(handler),
        transitous_url="https://transitous.test",
    )


def _both(transitous, photon):
    def handler(request: httpx.Request) -> httpx.Response:
        return transitous if request.url.host == "transitous.test" else photon

    return handler


async def test_transitous_places_with_town_and_no_duplicates():
    geocoder = _geocoder(
        _both(httpx.Response(200, json=TRANSITOUS), httpx.Response(200, json={"features": []}))
    )
    places = await geocoder.search("Berlin Hbf")
    await geocoder.close()
    assert [(p.name, p.kind, p.label) for p in places] == [
        ("Berlin Hbf", "stop", "Stop, Berlin"),
        ("Europaplatz 1", "house", "10557"),
    ]


async def test_photon_takes_over_when_transitous_fails():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "transitous.test":
            return httpx.Response(502)
        return httpx.Response(200, json=PHOTON)

    geocoder = _geocoder(handler)
    places = await geocoder.search("Berlin Hauptbahnhof")
    await geocoder.close()
    assert [p.name for p in places] == ["Berlin Hauptbahnhof"]


def test_dedupe_keeps_the_first_of_one_place():
    stop = Place("Goslar, Bahnhof", "Stop", 51.9109, 10.4228, "stop")
    same = Place("Goslar, Bahnhof", "Stop, Goslar", 51.9117, 10.4207, "stop")
    other = Place("Goslar, Bahnhof", "Stop", 51.80, 10.33, "stop")  # Same name, other town
    assert dedupe([stop, same, other]) == [stop, other]


async def test_the_town_typed_decides_the_order_and_germany_only():
    elsewhere = [
        {
            "type": "ADDRESS",
            "name": "Hauptstraße 5",
            "lat": 51.12,
            "lon": 11.66,
            "zip": "06628",
            "country": "DE",
            "areas": [{"name": "Naumburg (Saale)", "default": True}],
        },
        {
            "type": "ADDRESS",
            "name": "Hauptstraße 5",
            "lat": 47.72,
            "lon": 16.21,
            "zip": "2822",
            "country": "AT",
            "areas": [{"name": "Bad Erlach", "default": True}],
        },
    ]
    photon = {
        "features": [
            {
                "geometry": {"coordinates": [10.48, 51.91]},
                "properties": {
                    "street": "Hauptstraße",
                    "postcode": "38667",
                    "city": "Bad Harzburg",
                    "type": "street",
                },
            }
        ]
    }
    geocoder = _geocoder(
        _both(httpx.Response(200, json=elsewhere), httpx.Response(200, json=photon))
    )
    places = await geocoder.search("Hauptstraße 5 Bad Harzburg")
    await geocoder.close()
    assert places[0].label.endswith("Bad Harzburg")
    assert all("Erlach" not in p.label for p in places)  # Austria is out


def test_rank_knows_abbreviations_and_whole_words():
    from app.services.geocoding import rank

    station = Place("Berlin Hauptbahnhof", "Stop, Berlin", 52.52, 13.37, "stop")
    hagen = Place("Hagen Hbf", "Berliner Platz 1, 58089 Hagen", 51.36, 7.46, "place")
    snack = Place("Grillstand HBF", "Tunnel Tiergarten, 10557 Berlin", 52.52, 13.37, "place")
    assert rank("Berlin Hbf", [hagen, snack, station])[0] is station
    street = Place("Marktstraße 1", "38640 Goslar", 51.9, 10.43, "house")
    other = Place("Marktplatz", "38640 Goslar", 51.9, 10.43, "place")
    assert rank("Marktstr 1 Goslar", [other, street])[0] is street
