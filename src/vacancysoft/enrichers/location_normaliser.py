from __future__ import annotations


def normalise_location(location_raw: str | None) -> dict:
    if not location_raw:
        return {
            "raw": None,
            "city": None,
            "region": None,
            "country": None,
            "confidence": 0.0,
        }

    parts = [part.strip() for part in location_raw.split(",") if part.strip()]
    city = parts[0] if parts else None
    country = parts[-1] if len(parts) >= 2 else None
    region = parts[1] if len(parts) >= 3 else None

    return {
        "raw": location_raw,
        "city": city,
        "region": region,
        "country": country,
        "confidence": 0.85 if city or country else 0.3,
    }
