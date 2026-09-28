from __future__ import annotations

from typing import Any

import httpx

from backend.cache import cache
from backend.config import settings
from backend.utils import degrees_to_compass, ms_to_kmh

# Bottom strip: next 3 hours in 6 steps of 30 minutes.
NEAR_TERM_HOURS = 3
NEAR_TERM_STEPS = 6
STEP_MINUTES = 30


async def fetch_forecast(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
) -> dict[str, Any]:
    cache_key = f"forecast:{lat:.3f}:{lon:.3f}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": ",".join(
            [
                "temperature_2m",
                "relative_humidity_2m",
                "apparent_temperature",
                "precipitation",
                "rain",
                "weather_code",
                "cloud_cover",
                "pressure_msl",
                "wind_speed_10m",
                "wind_direction_10m",
                "wind_gusts_10m",
                "is_day",
            ]
        ),
        "minutely_15": ",".join(
            [
                "temperature_2m",
                "precipitation",
                "rain",
                "weather_code",
                "wind_speed_10m",
                "wind_direction_10m",
                "relative_humidity_2m",
            ]
        ),
        "forecast_minutely_15": 12,  # 12 x 15 min = 3 hours
        "hourly": ",".join(
            [
                "temperature_2m",
                "precipitation_probability",
                "precipitation",
                "cloud_cover",
                "wind_speed_10m",
                "wind_direction_10m",
            ]
        ),
        "forecast_hours": NEAR_TERM_HOURS + 1,
        "wind_speed_unit": "ms",
        "timezone": "auto",
    }
    response = await client.get(settings.open_meteo_base, params=params)
    response.raise_for_status()
    data = response.json()

    current = data.get("current") or {}
    wind_ms = current.get("wind_speed_10m")
    wind_dir = current.get("wind_direction_10m")

    minutely = data.get("minutely_15") or {}
    min_times = minutely.get("time") or []
    # Every 30 minutes: indices 0,2,4,6,8,10 within the 3h window
    near_term: list[dict[str, Any]] = []
    for step in range(NEAR_TERM_STEPS):
        i = step * (STEP_MINUTES // 15)
        if i >= len(min_times):
            break
        wind_h = _at(minutely.get("wind_speed_10m"), i)
        dir_h = _at(minutely.get("wind_direction_10m"), i)
        precip = _at(minutely.get("precipitation"), i)
        rain = _at(minutely.get("rain"), i)
        near_term.append(
            {
                "time": min_times[i],
                "temperature_c": _at(minutely.get("temperature_2m"), i),
                "precipitation_mm": precip if precip is not None else rain,
                "humidity": _at(minutely.get("relative_humidity_2m"), i),
                "weather_code": _at(minutely.get("weather_code"), i),
                "wind_speed_ms": wind_h,
                "wind_speed_kmh": ms_to_kmh(wind_h),
                "wind_direction_deg": dir_h,
                "wind_direction_compass": degrees_to_compass(dir_h),
                "step_minutes": step * STEP_MINUTES,
            }
        )

    # Fallback: hourly if minutely missing
    if not near_term:
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        for i, t in enumerate(times[:NEAR_TERM_HOURS]):
            wind_h = _at(hourly.get("wind_speed_10m"), i)
            dir_h = _at(hourly.get("wind_direction_10m"), i)
            near_term.append(
                {
                    "time": t,
                    "temperature_c": _at(hourly.get("temperature_2m"), i),
                    "precipitation_probability": _at(
                        hourly.get("precipitation_probability"), i
                    ),
                    "precipitation_mm": _at(hourly.get("precipitation"), i),
                    "cloud_cover": _at(hourly.get("cloud_cover"), i),
                    "wind_speed_ms": wind_h,
                    "wind_speed_kmh": ms_to_kmh(wind_h),
                    "wind_direction_deg": dir_h,
                    "wind_direction_compass": degrees_to_compass(dir_h),
                    "step_minutes": i * 60,
                }
            )

    payload = {
        "available": True,
        "source": "open-meteo",
        "timezone": data.get("timezone"),
        "horizon_hours": NEAR_TERM_HOURS,
        "step_minutes": STEP_MINUTES,
        "current": {
            "temperature_c": current.get("temperature_2m"),
            "apparent_temperature_c": current.get("apparent_temperature"),
            "humidity": current.get("relative_humidity_2m"),
            "precipitation_mm": current.get("precipitation"),
            "rain_mm": current.get("rain"),
            "cloud_cover": current.get("cloud_cover"),
            "pressure_hpa": current.get("pressure_msl"),
            "weather_code": current.get("weather_code"),
            "is_day": current.get("is_day"),
            "wind_speed_ms": wind_ms,
            "wind_speed_kmh": ms_to_kmh(wind_ms),
            "wind_gusts_ms": current.get("wind_gusts_10m"),
            "wind_gusts_kmh": ms_to_kmh(current.get("wind_gusts_10m")),
            "wind_direction_deg": wind_dir,
            "wind_direction_compass": degrees_to_compass(wind_dir),
            "time": current.get("time"),
        },
        "near_term": near_term,
        "hourly": near_term,
    }
    return cache.set(cache_key, payload, settings.open_meteo_cache_seconds)


def _at(values: list[Any] | None, index: int) -> Any:
    if not values or index >= len(values):
        return None
    return values[index]
