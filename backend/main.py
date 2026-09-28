from __future__ import annotations

from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.config import settings
from backend.jolpica import enrich_track, fetch_season_context
from backend.open_meteo import fetch_forecast
from backend.openf1 import fetch_live_weather, fetch_standings
from backend.radar import fetch_radar_frames
from backend.utils import utc_now

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"

app = FastAPI(title="F1 Track Info", version="1.0.0")


@app.on_event("startup")
async def startup() -> None:
    app.state.http = httpx.AsyncClient(
        timeout=settings.http_timeout,
        headers={"User-Agent": "F1TrackInfo/1.0"},
        follow_redirects=True,
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    await app.state.http.aclose()


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "f1-track-info",
        "time": utc_now().isoformat(),
        "live_poll_seconds": settings.live_poll_seconds,
    }


@app.get("/api/session")
async def session() -> dict:
    try:
        context = await fetch_season_context(app.state.http)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar upstream error: {exc}") from exc
    return context


@app.get("/api/track")
async def track() -> dict:
    try:
        context = await fetch_season_context(app.state.http)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar upstream error: {exc}") from exc

    if not context.get("available"):
        return {"available": False, "reason": context.get("reason", "No track data")}

    circuit = enrich_track(context["circuit"])
    return {
        "available": True,
        "season": context.get("season"),
        "round": context.get("round"),
        "race_name": context.get("race_name"),
        "race_date": context.get("race_date"),
        "race_time": context.get("race_time"),
        "active_session": context.get("active_session"),
        "sessions": context.get("sessions"),
        "circuit": circuit,
    }


@app.get("/api/live-weather")
async def live_weather() -> dict:
    try:
        context = await fetch_season_context(app.state.http)
        circuit = context.get("circuit") if context.get("available") else None
        return await fetch_live_weather(app.state.http, target_circuit=circuit)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"OpenF1 upstream error: {exc}") from exc


@app.get("/api/forecast")
async def forecast() -> dict:
    try:
        context = await fetch_season_context(app.state.http)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Calendar upstream error: {exc}") from exc

    if not context.get("available"):
        return {"available": False, "reason": "No circuit coordinates"}

    circuit = context["circuit"]
    lat, lon = circuit.get("lat"), circuit.get("lon")
    if lat is None or lon is None:
        return {"available": False, "reason": "Circuit coordinates missing"}

    try:
        data = await fetch_forecast(app.state.http, lat, lon)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Open-Meteo upstream error: {exc}") from exc

    data["circuit"] = {
        "name": circuit.get("name"),
        "locality": circuit.get("locality"),
        "country": circuit.get("country"),
        "lat": lat,
        "lon": lon,
    }
    return data


@app.get("/api/standings")
async def standings() -> dict:
    try:
        return await fetch_standings(app.state.http)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"OpenF1 standings error: {exc}") from exc


@app.get("/api/radar")
async def radar() -> dict:
    try:
        return await fetch_radar_frames(app.state.http)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Radar upstream error: {exc}") from exc


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


if FRONTEND.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND), name="static")
