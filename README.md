# F1 Track Info

Standalone pitwall-style web app for Formula 1 track weather and circuit information.
No Home Assistant. No API keys required.

## Features

- Live track weather via OpenF1 (air/track temp, wind, rain, humidity, pressure)
- Circuit forecast via Open-Meteo
- Track info for the current/next Grand Prix weekend
- Circuit map on the left (geo map + track outline when available), weather/info on the right
- English pitwall UI, Docker-ready

## Quick start

```bash
docker compose up --build
```

Open [http://localhost:8765](http://localhost:8765).

## Configuration

Copy `.env.example` to `.env` if you want to change the host port or cache intervals.

| Variable | Default | Meaning |
|----------|---------|---------|
| `PORT` | `8765` | Host port |
| `LIVE_POLL_SECONDS` | `20` | Browser poll interval hint |
| `OPENF1_CACHE_SECONDS` | `20` | Server cache for live weather |
| `OPEN_METEO_CACHE_SECONDS` | `60` | Server cache for forecast |
| `SESSION_CACHE_SECONDS` | `300` | Server cache for calendar/track |

## Fast-Release deploy

CI builds and pushes the image to GHCR, then notifies Fast-Release on every push to `main`.

Public URL (example): `https://f1trackweahter.kingofnoobie.fast-release.com`

### GitHub Actions secrets

Repo → **Settings → Secrets and variables → Actions**:

| Secret | Value |
|--------|--------|
| `FAST_RELEASE_DEPLOY_TOKEN` | Deploy token from the Fast-Release dashboard (shown once at project create) |
| `FAST_RELEASE_PROJECT_ID` | `cmulpx8fx0001hkxs0ih27gi0` |
| `FAST_RELEASE_API_URL` | Base URL of your Fast-Release API (no trailing slash). Local API is `http://localhost:4000`, but GitHub runners cannot reach localhost — use a tunnel (ngrok/cloudflared) or your public API host |

Do not commit these values. The workflow uses `${{ secrets.* }}` only.

Workflow file: [`.github/workflows/fast-release.yml`](.github/workflows/fast-release.yml)

## API

- `GET /api/health`
- `GET /api/session`
- `GET /api/track`
- `GET /api/live-weather`
- `GET /api/forecast`

## Data sources

- **OpenF1** — official-style session track weather (most accurate during sessions)
- **Open-Meteo** — location weather / forecast at the circuit
- **Jolpica** — F1 calendar and circuit metadata

## Local development (without Docker)

```bash
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --app-dir .
```

Run from the repository root so `frontend/` resolves correctly.
