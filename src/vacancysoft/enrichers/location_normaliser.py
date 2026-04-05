from __future__ import annotations


def normalise_location(location_raw: str | None) -> dict:
    return {
        "raw": location_raw,
        "city": None,
        "region": None,
        "country": None,
        "confidence": 0.0 if not location_raw else 0.3,
    }
