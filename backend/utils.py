from __future__ import annotations

from datetime import datetime, timezone


COMPASS = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSW",
    "SW",
    "WSW",
    "W",
    "WNW",
    "NW",
    "NNW",
]


def degrees_to_compass(degrees: float | None) -> str | None:
    if degrees is None:
        return None
    idx = int((degrees % 360) / 22.5 + 0.5) % 16
    return COMPASS[idx]


def ms_to_kmh(speed_ms: float | None) -> float | None:
    if speed_ms is None:
        return None
    return round(speed_ms * 3.6, 1)


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
