from __future__ import annotations

from typing import Any

import httpx

from backend.cache import cache
from backend.config import settings

RAINVIEWER_MAPS = "https://api.rainviewer.com/public/weather-maps.json"


async def fetch_radar_frames(client: httpx.AsyncClient) -> dict[str, Any]:
    cached = cache.get("radar_frames")
    if cached is not None:
        return cached

    response = await client.get(RAINVIEWER_MAPS)
    response.raise_for_status()
    data = response.json()

    host = data.get("host") or "https://tilecache.rainviewer.com"
    past = data.get("radar", {}).get("past") or []
    nowcast = data.get("radar", {}).get("nowcast") or []
    frames = [
        {
            "time": item.get("time"),
            "path": item.get("path"),
            "tile_url_template": f"{host}{item.get('path')}/256/{{z}}/{{x}}/{{y}}/2/1_1.png",
        }
        for item in [*past, *nowcast]
        if item.get("path") and item.get("time")
    ]

    payload = {
        "available": bool(frames),
        "host": host,
        "generated": data.get("generated"),
        "step_seconds": 600,
        "frames": frames,
    }
    return cache.set("radar_frames", payload, min(settings.open_meteo_cache_seconds, 60))
