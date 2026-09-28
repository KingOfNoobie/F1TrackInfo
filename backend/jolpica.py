from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from backend.cache import cache
from backend.config import settings
from backend.utils import parse_iso, utc_now


async def _get_json(client: httpx.AsyncClient, url: str) -> dict[str, Any]:
    response = await client.get(url)
    response.raise_for_status()
    return response.json()


def _race_datetime(race: dict[str, Any]) -> datetime | None:
    date = race.get("date")
    time_str = race.get("time") or "00:00:00Z"
    if not date:
        return None
    return parse_iso(f"{date}T{time_str}" if "T" not in date else date)


def _pick_next_or_current(races: list[dict[str, Any]]) -> dict[str, Any] | None:
    now = utc_now()
    upcoming: list[tuple[datetime, dict[str, Any]]] = []
    past: list[tuple[datetime, dict[str, Any]]] = []

    for race in races:
        dt = _race_datetime(race)
        if dt is None:
            continue
        # Keep showing a weekend until ~6h after race start
        if dt >= now.replace(tzinfo=timezone.utc) or (now - dt).total_seconds() < 6 * 3600:
            upcoming.append((dt, race))
        else:
            past.append((dt, race))

    if upcoming:
        upcoming.sort(key=lambda item: item[0])
        return upcoming[0][1]
    if past:
        past.sort(key=lambda item: item[0])
        return past[-1][1]
    return races[0] if races else None


def _session_list(race: dict[str, Any]) -> list[dict[str, Any]]:
    sessions: list[dict[str, Any]] = []
    mapping = [
        ("FirstPractice", "FP1"),
        ("SecondPractice", "FP2"),
        ("ThirdPractice", "FP3"),
        ("SprintQualifying", "Sprint Qualifying"),
        ("Sprint", "Sprint"),
        ("Qualifying", "Qualifying"),
    ]
    for key, label in mapping:
        block = race.get(key)
        if isinstance(block, dict) and block.get("date"):
            sessions.append(
                {
                    "name": label,
                    "date": block.get("date"),
                    "time": block.get("time"),
                    "datetime": f"{block.get('date')}T{block.get('time') or '00:00:00Z'}",
                }
            )
    sessions.append(
        {
            "name": "Race",
            "date": race.get("date"),
            "time": race.get("time"),
            "datetime": f"{race.get('date')}T{race.get('time') or '00:00:00Z'}",
        }
    )
    return sessions


def _active_session(sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    now = utc_now()
    dated: list[tuple[datetime, dict[str, Any]]] = []
    for session in sessions:
        dt = parse_iso(session.get("datetime"))
        if dt:
            dated.append((dt, {**session}))
    if not dated:
        return None
    dated.sort(key=lambda item: item[0])

    for dt, session in dated:
        if dt <= now <= dt + timedelta(hours=3):
            session["phase"] = "live"
            return session
    for dt, session in dated:
        if dt > now:
            session["phase"] = "next"
            return session
    last = dated[-1][1]
    last["phase"] = "completed"
    return last


def _display_race_name(race_name: str | None, circuit_name: str | None, country: str | None) -> str:
    raw = (race_name or "").strip()
    if not raw:
        if country:
            return f"{country} Grand Prix"
        return circuit_name or "Grand Prix"
    # Jolpica sometimes returns e.g. "Bahrain Grand Prix in Malaysia"
    if " in " in raw:
        base = raw.split(" in ", 1)[0].strip()
        if circuit_name:
            return f"{base} ({circuit_name})"
        return base
    return raw


async def fetch_season_context(client: httpx.AsyncClient) -> dict[str, Any]:
    cached = cache.get("season_context")
    if cached is not None:
        return cached

    year = utc_now().year
    data = await _get_json(client, f"{settings.jolpica_base}/{year}/races.json")
    races = (
        data.get("MRData", {})
        .get("RaceTable", {})
        .get("Races", [])
    )
    if not races:
        # Fallback to previous season mid-winter
        data = await _get_json(client, f"{settings.jolpica_base}/{year - 1}/races.json")
        races = (
            data.get("MRData", {})
            .get("RaceTable", {})
            .get("Races", [])
        )
        year = year - 1

    race = _pick_next_or_current(races)
    if race is None:
        payload = {"available": False, "reason": "No races found"}
        return cache.set("season_context", payload, settings.session_cache_seconds)

    circuit = race.get("Circuit", {})
    location = circuit.get("Location", {})
    sessions = _session_list(race)
    active = _active_session(sessions)

    try:
        lat = float(location.get("lat")) if location.get("lat") is not None else None
        lon = float(location.get("long")) if location.get("long") is not None else None
    except (TypeError, ValueError):
        lat, lon = None, None

    payload = {
        "available": True,
        "season": str(race.get("season") or year),
        "round": race.get("round"),
        "race_name": _display_race_name(
            race.get("raceName"),
            circuit.get("circuitName"),
            location.get("country"),
        ),
        "race_name_raw": race.get("raceName"),
        "race_date": race.get("date"),
        "race_time": race.get("time"),
        "circuit": {
            "circuit_id": circuit.get("circuitId"),
            "name": circuit.get("circuitName"),
            "locality": location.get("locality"),
            "country": location.get("country"),
            "lat": lat,
            "lon": lon,
            "url": circuit.get("url"),
        },
        "sessions": sessions,
        "active_session": active,
        "url": race.get("url"),
    }
    return cache.set("season_context", payload, settings.session_cache_seconds)


# Approximate official lengths / lap counts for current calendar circuits.
# Jolpica does not always expose these; values are used when id matches.
TRACK_STATS: dict[str, dict[str, Any]] = {
    "albert_park": {"length_km": 5.278, "laps": 58, "first_gp": 1996, "type": "Street"},
    "shanghai": {"length_km": 5.451, "laps": 56, "first_gp": 2004, "type": "Permanent"},
    "suzuka": {"length_km": 5.807, "laps": 53, "first_gp": 1987, "type": "Permanent"},
    "bahrain": {"length_km": 5.412, "laps": 57, "first_gp": 2004, "type": "Permanent"},
    "jeddah": {"length_km": 6.174, "laps": 50, "first_gp": 2021, "type": "Street"},
    "miami": {"length_km": 5.412, "laps": 57, "first_gp": 2022, "type": "Street"},
    "imola": {"length_km": 4.909, "laps": 63, "first_gp": 1980, "type": "Permanent"},
    "monaco": {"length_km": 3.337, "laps": 78, "first_gp": 1950, "type": "Street"},
    "catalunya": {"length_km": 4.657, "laps": 66, "first_gp": 1991, "type": "Permanent"},
    "villeneuve": {"length_km": 4.361, "laps": 70, "first_gp": 1978, "type": "Semi-permanent"},
    "red_bull_ring": {"length_km": 4.318, "laps": 71, "first_gp": 1970, "type": "Permanent"},
    "silverstone": {"length_km": 5.891, "laps": 52, "first_gp": 1950, "type": "Permanent"},
    "hungaroring": {"length_km": 4.381, "laps": 70, "first_gp": 1986, "type": "Permanent"},
    "spa": {"length_km": 7.004, "laps": 44, "first_gp": 1950, "type": "Permanent"},
    "zandvoort": {"length_km": 4.259, "laps": 72, "first_gp": 1952, "type": "Permanent"},
    "monza": {"length_km": 5.793, "laps": 53, "first_gp": 1950, "type": "Permanent"},
    "baku": {"length_km": 6.003, "laps": 51, "first_gp": 2016, "type": "Street"},
    "marina_bay": {"length_km": 4.94, "laps": 62, "first_gp": 2008, "type": "Street"},
    "americas": {"length_km": 5.513, "laps": 56, "first_gp": 2012, "type": "Permanent"},
    "rodriguez": {"length_km": 4.304, "laps": 71, "first_gp": 1963, "type": "Permanent"},
    "interlagos": {"length_km": 4.309, "laps": 71, "first_gp": 1973, "type": "Permanent"},
    "vegas": {"length_km": 6.201, "laps": 50, "first_gp": 2023, "type": "Street"},
    "losail": {"length_km": 5.419, "laps": 57, "first_gp": 2021, "type": "Permanent"},
    "yas_marina": {"length_km": 5.281, "laps": 58, "first_gp": 2009, "type": "Permanent"},
    "sepang": {"length_km": 5.543, "laps": 56, "first_gp": 1999, "type": "Permanent"},
}


def enrich_track(circuit: dict[str, Any]) -> dict[str, Any]:
    circuit_id = (circuit.get("circuit_id") or "").lower()
    stats = TRACK_STATS.get(circuit_id, {})
    length = stats.get("length_km")
    laps = stats.get("laps")
    race_distance = round(length * laps, 3) if length and laps else None
    return {
        **circuit,
        "length_km": length,
        "laps": laps,
        "race_distance_km": race_distance,
        "first_gp": stats.get("first_gp"),
        "type": stats.get("type"),
    }
