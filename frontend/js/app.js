const state = {
  pollMs: 20000,
  previous: {},
  outlines: null,
  map: null,
  marker: null,
  rangeLayer: null,
  windLayer: null,
  radarLayer: null,
  radarFrames: [],
  radarIndex: -1,
  radarTimer: null,
  circuit: null,
  lastCircuitKey: null,
  sessions: [],
  nextSession: null,
};

const RANGE_KM = [5, 10, 15, 20];
const WIND_HORIZON_MIN = 60;
const WIND_STEP_MIN = 10;
const SESSION_UPCOMING_MS = 2 * 24 * 60 * 60 * 1000;
const SESSION_COUNTDOWN_MS = 60 * 60 * 1000;
const SESSION_LIVE_MS = 3 * 60 * 60 * 1000;

function $(id) {
  return document.getElementById(id);
}

function fmt(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toFixed(digits);
}

function dash(value) {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

function setText(id, value) {
  const el = $(id);
  if (!el) return;
  if (el.textContent !== String(value)) el.textContent = value;
}

function flashIfChanged(key, value) {
  const prev = state.previous[key];
  state.previous[key] = value;
  if (prev === undefined || prev === value) return;
  const card = document.querySelector(`.metric[data-key="${key}"]`);
  if (!card) return;
  card.classList.remove("flash");
  void card.offsetWidth;
  card.classList.add("flash");
  setTimeout(() => card.classList.remove("flash"), 500);
}

function setStatus(_status) {
  // Status pill removed from UI
}

function updateClock() {
  setText("clock", new Date().toISOString().slice(11, 19) + "Z");
  updateSessionStatus();
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function formatCountdown(ms) {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return `${pad2(h)}:${pad2(m)}:${pad2(s)}`;
}

function formatSessionWhen(date) {
  return date.toISOString().replace("T", " ").slice(0, 16) + "Z";
}

function pickNextSession(sessions) {
  const list = sessions || state.sessions || [];
  const now = Date.now();
  const upcoming = list
    .map((session) => {
      const raw = session.datetime || `${session.date}T${session.time || "00:00:00Z"}`;
      const start = new Date(raw);
      return { ...session, start, startMs: start.getTime() };
    })
    .filter((session) => !Number.isNaN(session.startMs))
    .sort((a, b) => a.startMs - b.startMs);

  // Prefer a session that is currently live, then the next upcoming one
  const live = [...upcoming]
    .reverse()
    .find((session) => {
      const elapsed = now - session.startMs;
      return elapsed >= 0 && elapsed < SESSION_LIVE_MS;
    });
  if (live) return live;

  return upcoming.find((session) => session.startMs > now) || null;
}

function updateSessionStatus() {
  const meta = $("session-meta");
  const nameEl = $("session-name");
  if (!nameEl || !meta) return;

  state.nextSession = pickNextSession(state.sessions);
  const session = state.nextSession;
  if (!session) {
    setText("session-name", "SESSION");
    setText("session-meta", "No upcoming session");
    meta.dataset.mode = "idle";
    return;
  }

  const now = Date.now();
  const ms = session.startMs - now;
  const circuitBit = state.circuit?.name || state.circuit?.locality || "";

  if (ms <= 0 && now - session.startMs < SESSION_LIVE_MS) {
    const elapsed = now - session.startMs;
    setText("session-name", `LIVE ${session.name}`);
    setText("session-meta", formatCountdown(elapsed));
    meta.dataset.mode = "live";
    return;
  }

  if (ms > 0 && ms <= SESSION_COUNTDOWN_MS) {
    setText("session-name", `NEXT ${session.name}`);
    setText("session-meta", formatCountdown(ms));
    meta.dataset.mode = "countdown";
    return;
  }

  if (ms > 0 && ms <= SESSION_UPCOMING_MS) {
    setText("session-name", "SESSION UPCOMING");
    setText(
      "session-meta",
      [session.name, circuitBit, formatSessionWhen(session.start)].filter(Boolean).join(" · ")
    );
    meta.dataset.mode = "upcoming";
    return;
  }

  if (ms > 0) {
    setText("session-name", `NEXT ${session.name}`);
    setText(
      "session-meta",
      [circuitBit, formatSessionWhen(session.start)].filter(Boolean).join(" · ")
    );
    meta.dataset.mode = "scheduled";
    return;
  }

  setText("session-name", session.name || "SESSION");
  setText("session-meta", [circuitBit, "COMPLETED"].filter(Boolean).join(" · "));
  meta.dataset.mode = "done";
}

async function fetchJson(path) {
  const res = await fetch(path, { cache: "no-store" });
  if (!res.ok) throw new Error(`${path} failed (${res.status})`);
  return res.json();
}

async function loadOutlines() {
  if (state.outlines) return state.outlines;
  try {
    state.outlines = await fetchJson("/static/data/track-outlines.json");
  } catch (err) {
    console.warn("Track outlines unavailable", err);
    state.outlines = {};
  }
  return state.outlines;
}

function findOutline(circuitId, circuitName) {
  const outlines = state.outlines || {};
  const needle = (circuitId || "").toLowerCase();
  const nameNeedle = (circuitName || "").toLowerCase().replace(/\s+/g, "_");
  if (needle && outlines[needle]) return outlines[needle];
  for (const [id, entry] of Object.entries(outlines)) {
    const aliases = (entry.aliases || []).map((a) => a.toLowerCase());
    if (id === needle || aliases.includes(needle) || aliases.includes(nameNeedle)) {
      return entry;
    }
    if (circuitName && aliases.some((a) => circuitName.toLowerCase().includes(a.replace(/_/g, " ")))) {
      return entry;
    }
  }
  return null;
}

function pointsToPath(points) {
  if (!points?.length) return "";
  return points
    .map((p, i) => `${i === 0 ? "M" : "L"}${p[0]} ${p[1]}`)
    .join(" ") + " Z";
}

function ensureMap() {
  if (state.map) return state.map;
  if (typeof L === "undefined") {
    console.error("Leaflet failed to load");
    return null;
  }

  const el = $("geo-map");
  if (!el) return null;

  state.map = L.map(el, {
    zoomControl: true,
    attributionControl: true,
    scrollWheelZoom: true,
    doubleClickZoom: true,
    boxZoom: false,
    keyboard: true,
    touchZoom: true,
    dragging: true,
  });

  // Satellite imagery so the circuit is actually visible (dark street tiles look black).
  L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      attribution: "Tiles &copy; Esri",
      maxZoom: 19,
    }
  ).addTo(state.map);

  L.tileLayer(
    "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
    {
      attribution: "Labels &copy; Esri",
      maxZoom: 19,
      opacity: 0.85,
    }
  ).addTo(state.map);

  state.map.setView([20, 0], 2);
  return state.map;
}

function refreshMapSize() {
  if (!state.map) return;
  state.map.invalidateSize(true);
}

function clearRangeOverlay() {
  if (state.rangeLayer && state.map) {
    state.map.removeLayer(state.rangeLayer);
  }
  state.rangeLayer = null;
  if (state.marker && state.map) {
    state.map.removeLayer(state.marker);
  }
  state.marker = null;
}

function clearWindOverlay() {
  if (state.windLayer && state.map) {
    state.map.removeLayer(state.windLayer);
  }
  state.windLayer = null;
}

function destinationPoint(lat, lon, bearingDeg, distanceM) {
  const rad = Math.PI / 180;
  const R = 6371000;
  const δ = distanceM / R;
  const θ = bearingDeg * rad;
  const φ1 = lat * rad;
  const λ1 = lon * rad;
  const sinφ1 = Math.sin(φ1);
  const cosφ1 = Math.cos(φ1);
  const sinδ = Math.sin(δ);
  const cosδ = Math.cos(δ);
  const sinφ2 = sinφ1 * cosδ + cosφ1 * sinδ * Math.cos(θ);
  const φ2 = Math.asin(sinφ2);
  const λ2 =
    λ1 +
    Math.atan2(Math.sin(θ) * sinδ * cosφ1, cosδ - sinφ1 * sinφ2);
  return [φ2 / rad, ((λ2 / rad + 540) % 360) - 180];
}

function buildCrossIcon() {
  return L.divIcon({
    className: "circuit-cross-wrap",
    html: '<div class="circuit-cross" aria-hidden="true"><span></span><span></span></div>',
    iconSize: [28, 28],
    iconAnchor: [14, 14],
  });
}

function drawRangeRings(map, lat, lon) {
  clearRangeOverlay();
  state.rangeLayer = L.layerGroup().addTo(map);

  RANGE_KM.forEach((km) => {
    L.circle([lat, lon], {
      radius: km * 1000,
      color: "rgba(230, 236, 242, 0.75)",
      weight: 1.25,
      fill: false,
      interactive: false,
    }).addTo(state.rangeLayer);

    const northLat = lat + km / 110.574;
    L.marker([northLat, lon], {
      interactive: false,
      keyboard: false,
      icon: L.divIcon({
        className: "range-label-wrap",
        html: `<span class="range-label">${km} km</span>`,
        iconSize: [48, 16],
        iconAnchor: [24, 8],
      }),
    }).addTo(state.rangeLayer);
  });

  state.marker = L.marker([lat, lon], {
    interactive: false,
    keyboard: false,
    icon: buildCrossIcon(),
    zIndexOffset: 500,
  }).addTo(map);

  const pad = 0.02;
  const bounds = L.latLngBounds(
    [lat - 20 / 110.574 - pad, lon - 20 / (111.32 * Math.cos((lat * Math.PI) / 180)) - pad],
    [lat + 20 / 110.574 + pad, lon + 20 / (111.32 * Math.cos((lat * Math.PI) / 180)) + pad]
  );
  map.fitBounds(bounds, { animate: false, padding: [12, 12] });
  // Lock zoom-out at this overview level; zoom-in remains allowed.
  const baseZoom = map.getZoom();
  map.setMinZoom(baseZoom);
  map.setMaxZoom(18);
}

function drawWindVector(lat, lon, windFromDeg, windSpeedMs, windSpeedKmh) {
  clearWindOverlay();
  if (!state.map || windFromDeg == null || windSpeedMs == null || windSpeedMs < 0.2) {
    return;
  }

  state.windLayer = L.layerGroup().addTo(state.map);
  const toward = (Number(windFromDeg) + 180) % 360;
  const fromBearing = Number(windFromDeg);
  const metersPerStep = Number(windSpeedMs) * WIND_STEP_MIN * 60;
  const totalMeters = Number(windSpeedMs) * WIND_HORIZON_MIN * 60;

  // Line from upwind (where weather comes from) toward the circuit
  const start = destinationPoint(lat, lon, fromBearing, totalMeters);
  const line = [start, [lat, lon]];
  L.polyline(line, {
    color: "#e10600",
    weight: 3,
    opacity: 0.95,
    interactive: false,
  }).addTo(state.windLayer);

  // Tick marks every 10 minutes of advection
  const steps = Math.max(1, Math.round(WIND_HORIZON_MIN / WIND_STEP_MIN));
  for (let i = 1; i <= steps; i += 1) {
    const dist = metersPerStep * i;
    const point = destinationPoint(lat, lon, fromBearing, dist);
    const tickA = destinationPoint(point[0], point[1], fromBearing + 90, 350);
    const tickB = destinationPoint(point[0], point[1], fromBearing - 90, 350);
    L.polyline([tickA, tickB], {
      color: "#e10600",
      weight: 2,
      opacity: 0.9,
      interactive: false,
    }).addTo(state.windLayer);

    L.marker(point, {
      interactive: false,
      keyboard: false,
      icon: L.divIcon({
        className: "wind-tick-label-wrap",
        html: `<span class="wind-tick-label">-${i * WIND_STEP_MIN}m</span>`,
        iconSize: [36, 14],
        iconAnchor: [18, -4],
      }),
    }).addTo(state.windLayer);
  }

  const mid = destinationPoint(lat, lon, fromBearing, totalMeters * 0.55);
  L.marker(mid, {
    interactive: false,
    keyboard: false,
    icon: L.divIcon({
      className: "wind-speed-label-wrap",
      html: `<span class="wind-speed-label">${fmt(windSpeedKmh ?? windSpeedMs * 3.6, 0)} km/h FROM ${fmt(windFromDeg, 0)}°</span>`,
      iconSize: [140, 18],
      iconAnchor: [70, 9],
    }),
  }).addTo(state.windLayer);

  // Small arrow tip near circuit showing flow direction
  const tip = destinationPoint(lat, lon, toward, 600);
  L.polyline([[lat, lon], tip], {
    color: "#e10600",
    weight: 2,
    opacity: 0.7,
    dashArray: "4 4",
    interactive: false,
  }).addTo(state.windLayer);
}

function setRadarFrame(index) {
  if (!state.map || !state.radarFrames.length) return;
  const frames = state.radarFrames;
  const i = ((index % frames.length) + frames.length) % frames.length;
  state.radarIndex = i;
  const frame = frames[i];

  if (state.radarLayer) {
    state.map.removeLayer(state.radarLayer);
    state.radarLayer = null;
  }

  state.radarLayer = L.tileLayer(frame.tile_url_template, {
    opacity: 0.55,
    maxZoom: 12,
    maxNativeZoom: 7,
    zIndex: 350,
  }).addTo(state.map);

  const dt = new Date(frame.time * 1000);
  setText(
    "radar-time",
    `Radar ${dt.toISOString().slice(11, 16)}Z`
  );
}

function stopRadarPlay() {
  if (state.radarTimer) {
    clearInterval(state.radarTimer);
    state.radarTimer = null;
  }
  const btn = $("radar-play");
  if (btn) btn.textContent = "▶";
}

function startRadarPlay() {
  if (!state.radarFrames.length) return;
  if (state.radarTimer) return;
  const btn = $("radar-play");
  if (btn) btn.textContent = "❚❚";
  state.radarTimer = setInterval(() => {
    setRadarFrame(state.radarIndex + 1);
  }, 900);
}

function toggleRadarPlay() {
  if (state.radarTimer) {
    stopRadarPlay();
    return;
  }
  startRadarPlay();
}

async function loadRadar() {
  try {
    const data = await fetchJson("/api/radar");
    state.radarFrames = data.frames || [];
    if (!state.radarFrames.length) {
      setText("radar-time", "Radar unavailable");
      stopRadarPlay();
      return;
    }
    setRadarFrame(state.radarFrames.length - 1);
    startRadarPlay();
  } catch (err) {
    console.warn(err);
    setText("radar-time", "Radar error");
    stopRadarPlay();
  }
}

function updateMap(circuit) {
  const map = ensureMap();
  const lat = circuit?.lat;
  const lon = circuit?.lon;
  const name = circuit?.name || "Circuit";
  state.circuit = circuit;
  setText("map-circuit-name", name);

  if (map && lat != null && lon != null) {
    const key = `${circuit.circuit_id || name}:${lat}:${lon}`;
    if (state.lastCircuitKey !== key) {
      state.lastCircuitKey = key;
      drawRangeRings(map, lat, lon);
      loadRadar();
    }
    requestAnimationFrame(() => {
      refreshMapSize();
      setTimeout(refreshMapSize, 150);
      setTimeout(refreshMapSize, 500);
    });
  }

  const svg = $("track-svg");
  const path = $("track-path");
  path.setAttribute("d", "");
  svg.classList.remove("is-visible");
}

function updateWindFromWeather(live, forecast) {
  const c = state.circuit;
  if (!c?.lat || c?.lon == null) return;

  if (live?.available) {
    drawWindVector(
      c.lat,
      c.lon,
      live.wind_direction_deg,
      live.wind_speed_ms,
      live.wind_speed_kmh
    );
    return;
  }

  const current = forecast?.current || {};
  drawWindVector(
    c.lat,
    c.lon,
    current.wind_direction_deg,
    current.wind_speed_ms,
    current.wind_speed_kmh
  );
}

function applyTrack(track) {
  if (!track?.available) {
    setText("race-title", "No race weekend data");
    return;
  }
  const c = track.circuit || {};
  const location = [c.locality, c.country].filter(Boolean).join(", ");
  setText("race-title", `${track.race_name || "Grand Prix"} · Round ${track.round || "—"}`);
  setText("track-name", dash(c.name));
  setText("track-location", dash(location));
  setText("track-gp", dash(track.race_name));
  setText("track-round", dash(track.round));
  setText("track-length", c.length_km != null ? `${fmt(c.length_km, 3)} km` : "—");
  setText("track-laps", dash(c.laps));
  setText(
    "track-distance",
    c.race_distance_km != null ? `${fmt(c.race_distance_km, 3)} km` : "—"
  );
  const raceTime = [track.race_date, track.race_time].filter(Boolean).join(" ");
  setText("track-race-time", dash(raceTime));

  state.circuit = c;
  state.sessions = Array.isArray(track.sessions) ? track.sessions : [];
  if (track.active_session && !state.sessions.some((s) => s.name === track.active_session.name && s.datetime === track.active_session.datetime)) {
    state.sessions = [...state.sessions, track.active_session];
  }
  updateSessionStatus();

  updateMap(c);
}

function applyLive(live, forecast) {
  const useLive = Boolean(live?.available);
  const current = forecast?.current || {};

  if (useLive) {
    setStatus(live.status || "LIVE");
    setText("air-temp", fmt(live.air_temperature));
    setText("track-temp", fmt(live.track_temperature));
    setText(
      "wind-speed",
      fmt(
        live.wind_speed_kmh ??
          (live.wind_speed_ms != null ? live.wind_speed_ms * 3.6 : null),
        1
      )
    );
    setText("wind-dir", live.wind_direction_compass || "--");
    setText("wind-deg", fmt(live.wind_direction_deg, 0));
    setText("rain", fmt(live.rainfall, 1));
    setText("humidity", fmt(live.humidity, 0));
    setText("pressure", fmt(live.pressure, 0));
    setWindArrow(live.wind_direction_deg);
    flashIfChanged("air", live.air_temperature);
    flashIfChanged("track", live.track_temperature);
    flashIfChanged("wind", live.wind_speed_kmh);
    flashIfChanged("rain", live.rainfall);
    flashIfChanged("hum", live.humidity);
    flashIfChanged("press", live.pressure);
    updateWindFromWeather(live, forecast);
    return;
  }

  setStatus("FORECAST ONLY");
  setText("air-temp", fmt(current.temperature_c));
  setText("track-temp", "--");
  setText("wind-speed", fmt(current.wind_speed_kmh, 1));
  setText("wind-dir", current.wind_direction_compass || "--");
  setText("wind-deg", fmt(current.wind_direction_deg, 0));
  setText("rain", fmt(current.precipitation_mm ?? current.rain_mm, 1));
  setText("humidity", fmt(current.humidity, 0));
  setText("pressure", fmt(current.pressure_hpa, 0));
  setWindArrow(current.wind_direction_deg);
  updateWindFromWeather(live, forecast);
}

function setWindArrow(degrees) {
  const arrow = $("wind-arrow");
  if (!arrow) return;
  if (degrees === null || degrees === undefined || Number.isNaN(Number(degrees))) {
    arrow.style.transform = "rotate(0deg)";
    return;
  }
  arrow.style.transform = `rotate(${Number(degrees)}deg)`;
}

function applyStandings(standings) {
  const label = $("ticker-label");
  const track = $("ticker-track");
  if (!track) return;

  if (standings?.available && Array.isArray(standings.entries) && standings.entries.length) {
    if (label) {
      label.textContent = standings.live ? "LIVE" : "LAST";
    }
    const parts = standings.entries.map((entry) => {
      const pos = entry.position != null ? `P${entry.position}` : "P-";
      let suffix = "";
      if (entry.status) {
        suffix = ` - ${entry.status}`;
      } else if (entry.gap_to_leader != null && Number(entry.gap_to_leader) > 0) {
        suffix = ` +${Number(entry.gap_to_leader).toFixed(3)}`;
      }
      return `<span class="pos">${pos}</span>${entry.code || entry.driver_number}${suffix}`;
    });
    const headline = standings.label ? `${standings.label}` : "STANDINGS";
    const line = `${headline} · ${parts.join('<span class="sep">|</span>')}`;
    // Duplicate for seamless left-to-right loop
    track.innerHTML = `<span class="ticker-text">${line}</span><span class="ticker-text">${line}</span>`;
    return;
  }

  if (label) label.textContent = "STANDINGS";
  const fallback = "No live classification · waiting for next session";
  track.innerHTML = `<span class="ticker-text">${fallback}</span><span class="ticker-text">${fallback}</span>`;
}

function applyForecast(forecast) {
  const strip = $("forecast-strip");
  const steps = forecast?.near_term || forecast?.hourly;
  if (!forecast?.available || !Array.isArray(steps) || !steps.length) {
    strip.innerHTML =
      '<div class="forecast-card"><div class="t">N/A</div><div class="meta">Forecast unavailable</div></div>';
    return;
  }
  const stepMin = forecast.step_minutes || 30;
  const horizon = forecast.horizon_hours || 3;
  setText(
    "forecast-note",
    `Next ${horizon}h · every ${stepMin} min · ${forecast.circuit?.name || "circuit"}`
  );
  strip.innerHTML = steps
    .slice(0, 6)
    .map((h) => {
      const time = (h.time || "").slice(11, 16) || "--:--";
      const rain =
        h.precipitation_probability != null
          ? `Rain ${dash(h.precipitation_probability)}%`
          : `Rain ${fmt(h.precipitation_mm, 1)} mm`;
      return `<article class="forecast-card">
        <div class="t">${time}</div>
        <div class="temp">${fmt(h.temperature_c, 0)}°</div>
        <div class="meta">${rain}<br/>Wind ${fmt(h.wind_speed_kmh, 0)} km/h ${h.wind_direction_compass || ""}</div>
      </article>`;
    })
    .join("");
}

async function refresh() {
  try {
    await loadOutlines();
    const [health, track, live, forecast, standings] = await Promise.all([
      fetchJson("/api/health"),
      fetchJson("/api/track"),
      fetchJson("/api/live-weather"),
      fetchJson("/api/forecast"),
      fetchJson("/api/standings"),
    ]);
    if (health.live_poll_seconds) {
      state.pollMs = health.live_poll_seconds * 1000;
    }
    applyTrack(track);
    applyLive(live, forecast);
    applyForecast(forecast);
    applyStandings(standings);
  } catch (err) {
    console.error(err);
    setStatus("FORECAST ONLY");
  }
}

updateClock();
setInterval(updateClock, 1000);

$("radar-prev")?.addEventListener("click", () => {
  setRadarFrame(state.radarIndex - 1);
  startRadarPlay();
});
$("radar-next")?.addEventListener("click", () => {
  setRadarFrame(state.radarIndex + 1);
  startRadarPlay();
});
$("radar-play")?.addEventListener("click", () => toggleRadarPlay());

async function loop() {
  await refresh();
  setTimeout(loop, state.pollMs);
}

loop();
window.addEventListener("resize", refreshMapSize);
