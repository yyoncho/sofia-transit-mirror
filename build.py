#!/usr/bin/env python3
"""Build a static mirror of Sofia Traffic line/stop schedules from the JSON
files in data/ (produced by fetch_line.py) into docs/ (served via GitHub
Pages from the main branch's /docs folder, and also served as the static
asset root — style.css, favorites.js, manifest, icons — by the live FastAPI
server on the VPS, which additionally serves dynamic, stop-centric pages at
/stops/{code} with live data and lazy-fetched schedules for ANY line;
see server/app.py).

Stop pages here are namespaced under their line, since the same physical
stop code can be served by more than one line: docs/lines/<line>/stops/<code>/.
Each line also gets an overview page at docs/lines/<line>/index.html
listing its stops in order, per direction.

Terminus stops only show a "Departs toward ..." section for the direction
that actually starts there — the direction that only ends there (arrivals)
is dropped, since a stop page is a boarding schedule, not an arrivals board.
"""
import json
import shutil
from collections import defaultdict
from pathlib import Path

from templates import (
    CSS,
    CURRENT_HOUR_SCRIPT,
    FAVORITES_JS,
    LIVE_SCRIPT,
    MANIFEST,
    SERVICE_WORKER_JS,
    esc,
    format_times,
    page,
    times_table,
)

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "docs"

HOME_SCRIPT = """
<script>
var WALK_KM = 1.5; // ~15-18 min walk at a normal pace

document.addEventListener('DOMContentLoaded', function () {
  var favSection = document.getElementById('favorites-list');

  function renderFavorites() {
    STM.getFavorites().then(function (favs) {
      if (favs.length === 0) {
        favSection.innerHTML = '<p class="muted">No favorites yet — open a stop page and tap \\u201cSave to favorites\\u201d.</p>';
        return;
      }
      favSection.innerHTML = favs.map(function (f) {
        return '<li><a href="' + f.path + '">' + f.name + '</a> ' +
          '<span class="code">#' + f.code + '</span> ' +
          '<button class="remove-fav" data-code="' + f.code + '">remove</button></li>';
      }).join('');
      favSection.querySelectorAll('.remove-fav').forEach(function (b) {
        b.addEventListener('click', function () {
          STM.removeFavorite(b.dataset.code).then(renderFavorites);
        });
      });
    }).catch(function () {
      favSection.innerHTML = '<p class="muted">Could not load favorites right now (needs the live server, not this static mirror).</p>';
    });
  }
  renderFavorites();
});

document.addEventListener('DOMContentLoaded', function () {
  var btn = document.getElementById('nearest-btn');
  var status = document.getElementById('nearest-status');
  var list = document.getElementById('nearby-list');
  if (!btn) return;

  btn.addEventListener('click', function () {
    list.innerHTML = '';
    if (!navigator.geolocation) { status.textContent = 'Geolocation not available in this browser.'; return; }
    status.textContent = 'Finding your location\\u2026';
    navigator.geolocation.getCurrentPosition(function (pos) {
      var lat = pos.coords.latitude, lon = pos.coords.longitude;
      status.textContent = '';
      Promise.all([
        fetch('/api/stops/nearby?' + new URLSearchParams({ lat: lat, lon: lon, limit: 8 })).then(function (r) { return r.ok ? r.json() : []; }),
        STM.getFavorites().catch(function () { return []; }),
      ]).then(function (both) {
        var nearby = both[0], favs = both[1];

        var favNearby = favs
          .map(function (f) { return Object.assign({}, f, { distance_km: STM.haversineKm(lat, lon, f.lat, f.lon), isFavorite: true }); })
          .filter(function (f) { return f.distance_km <= WALK_KM; });

        var favCodes = favNearby.map(function (f) { return f.code; });
        var rest = nearby
          .filter(function (s) { return favCodes.indexOf(s.code) === -1; })
          .map(function (s) { return Object.assign({}, s, { isFavorite: false }); });

        var picks = favNearby.sort(function (a, b) { return a.distance_km - b.distance_km; })
          .concat(rest.sort(function (a, b) { return a.distance_km - b.distance_km; }))
          .slice(0, 5);

        if (picks.length === 0) {
          status.textContent = 'No stops found nearby.';
          return;
        }
        list.innerHTML = picks.map(function (s) {
          var star = s.isFavorite ? '<span class="badge">\\u2605 favorite</span> ' : '';
          return '<li><a href="/stops/' + s.code + '">' + star + s.name + '</a> ' +
            '<span class="code">#' + s.code + ' \\u00b7 ' + s.distance_km.toFixed(2) + ' km</span></li>';
        }).join('');
      }).catch(function () {
        status.textContent = 'Could not reach the live server (this static mirror has no backend).';
      });
    }, function () {
      status.textContent = 'Location permission denied.';
    }, { timeout: 8000 });
  });
});

document.addEventListener('DOMContentLoaded', function () {
  var input = document.getElementById('search-box');
  var results = document.getElementById('search-results');
  if (!input) return;
  var timer = null;

  input.addEventListener('input', function () {
    clearTimeout(timer);
    var q = input.value.trim();
    if (q.length < 1) { results.innerHTML = ''; return; }
    timer = setTimeout(function () {
      Promise.all([
        fetch('/api/stops/search?q=' + encodeURIComponent(q)).then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; }),
        fetch('/api/lines/search?q=' + encodeURIComponent(q)).then(function (r) { return r.ok ? r.json() : []; }).catch(function () { return []; }),
      ]).then(function (both) {
        var stops = both[0], lines = both[1];
        if (stops.length === 0 && lines.length === 0) {
          results.innerHTML = '<li class="muted">No matches (needs the live server, not this static mirror).</li>';
          return;
        }
        var lineItems = lines.map(function (l) {
          return '<li><a href="/l/' + l.ext_id + '">Line ' + l.name + '</a></li>';
        }).join('');
        var stopItems = stops.map(function (s) {
          return '<li><a href="/stops/' + s.code + '">' + s.name + '</a> <span class="code">#' + s.code + '</span></li>';
        }).join('');
        results.innerHTML = lineItems + stopItems;
      });
    }, 200);
  });
});
</script>
"""


def main():
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir()
    (OUT_DIR / "lines").mkdir()

    # stops[code] = {name_en, name, lat, lon, lines: {line_name: {direction: {...}}}}
    stops = {}
    lines_index = []  # (name, tr_name, color)
    line_stop_order = {}  # line_name -> {direction_name: [codes in order]}

    for jf in sorted(DATA_DIR.glob("line_*.json")):
        d = json.loads(jf.read_text())
        line = d["line"]
        line_name = line["name"]
        lines_index.append((line_name, line.get("tr_name", ""), line.get("tr_color", "#333")))
        line_stop_order[line_name] = {}

        # group routes by direction name (ignoring weekend flag) to merge weekday+weekend times
        routes_by_direction = defaultdict(list)
        for r in d["routes"]:
            routes_by_direction[r["name"]].append(r)

        for direction_name, route_variants in routes_by_direction.items():
            ordered_codes = []
            raw_times_by_code = defaultdict(list)
            stop_meta_by_code = {}
            for r in route_variants:
                segs = sorted(r["segments"], key=lambda s: s["sequence"])
                seq_codes = []
                for seg in segs:
                    st = seg["stop"]
                    code = st["code"]
                    seq_codes.append(code)
                    stop_meta_by_code[code] = st
                    raw_times_by_code[code].extend(st.get("times", []))
                if segs:
                    last = segs[-1]["end_stop"]
                    seq_codes.append(last["code"])
                    stop_meta_by_code[last["code"]] = last
                    raw_times_by_code[last["code"]].extend(last.get("times", []))
                if len(seq_codes) > len(ordered_codes):
                    ordered_codes = seq_codes
            line_stop_order[line_name][direction_name] = ordered_codes

            def _name(st):
                return st.get("name_en") or st.get("name") or ""

            origin_name = _name(stop_meta_by_code[ordered_codes[0]]) if ordered_codes else ""
            dest_name = _name(stop_meta_by_code[ordered_codes[-1]]) if ordered_codes else ""

            for idx, code in enumerate(ordered_codes):
                st = stop_meta_by_code[code]
                stops.setdefault(code, {
                    "name_en": st.get("name_en") or st.get("name"),
                    "name": st.get("name"),
                    "lat": st.get("latitude"),
                    "lon": st.get("longitude"),
                    "lines": {},
                })
                is_terminus_only = idx == len(ordered_codes) - 1 and len(ordered_codes) > 1
                if is_terminus_only:
                    kind = "arrival"
                    label = f"Arrives from {origin_name}"
                else:
                    kind = "departure"
                    label = f"Departs toward {dest_name}"
                stops[code]["lines"].setdefault(line_name, {})
                stops[code]["lines"][line_name][direction_name] = {
                    "times_by_daytype": format_times(raw_times_by_code[code]),
                    "kind": kind,
                    "label": label,
                }

    # --- render stop pages, namespaced under their line ---
    for code, s in stops.items():
        for line_name, directions in s["lines"].items():
            departures = [(n, i) for n, i in directions.items() if i["kind"] == "departure"]
            sections = []
            for direction_name, info in departures:
                sections.append(
                    f'<section class="line-block">'
                    f'<h2>{esc(info["label"])}</h2>'
                    f'{times_table(info["times_by_daytype"])}'
                    f"</section>"
                )
            if not departures:
                sections.append(
                    '<p class="muted">This is the end of the line here — no onward departures '
                    "board at this stop for this line.</p>"
                )
            other_lines = sorted(l for l in s["lines"] if l != line_name)
            other_lines_html = ""
            if other_lines:
                links = ", ".join(
                    f'<a href="../../../{esc(ol)}/stops/{esc(code)}/index.html">{esc(ol)}</a>'
                    for ol in other_lines
                )
                other_lines_html = f'<p class="also-served">Also served by: {links}</p>'
            map_link = ""
            if s["lat"] and s["lon"]:
                map_link = (
                    f'<a href="https://www.openstreetmap.org/?mlat={s["lat"]}&amp;mlon={s["lon"]}#map=18/{s["lat"]}/{s["lon"]}" '
                    f'target="_blank" rel="noopener">View on map</a>'
                )
            path = f"/stops/{code}"  # points at the live server's stop-centric page
            stop_json = json.dumps({
                "code": code,
                "name": s["name_en"],
                "lat": float(s["lat"]) if s["lat"] else None,
                "lon": float(s["lon"]) if s["lon"] else None,
                "path": path,
                "line": line_name,
            })
            stop_script = f"<script>window.STOP = {stop_json};</script>"
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
            body = (
                f'<p class="breadcrumb"><a href="../../index.html">Line {esc(line_name)}</a></p>'
                f'<h1>{esc(s["name_en"])} <span class="code">#{esc(code)}</span></h1>'
                f'<p class="native-name">{esc(s["name"])}</p>'
                f'<p class="stop-actions">{map_link} <button id="fav-btn" class="fav-btn" disabled>☆ Save to favorites</button></p>'
                f"{other_lines_html}"
                '<section class="line-block live-block"><h2>Live now</h2>'
                '<div id="live-times" class="live-times"><p class="muted">Loading live data… (needs the live server)</p></div></section>'
                + "\n".join(sections)
            )
            stop_dir = OUT_DIR / "lines" / line_name / "stops" / code
            stop_dir.mkdir(parents=True, exist_ok=True)
            (stop_dir / "index.html").write_text(
                page(
                    f"Line {line_name} — {s['name_en']} ({code})",
                    body,
                    base="../../../..",
                    extra_head=CURRENT_HOUR_SCRIPT + stop_script + LIVE_SCRIPT + fav_script,
                )
            )

    # --- render line pages ---
    for line_name, directions in line_stop_order.items():
        blocks = []
        for direction_name, codes in directions.items():
            items = "\n".join(
                f'<li><a href="stops/{c}/index.html">{esc(stops[c]["name_en"])}</a> '
                f'<span class="code">#{esc(c)}</span></li>'
                for c in codes
            )
            blocks.append(f"<h2>{esc(direction_name.title())}</h2><ol class=\"stop-list\">{items}</ol>")
        body = f"<h1>Line {esc(line_name)}</h1>" + "\n".join(blocks)
        line_dir = OUT_DIR / "lines" / line_name
        line_dir.mkdir(parents=True, exist_ok=True)
        (line_dir / "index.html").write_text(page(f"Line {line_name}", body, base="../.."))

    # --- home page ---
    line_items = "\n".join(
        f'<li><a href="lines/{esc(name)}/index.html">Line {esc(name)}</a> <span class="badge">{esc(tr)}</span></li>'
        for name, tr, color in lines_index
    )
    stop_rows = []
    for code, s in sorted(stops.items(), key=lambda kv: kv[1]["name_en"] or ""):
        line_links = ", ".join(
            f'<a href="lines/{esc(ln)}/stops/{esc(code)}/index.html">{esc(ln)}</a>' for ln in sorted(s["lines"])
        )
        stop_rows.append(
            f'<li>{esc(s["name_en"])} <span class="code">#{esc(code)}</span> &mdash; {line_links}</li>'
        )
    stop_items = "\n".join(stop_rows)
    home_body = (
        "<h1>Sofia Transit Mirror</h1>"
        "<p>A static, link-friendly mirror of Sofia public transport schedules "
        "sourced from sofiatraffic.bg, plus a live server for real-time boards, "
        "any-line lookups, and favorites.</p>"
        '<button id="nearest-btn" class="nearest-btn">\U0001F4CD Find nearest stop</button>'
        '<p id="nearest-status" class="nearest-status"></p>'
        '<ul id="nearby-list" class="stop-list"></ul>'
        '<h2>Find a line or stop</h2>'
        '<input id="search-box" class="search-box" type="text" placeholder="Line number or stop name…" autocomplete="off">'
        '<ul id="search-results" class="search-results"></ul>'
        '<h2>Your favorites</h2><ul id="favorites-list" class="stop-list"></ul>'
        f"<h2>Pre-built lines</h2><ul class=\"line-list\">{line_items}</ul>"
        f"<h2>Pre-built stops ({len(stops)})</h2><ul class=\"stop-list\">{stop_items}</ul>"
    )
    (OUT_DIR / "index.html").write_text(page("Sofia Transit Mirror", home_body, base=".", extra_head=HOME_SCRIPT))

    (OUT_DIR / "style.css").write_text(CSS)
    (OUT_DIR / "favorites.js").write_text(FAVORITES_JS)
    (OUT_DIR / "manifest.webmanifest").write_text(json.dumps(MANIFEST, indent=2))
    (OUT_DIR / "service-worker.js").write_text(SERVICE_WORKER_JS)
    (OUT_DIR / ".nojekyll").write_text("")

    icons_src = ROOT / "assets" / "icons"
    if icons_src.exists():
        icons_dst = OUT_DIR / "icons"
        icons_dst.mkdir(exist_ok=True)
        for f in icons_src.glob("*.png"):
            shutil.copy(f, icons_dst / f.name)

    stop_page_count = sum(len(s["lines"]) for s in stops.values())
    print(f"Built {stop_page_count} stop pages ({len(stops)} unique stops) and {len(line_stop_order)} line pages into {OUT_DIR}")


if __name__ == "__main__":
    main()
