#!/usr/bin/env python3
"""Fetch a Sofia Traffic line's full schedule (routes, stops, departure times)
from the public sofiatraffic.bg backend and save the raw JSON to data/.

Usage: python3 fetch_line.py <line_name> <line_id> <ext_id> <type> <color> <icon>
Example: python3 fetch_line.py 314 155 A200 1 "#BD202E" /images/transport_types/bus.png

The line_id/ext_id/type/color/icon values are the ones the site's own
"Search timetables" autocomplete resolves to for a given line name; they can
be read from the network request the site makes when you pick a line in the
UI (POST /bg/trip/getSchedule).
"""
import json
import sys
import urllib.parse
from pathlib import Path

import requests

BASE = "https://www.sofiatraffic.bg"
DATA_DIR = Path(__file__).parent / "data"


def fetch_schedule(name: str, line_id: int, ext_id: str, line_type: int, color: str, icon: str) -> dict:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    # Establish session + CSRF cookie
    session.get(f"{BASE}/en/public-transport")
    xsrf_cookie = session.cookies.get("XSRF-TOKEN")
    if not xsrf_cookie:
        raise RuntimeError("Did not receive XSRF-TOKEN cookie")
    xsrf_token = urllib.parse.unquote(xsrf_cookie)

    payload = {
        "line_id": line_id,
        "name": name,
        "ext_id": ext_id,
        "type": line_type,
        "color": color,
        "icon": icon,
        "isWeekend": 0,
    }
    resp = session.post(
        f"{BASE}/bg/trip/getSchedule",
        json=payload,
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "X-XSRF-TOKEN": xsrf_token,
            "Referer": f"{BASE}/en/public-transport",
            "Accept": "application/json",
        },
    )
    resp.raise_for_status()
    return resp.json()


def main():
    if len(sys.argv) != 7:
        print(__doc__)
        sys.exit(1)
    name, line_id, ext_id, line_type, color, icon = sys.argv[1:7]
    data = fetch_schedule(name, int(line_id), ext_id, int(line_type), color, icon)
    DATA_DIR.mkdir(exist_ok=True)
    out_path = DATA_DIR / f"line_{name}.json"
    out_path.write_text(json.dumps(data, ensure_ascii=False))
    print(f"Saved {out_path} ({out_path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
