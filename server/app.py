#!/usr/bin/env python3
"""Sofia Transit Mirror server.

Serves the static assets built by build.py (docs/ — style.css, favorites.js,
manifest, icons, and the pre-baked line-314 pages) plus:

- /stops/{code}            — dynamic, stop-centric page for ANY physical
  stop in Sofia: live board (all lines) + "show timetable" per line, fetched
  and cached lazily on first request (no need to pre-fetch every line).
- /api/stops/nearest        — nearest stop to a given lat/lon, over the full
  ~2800-stop directory (data/all_stops.json).
- /api/stops/search         — simple substring search over stop names/codes.
- /api/schedule/{ext_id}/stop/{code} — lazy, cached per-line schedule lookup
  scoped to one stop (used by the "show timetable" toggle on /stops/{code}).
- /api/favorites (GET/POST/DELETE) — per-user (device-scoped via a
  client-generated id), persisted in SQLite. Favorites are per PHYSICAL STOP,
  not per line, since /stops/{code} already shows every line at that stop.
- /api/virtual/{stop_code}  — proxies sofiatraffic.bg's live "Virtual
  timetable" endpoint server-side (needs a session + CSRF cookie dance a
  browser can't do cross-origin), with a short cache.
"""
import json
import sqlite3
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from templates import (  # noqa: E402
    CURRENT_HOUR_SCRIPT,
    LIVE_SCRIPT,
    esc,
    extract_departures_for_stop,
    list_stops_for_line,
    page,
)

BASE = "https://www.sofiatraffic.bg"
DATA_DIR = ROOT / "data"
DB_PATH = Path(__file__).parent / "favorites.db"
STATIC_DIR = ROOT / "docs"
SCHEDULE_CACHE_DIR = Path(__file__).parent / "schedule_cache"
SCHEDULE_CACHE_DIR.mkdir(exist_ok=True)

CACHE_TTL = 15  # seconds; keeps live-widget polling from hammering upstream
SESSION_TTL = 1800  # refresh the upstream session/CSRF cookie every 30 min
SCHEDULE_CACHE_TTL = 24 * 3600  # a line's fixed timetable barely changes

app = FastAPI()

ALL_STOPS = json.loads((DATA_DIR / "all_stops.json").read_text())
ALL_LINES = json.loads((DATA_DIR / "all_lines.json").read_text())
STOPS_BY_CODE = {s["code"]: s for s in ALL_STOPS}
LINES_BY_EXT_ID = {l["ext_id"]: l for l in ALL_LINES}
LINES_BY_NAME = {}
for _l in ALL_LINES:
    LINES_BY_NAME.setdefault(_l["name"], []).append(_l)


def haversine_km(lat1, lon1, lat2, lon2):
    from math import asin, cos, radians, sin, sqrt

    r = 6371
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(dlon / 2) ** 2
    return 2 * r * asin(sqrt(a))


# --- Favorites (per user, per physical stop) ---


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS favorites (
            user_id TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            lat REAL,
            lon REAL,
            path TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (user_id, code)
        )
        """
    )
    conn.commit()
    conn.close()


init_db()


def _require_user_id(value):
    if not value:
        raise HTTPException(status_code=400, detail="missing user_id")
    return value


@app.get("/api/favorites")
def list_favorites(user_id: str):
    _require_user_id(user_id)
    conn = get_db()
    rows = conn.execute(
        "SELECT code, name, lat, lon, path FROM favorites WHERE user_id=? ORDER BY created_at",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/favorites")
async def add_favorite(request: Request):
    data = await request.json()
    _require_user_id(data.get("user_id"))
    for field in ("code", "name", "path"):
        if not data.get(field):
            raise HTTPException(status_code=400, detail=f"missing {field}")
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO favorites (user_id, code, name, lat, lon, path, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            data["user_id"],
            data["code"],
            data["name"],
            data.get("lat"),
            data.get("lon"),
            data["path"],
            time.time(),
        ),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


@app.delete("/api/favorites")
def remove_favorite(user_id: str, code: str):
    _require_user_id(user_id)
    conn = get_db()
    conn.execute("DELETE FROM favorites WHERE user_id=? AND code=?", (user_id, code))
    conn.commit()
    conn.close()
    return {"ok": True}


# --- Stop directory: nearest / search ---


@app.get("/api/stops/nearest")
def nearest_stop(lat: float, lon: float):
    best = min(ALL_STOPS, key=lambda s: haversine_km(lat, lon, float(s["latitude"]), float(s["longitude"])))
    return {
        "code": best["code"],
        "name": best["name"],
        "lat": float(best["latitude"]),
        "lon": float(best["longitude"]),
    }


@app.get("/api/stops/search")
def search_stops(q: str, limit: int = 15):
    ql = q.strip().lower()
    if not ql:
        return []
    matches = [
        s for s in ALL_STOPS
        if ql in s["name"].lower() or ql in s["code"]
    ][:limit]
    return [{"code": s["code"], "name": s["name"], "lat": float(s["latitude"]), "lon": float(s["longitude"])} for s in matches]


@app.get("/api/lines/search")
def search_lines(q: str, limit: int = 15):
    ql = q.strip().lower()
    if not ql:
        return []
    # exact-prefix matches on the line number/name first (e.g. "9" -> "9", "94", "94B"),
    # since line names are short and users mostly type the number they're looking for
    matches = [l for l in ALL_LINES if l["name"].lower().startswith(ql)]
    matches.sort(key=lambda l: (len(l["name"]), l["name"]))
    return [{"ext_id": l["ext_id"], "name": l["name"], "type": l["type"]} for l in matches[:limit]]


# --- Live virtual timetable proxy ---

_session_lock = threading.Lock()
_session = None
_session_ts = 0.0
_vt_cache = {}


def _fresh_session():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0"})
    s.get(f"{BASE}/en/public-transport", timeout=10)
    return s


def get_session(force=False):
    global _session, _session_ts
    with _session_lock:
        if force or _session is None or time.time() - _session_ts > SESSION_TTL:
            _session = _fresh_session()
            _session_ts = time.time()
        return _session


def _upstream_post(path, payload):
    """POST to sofiatraffic.bg with the session/CSRF dance, retrying once
    with a fresh session if the first attempt fails."""
    for attempt in (0, 1):
        session = get_session(force=attempt == 1)
        xsrf = urllib.parse.unquote(session.cookies.get("XSRF-TOKEN") or "")
        resp = session.post(
            f"{BASE}{path}",
            json=payload,
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "X-XSRF-TOKEN": xsrf,
                "Referer": f"{BASE}/en/public-transport",
                "Accept": "application/json",
            },
            timeout=10,
        )
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                continue
    raise HTTPException(status_code=502, detail="upstream unavailable")


@app.get("/api/virtual/{stop_code}")
def virtual_table(stop_code: str, type: int = 1):
    cache_key = (stop_code, type)
    now = time.time()
    cached = _vt_cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]
    data = _upstream_post("/bg/trip/getVirtualTable", {"stop": stop_code, "type": type})
    _vt_cache[cache_key] = (now, data)
    return data


# --- Lazy, cached per-line schedule lookup ---


def fetch_schedule_for_line(ext_id):
    line = LINES_BY_EXT_ID.get(ext_id)
    if not line:
        raise HTTPException(status_code=404, detail="unknown line")
    cache_path = SCHEDULE_CACHE_DIR / f"{ext_id}.json"
    if cache_path.exists() and time.time() - cache_path.stat().st_mtime < SCHEDULE_CACHE_TTL:
        return json.loads(cache_path.read_text())
    data = _upstream_post(
        "/bg/trip/getSchedule",
        {
            "line_id": line["line_id"],
            "name": line["name"],
            "ext_id": line["ext_id"],
            "type": line["type"],
            "color": line["color"],
            "icon": line["icon"],
            "isWeekend": 0,
        },
    )
    cache_path.write_text(json.dumps(data))
    return data


@app.get("/api/schedule/{ext_id}/stop/{code}")
def schedule_for_stop(ext_id: str, code: str):
    data = fetch_schedule_for_line(ext_id)
    return extract_departures_for_stop(data, code)


# --- Dynamic, stop-centric page (any physical stop, any line) ---

STOP_PAGE_SCRIPT = """
<script>
document.addEventListener('DOMContentLoaded', function () {
  var container = document.getElementById('line-toggles');
  container.querySelectorAll('.line-toggle').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var extId = btn.dataset.extId;
      var target = document.getElementById('schedule-' + extId);
      var isOpen = btn.classList.toggle('open');
      target.hidden = !isOpen;
      if (isOpen && !target.dataset.loaded) {
        target.innerHTML = '<p class="muted">Loading timetable\\u2026</p>';
        fetch('/api/schedule/' + encodeURIComponent(extId) + '/stop/' + encodeURIComponent(STOP.code))
          .then(function (r) { if (!r.ok) throw new Error('bad'); return r.json(); })
          .then(function (sections) {
            if (sections.length === 0) {
              target.innerHTML = '<p class="muted">No boardable departures found here for this line.</p>';
              return;
            }
            target.innerHTML = sections.map(function (s) {
              return '<h4>' + s.label + '</h4>' + renderTimesTable(s.times_by_daytype);
            }).join('');
            target.dataset.loaded = '1';
          })
          .catch(function () { target.innerHTML = '<p class="muted">Could not load this timetable right now.</p>'; });
      }
    });
  });
});

function renderTimesTable(timesByDaytype) {
  var labels = { 0: 'Weekday', 1: 'Weekend / Holiday' };
  var html = '';
  [0, 1].forEach(function (wk) {
    var times = timesByDaytype[wk];
    if (!times || times.length === 0) return;
    var hours = {};
    times.forEach(function (t) {
      var parts = t.split(':');
      (hours[parts[0]] = hours[parts[0]] || []).push(parts[1]);
    });
    html += '<h5>' + labels[wk] + '</h5><table class="timetable"><tbody>';
    Object.keys(hours).sort().forEach(function (h) {
      html += '<tr><th>' + h + ':00</th><td>' + hours[h].sort().map(function (m) { return h + ':' + m; }).join(', ') + '</td></tr>';
    });
    html += '</tbody></table>';
  });
  return html || '<p class="muted">No scheduled times found.</p>';
}
</script>
"""


@app.get("/stops/{code}", response_class=HTMLResponse)
def stop_page(code: str):
    stop = STOPS_BY_CODE.get(code)
    if not stop:
        raise HTTPException(status_code=404, detail="unknown stop")

    lat, lon = float(stop["latitude"]), float(stop["longitude"])
    stop_json = json.dumps({"code": code, "name": stop["name"], "lat": lat, "lon": lon, "path": f"/stops/{code}"})

    # Seed the "which lines serve this stop" list from the live board (cheap,
    # always available) rather than eagerly fetching every one of the 139
    # lines' full schedules just to find out which pass through here.
    try:
        live = _upstream_post("/bg/trip/getVirtualTable", {"stop": code, "type": 1})
    except HTTPException:
        live = {}
    expected_last_stop = f"A{code}"
    lines_here = {}
    for entry in live.values():
        if entry.get("last_stop") == expected_last_stop:
            continue  # arrival-only here, not boardable
        lines_here[entry["ext_id"]] = entry["name"]

    toggles = "\n".join(
        f'<button class="line-toggle" data-ext-id="{esc(ext_id)}">{esc(name)}</button>'
        f'<div id="schedule-{esc(ext_id)}" class="line-schedule" hidden></div>'
        for ext_id, name in sorted(lines_here.items(), key=lambda kv: kv[1])
    )
    if not toggles:
        toggles = '<p class="muted">No lines currently detected live at this stop — try again in a minute.</p>'

    map_link = (
        f'<a href="https://www.openstreetmap.org/?mlat={lat}&amp;mlon={lon}#map=18/{lat}/{lon}" '
        f'target="_blank" rel="noopener">View on map</a>'
    )
    body = (
        f'<h1>{esc(stop["name"])} <span class="code">#{esc(code)}</span></h1>'
        f'<p class="stop-actions">{map_link} <button id="fav-btn" class="fav-btn" disabled>☆ Save to favorites</button></p>'
        '<section class="line-block live-block"><h2>Live now</h2>'
        '<div id="live-times" class="live-times"><p class="muted">Loading live data…</p></div></section>'
        '<section class="line-block"><h2>Full timetable by line</h2>'
        '<p class="muted">Tap a line to load its fixed timetable for this stop (fetched once, cached).</p>'
        f'<div id="line-toggles">{toggles}</div></section>'
    )
    fav_script = """
<script>
document.addEventListener('DOMContentLoaded', function () {
  var btn = document.getElementById('fav-btn');
  function render() {
    STM.getFavorites().then(function (favs) {
      var fav = STM.isFavorite(STOP.code, favs);
      btn.textContent = fav ? '\\u2605 Remove from favorites' : '\\u2606 Save to favorites';
      btn.classList.toggle('is-fav', fav);
      btn.disabled = false;
      btn.onclick = function () {
        btn.disabled = true;
        var action = fav ? STM.removeFavorite(STOP.code) : STM.addFavorite(STOP);
        action.then(render);
      };
    }).catch(function () { btn.textContent = 'Favorites unavailable'; btn.disabled = true; });
  }
  render();
});
</script>
"""
    html = page(
        f"{stop['name']} (#{code})",
        body,
        static_prefix="/",
        extra_head=f"<script>window.STOP = {stop_json};</script>" + CURRENT_HOUR_SCRIPT + LIVE_SCRIPT + fav_script + STOP_PAGE_SCRIPT,
    )
    return HTMLResponse(html)


# Dynamic "browse this line" page for ANY line, fetched+cached lazily. Lives
# at /l/{ext_id} (not /lines/{name}/...) to avoid colliding with the
# pre-built static pages the static mount below serves for line 314.
@app.get("/l/{ext_id}", response_class=HTMLResponse)
def line_page(ext_id: str):
    line = LINES_BY_EXT_ID.get(ext_id)
    if not line:
        raise HTTPException(status_code=404, detail="unknown line")
    data = fetch_schedule_for_line(ext_id)
    directions = list_stops_for_line(data)

    blocks = []
    for d in directions:
        items = "\n".join(
            f'<li><a href="/stops/{esc(st["code"])}">{esc(st["name"])}</a> <span class="code">#{esc(st["code"])}</span></li>'
            for st in d["stops"]
        )
        blocks.append(f'<h2>{esc(d["direction"].title())}</h2><ol class="stop-list">{items}</ol>')
    body = f'<h1>Line {esc(line["name"])}</h1>' + "\n".join(blocks)
    html = page(f"Line {line['name']}", body, static_prefix="/")
    return HTMLResponse(html)


# Static site last, so explicit routes above take precedence over the catch-all mount.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
