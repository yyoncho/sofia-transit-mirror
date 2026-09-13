#!/usr/bin/env python3
"""Sofia Transit Mirror server.

Serves the static site built by build.py (docs/) and two small JSON APIs:

- /api/favorites   (GET/POST/DELETE) — a single global favorites list,
  persisted in SQLite. No auth: this is a personal, single-user app.
- /api/virtual/{stop_code} (GET) — proxies sofiatraffic.bg's live
  "Virtual timetable" endpoint server-side (it needs a session + CSRF
  cookie dance that a browser can't do cross-origin), with a short cache
  so a page full of viewers doesn't hammer the upstream site.
"""
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

BASE = "https://www.sofiatraffic.bg"
DB_PATH = Path(__file__).parent / "favorites.db"
STATIC_DIR = Path(__file__).parent.parent / "docs"
CACHE_TTL = 15  # seconds; keeps live-widget polling from hammering upstream
SESSION_TTL = 1800  # refresh the upstream session/CSRF cookie every 30 min

app = FastAPI()


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
            line TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            lat REAL,
            lon REAL,
            path TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (user_id, line, code)
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
        "SELECT line, code, name, lat, lon, path FROM favorites WHERE user_id=? ORDER BY created_at",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/favorites")
async def add_favorite(request: Request):
    data = await request.json()
    _require_user_id(data.get("user_id"))
    for field in ("line", "code", "name", "path"):
        if not data.get(field):
            raise HTTPException(status_code=400, detail=f"missing {field}")
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO favorites (user_id, line, code, name, lat, lon, path, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (
            data["user_id"],
            data["line"],
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
def remove_favorite(user_id: str, line: str, code: str):
    _require_user_id(user_id)
    conn = get_db()
    conn.execute("DELETE FROM favorites WHERE user_id=? AND line=? AND code=?", (user_id, line, code))
    conn.commit()
    conn.close()
    return {"ok": True}


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


@app.get("/api/virtual/{stop_code}")
def virtual_table(stop_code: str, type: int = 1):
    cache_key = (stop_code, type)
    now = time.time()
    cached = _vt_cache.get(cache_key)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    for attempt in (0, 1):
        session = get_session(force=attempt == 1)
        xsrf_cookie = session.cookies.get("XSRF-TOKEN")
        xsrf = urllib.parse.unquote(xsrf_cookie or "")
        resp = session.post(
            f"{BASE}/bg/trip/getVirtualTable",
            json={"stop": stop_code, "type": type},
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
                data = resp.json()
            except ValueError:
                continue
            _vt_cache[cache_key] = (now, data)
            return data
    raise HTTPException(status_code=502, detail="upstream unavailable")


# Static site last, so /api/* above takes precedence over the catch-all mount.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
