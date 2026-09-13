#!/usr/bin/env python3
"""Fetch the full stop directory and full line directory from sofiatraffic.bg
and save them to data/all_stops.json and data/all_lines.json.

These power "nearest stop" search and let the server look up any line's
line_id/ext_id/type/color/icon on demand, so schedules can be fetched lazily
per-line instead of needing them hardcoded ahead of time.

Usage: python3 fetch_directory.py
"""
import json
import urllib.parse
from pathlib import Path

import requests

BASE = "https://www.sofiatraffic.bg"
DATA_DIR = Path(__file__).parent / "data"


def make_session():
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})
    session.get(f"{BASE}/en/public-transport")
    xsrf_cookie = session.cookies.get("XSRF-TOKEN")
    if not xsrf_cookie:
        raise RuntimeError("Did not receive XSRF-TOKEN cookie")
    return session, urllib.parse.unquote(xsrf_cookie)


def post(session, xsrf_token, path):
    resp = session.post(
        f"{BASE}{path}",
        json={},
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
    session, xsrf = make_session()
    stops = post(session, xsrf, "/bg/trip/getAllStops")
    lines = post(session, xsrf, "/bg/trip/getLines")
    DATA_DIR.mkdir(exist_ok=True)
    (DATA_DIR / "all_stops.json").write_text(json.dumps(stops, ensure_ascii=False))
    (DATA_DIR / "all_lines.json").write_text(json.dumps(lines, ensure_ascii=False))
    print(f"Saved {len(stops)} stops and {len(lines)} lines to {DATA_DIR}")


if __name__ == "__main__":
    main()
