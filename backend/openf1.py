from __future__ import annotations

from datetime import timedelta
from typing import Any

import httpx

from backend.cache import cache
from backend.config import settings
from backend.utils import degrees_to_compass, ms_to_kmh, parse_iso, utc_now


SESSION_NAME_MAP = {
    "Practice 1": "FP1",
    "Practice 2": "FP2",
    "Practice 3": "FP3",
    "Qualifying": "Qualifying",
    "Sprint Qualifying": "Sprint Qualifying",
    "Sprint": "Sprint",
    "Race": "Race",
}


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None) -> Any:
    response = await client.get(f"{settings.openf1_base}{path}", params=params or {})
    response.raise_for_status()
    return response.json()


def _freshness(weather: dict[str, Any]) -> str:
    measured = parse_iso(weather.get("date"))
    if measured is None:
        return "STALE"
    age = utc_now() - measured
    if age <= timedelta(minutes=20):
        return "LIVE"
    if age <= timedelta(hours=4):
        return "LAST SESSION"
    return "STALE"


def _norm(value: str | None) -> str:
    return "".join(ch for ch in (value or "").lower() if ch.isalnum())


def _session_matches_circuit(session: dict[str, Any], circuit: dict[str, Any] | None) -> bool:
    if not circuit:
        return True
    candidates = [
        session.get("circuit_short_name"),
        session.get("location"),
        session.get("country_name"),
    ]
    targets = [
        circuit.get("circuit_id"),
        circuit.get("name"),
        circuit.get("locality"),
        circuit.get("country"),
    ]
    cand_norm = {_norm(c) for c in candidates if c}
    targ_norm = {_norm(t) for t in targets if t}
    if not cand_norm or not targ_norm:
        return False
    for c in cand_norm:
        for t in targ_norm:
            if not c or not t:
                continue
            if c == t or c in t or t in c:
                return True
    return False


async def fetch_live_weather(
    client: httpx.AsyncClient,
    target_circuit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cache_key = "live_weather:" + _norm((target_circuit or {}).get("circuit_id") or "any")
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    sessions = await _get(client, "/sessions", {"session_key": "latest"})
    if not isinstance(sessions, list) or not sessions:
        payload = {
            "available": False,
            "status": "FORECAST ONLY",
            "reason": "No OpenF1 session available",
        }
        return cache.set(cache_key, payload, settings.openf1_cache_seconds)

    session = sessions[0]
    session_key = session.get("session_key")
    meeting_key = session.get("meeting_key")
    session_payload = _session_payload(session)

    if not _session_matches_circuit(session, target_circuit):
        payload = {
            "available": False,
            "status": "FORECAST ONLY",
            "reason": "Latest OpenF1 session is a different circuit",
            "session": session_payload,
        }
        return cache.set(cache_key, payload, settings.openf1_cache_seconds)

    weather_rows = await _get(client, "/weather", {"session_key": session_key})
    if not isinstance(weather_rows, list) or not weather_rows:
        weather_rows = (
            await _get(client, "/weather", {"meeting_key": meeting_key}) if meeting_key else []
        )

    if not isinstance(weather_rows, list) or not weather_rows:
        payload = {
            "available": False,
            "status": "FORECAST ONLY",
            "reason": "No OpenF1 weather samples for this circuit",
            "session": session_payload,
        }
        return cache.set(cache_key, payload, settings.openf1_cache_seconds)

    latest = max(weather_rows, key=lambda row: row.get("date") or "")
    wind_ms = latest.get("wind_speed")
    wind_dir = latest.get("wind_direction")
    status = _freshness(latest)

    if status == "STALE":
        payload = {
            "available": False,
            "status": "FORECAST ONLY",
            "reason": "OpenF1 weather is too old for this circuit",
            "session": session_payload,
            "measured_at": latest.get("date"),
        }
        return cache.set(cache_key, payload, settings.openf1_cache_seconds)

    payload = {
        "available": True,
        "status": status,
        "source": "openf1",
        "session": session_payload,
        "measured_at": latest.get("date"),
        "air_temperature": latest.get("air_temperature"),
        "track_temperature": latest.get("track_temperature"),
        "humidity": latest.get("humidity"),
        "pressure": latest.get("pressure"),
        "rainfall": latest.get("rainfall"),
        "wind_speed_ms": wind_ms,
        "wind_speed_kmh": ms_to_kmh(float(wind_ms) if wind_ms is not None else None),
        "wind_direction_deg": wind_dir,
        "wind_direction_compass": degrees_to_compass(
            float(wind_dir) if wind_dir is not None else None
        ),
    }
    return cache.set(cache_key, payload, settings.openf1_cache_seconds)


async def _get_optional(
    client: httpx.AsyncClient, path: str, params: dict[str, Any] | None = None
) -> Any:
    try:
        return await _get(client, path, params)
    except httpx.HTTPError:
        return None


async def fetch_standings(client: httpx.AsyncClient) -> dict[str, Any]:
    """Latest session classification for the ticker (live or last available)."""
    cached = cache.get("standings_ticker")
    if cached is not None:
        return cached

    sessions = await _get_optional(client, "/sessions", {"session_key": "latest"})
    if not isinstance(sessions, list) or not sessions:
        payload = {"available": False, "reason": "No session", "entries": []}
        return cache.set("standings_ticker", payload, 60)

    session = sessions[0]
    session_key = session.get("session_key")
    session_payload = _session_payload(session)
    live = _session_is_live(session)

    drivers_raw = await _get_optional(client, "/drivers", {"session_key": session_key})
    driver_map: dict[Any, dict[str, Any]] = {}
    if isinstance(drivers_raw, list):
        for driver in drivers_raw:
            driver_map[driver.get("driver_number")] = driver

    # session_result is the source of truth for DNF/DNS/DSQ
    result_by_driver: dict[Any, dict[str, Any]] = {}
    results = await _get_optional(client, "/session_result", {"session_key": session_key})
    if isinstance(results, list):
        for row in results:
            driver_number = row.get("driver_number")
            if driver_number is not None:
                result_by_driver[driver_number] = row

    latest_by_driver: dict[Any, dict[str, Any]] = {}
    # Only hit the heavy /position stream while a session is live
    if live:
        positions_raw = await _get_optional(
            client, "/position", {"session_key": session_key}
        )
        if isinstance(positions_raw, list):
            for row in positions_raw:
                driver_number = row.get("driver_number")
                if driver_number is None:
                    continue
                prev = latest_by_driver.get(driver_number)
                if prev is None or (row.get("date") or "") >= (prev.get("date") or ""):
                    latest_by_driver[driver_number] = row

    def _status_for(driver_number: Any) -> str | None:
        result = result_by_driver.get(driver_number) or {}
        if result.get("dns"):
            return "DNS"
        if result.get("dsq"):
            return "DSQ"
        if result.get("dnf"):
            return "DNF"
        return None

    def _entry(driver_number: Any, position: Any) -> dict[str, Any]:
        driver = driver_map.get(driver_number) or {}
        result = result_by_driver.get(driver_number) or {}
        # Prefer official result position when present
        final_pos = result.get("position") if result.get("position") is not None else position
        code = (
            driver.get("name_acronym")
            or driver.get("broadcast_name")
            or str(driver_number)
        )
        gap = result.get("gap_to_leader")
        try:
            gap_s = float(gap) if gap is not None else None
        except (TypeError, ValueError):
            gap_s = None
        return {
            "position": final_pos,
            "driver_number": driver_number,
            "code": code,
            "team": driver.get("team_name"),
            "status": _status_for(driver_number),
            "gap_to_leader": gap_s,
        }

    entries: list[dict[str, Any]] = []
    if result_by_driver:
        ordered_results = sorted(
            result_by_driver.values(),
            key=lambda item: item.get("position")
            if isinstance(item.get("position"), (int, float))
            else 99,
        )
        for row in ordered_results:
            entries.append(_entry(row.get("driver_number"), row.get("position")))
    elif latest_by_driver:
        ordered = sorted(
            latest_by_driver.values(),
            key=lambda row: row.get("position")
            if isinstance(row.get("position"), (int, float))
            else 99,
        )
        for row in ordered:
            entries.append(_entry(row.get("driver_number"), row.get("position")))

    def _sort_key(entry: dict[str, Any]) -> tuple[int, int]:
        pos = entry.get("position")
        if isinstance(pos, (int, float)):
            return (0, int(pos))
        status = entry.get("status") or ""
        order = {"DNF": 1, "DNS": 2, "DSQ": 3}.get(status, 9)
        return (1, order)

    entries.sort(key=_sort_key)

    label_parts = [
        session_payload.get("circuit_short_name") or session_payload.get("location"),
        session_payload.get("session_name"),
    ]
    payload = {
        "available": bool(entries),
        "live": live,
        "label": " · ".join([p for p in label_parts if p]),
        "session": session_payload,
        "entries": entries,
    }
    return cache.set("standings_ticker", payload, 60)


def _session_is_live(session: dict[str, Any]) -> bool:
    start = parse_iso(session.get("date_start"))
    end = parse_iso(session.get("date_end"))
    now = utc_now()
    if start and end:
        return start <= now <= end + timedelta(minutes=15)
    if start and not end:
        return start <= now <= start + timedelta(hours=3)
    return False


def _session_payload(session: dict[str, Any]) -> dict[str, Any]:
    name = session.get("session_name") or session.get("session_type")
    return {
        "session_key": session.get("session_key"),
        "meeting_key": session.get("meeting_key"),
        "session_name": SESSION_NAME_MAP.get(name, name),
        "session_type": session.get("session_type"),
        "location": session.get("location"),
        "country_name": session.get("country_name"),
        "circuit_short_name": session.get("circuit_short_name"),
        "date_start": session.get("date_start"),
        "date_end": session.get("date_end"),
        "gmt_offset": session.get("gmt_offset"),
        "year": session.get("year"),
    }
