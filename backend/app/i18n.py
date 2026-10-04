"""Messages for travellers in English or German (the request's `lang`)."""

LANGS = ("en", "de")

# Shared vehicles in running text
VEHICLE = {
    "en": {"bike": "bike", "scooter": "scooter", "car_share": "shared car"},
    "de": {"bike": "Leihrad", "scooter": "E-Scooter", "car_share": "Carsharing-Auto"},
}


def tr(lang: str, en: str, de: str) -> str:
    """Pick the message for `lang`; English unless German was asked for."""
    return de if lang == "de" else en


def vehicle(lang: str, mode: str) -> str:
    return VEHICLE["de" if lang == "de" else "en"].get(mode, mode.replace("_", " "))
