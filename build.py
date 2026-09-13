#!/usr/bin/env python3
"""Build a static mirror of Sofia Traffic line/stop schedules from the JSON
files in data/ (produced by fetch_line.py) into docs/ (served via GitHub
Pages from the main branch's /docs folder).

Stop pages are namespaced under their line, since the same physical stop
code can be served by more than one line: docs/lines/<line>/stops/<code>/.
Each line also gets an overview page at docs/lines/<line>/index.html
listing its stops in order, per direction.

Terminus stops show two kinds of sections, matching how the official site's
own live "Virtual timetable" treats them: a "Departs toward ..." section for
the route that starts there, and an "Arrives from ..." section for the route
that ends there — these are genuinely different lists, not duplicates.
"""
import json
import shutil
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "docs"

DAYTYPE_LABEL = {0: "Weekday", 1: "Weekend / Holiday"}

# Highlights the current hour's row client-side, in Sofia local time
# (independent of the visitor's own timezone).
CURRENT_HOUR_SCRIPT = """
<script>
document.addEventListener('DOMContentLoaded', function () {
  var hour = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', hourCycle: 'h23', timeZone: 'Europe/Sofia' }).format(new Date());
  document.querySelectorAll('tr[data-hour="' + hour + '"]').forEach(function (row) {
    row.classList.add('current-hour');
    var th = row.querySelector('th');
    if (th) th.insertAdjacentHTML('beforeend', ' <span class="now-badge">now</span>');
  });
});
</script>
"""

FAVORITES_JS = """
(function (global) {
  var USER_ID_KEY = 'stm_user_id';

  function getUserId() {
    try {
      var id = localStorage.getItem(USER_ID_KEY);
      if (!id) {
        id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2));
        localStorage.setItem(USER_ID_KEY, id);
      }
      return id;
    } catch (e) {
      // storage unavailable (private mode etc.) — fall back to a per-tab id
      if (!global.__stmSessionId) global.__stmSessionId = String(Date.now()) + Math.random().toString(16).slice(2);
      return global.__stmSessionId;
    }
  }

  function getFavorites() {
    return fetch('/api/favorites?user_id=' + encodeURIComponent(getUserId()))
      .then(function (r) { if (!r.ok) throw new Error('bad status'); return r.json(); });
  }

  function isFavorite(line, code, list) {
    return list.some(function (f) { return f.line === line && f.code === code; });
  }

  function addFavorite(stop) {
    return fetch('/api/favorites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ user_id: getUserId() }, stop)),
    });
  }

  function removeFavorite(line, code) {
    var params = new URLSearchParams({ user_id: getUserId(), line: line, code: code });
    return fetch('/api/favorites?' + params.toString(), { method: 'DELETE' });
  }

  function haversineKm(lat1, lon1, lat2, lon2) {
    var R = 6371;
    var dLat = (lat2 - lat1) * Math.PI / 180;
    var dLon = (lon2 - lon1) * Math.PI / 180;
    var a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
      Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
      Math.sin(dLon / 2) * Math.sin(dLon / 2);
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  global.STM = {
    getUserId: getUserId,
    getFavorites: getFavorites,
    isFavorite: isFavorite,
    addFavorite: addFavorite,
    removeFavorite: removeFavorite,
    haversineKm: haversineKm,
  };
})(window);
"""

HOME_SCRIPT = """
<script>
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
          '<span class="code">Line ' + f.line + ' &middot; #' + f.code + '</span> ' +
          '<button class="remove-fav" data-line="' + f.line + '" data-code="' + f.code + '">remove</button></li>';
      }).join('');
      favSection.querySelectorAll('.remove-fav').forEach(function (b) {
        b.addEventListener('click', function () {
          STM.removeFavorite(b.dataset.line, b.dataset.code).then(renderFavorites);
        });
      });
      maybeRedirect(favs);
    }).catch(function () {
      favSection.innerHTML = '<p class="muted">Could not load favorites right now.</p>';
    });
  }
  renderFavorites();

  function maybeRedirect(favs) {
    if (favs.length === 0 || !navigator.geolocation) return;

    var banner = document.getElementById('geo-banner');
    var cancelBtn = document.getElementById('geo-cancel');
    var cancelled = false;
    banner.hidden = false;
    cancelBtn.hidden = false;
    banner.textContent = 'Finding your nearest favorite stop\\u2026';
    cancelBtn.addEventListener('click', function () {
      cancelled = true;
      banner.hidden = true;
      cancelBtn.hidden = true;
    });

    navigator.geolocation.getCurrentPosition(function (pos) {
      if (cancelled) return;
      var lat = pos.coords.latitude, lon = pos.coords.longitude;
      var nearest = favs.map(function (f) {
        return { f: f, d: STM.haversineKm(lat, lon, f.lat, f.lon) };
      }).sort(function (a, b) { return a.d - b.d; })[0];
      banner.innerHTML = 'Nearest favorite: <strong>' + nearest.f.name + '</strong> (' + nearest.d.toFixed(1) + ' km) \\u2014 opening\\u2026';
      setTimeout(function () { if (!cancelled) location.href = nearest.f.path; }, 1800);
    }, function () {
      banner.hidden = true;
      cancelBtn.hidden = true;
    }, { timeout: 8000 });
  }
});
</script>
"""

LIVE_SCRIPT = """
<script>
document.addEventListener('DOMContentLoaded', function () {
  var el = document.getElementById('live-times');
  if (!el) return;
  var expectedLastStop = 'A' + STOP.code;

  function load() {
    fetch('/api/virtual/' + encodeURIComponent(STOP.code))
      .then(function (r) { if (!r.ok) throw new Error('bad status'); return r.json(); })
      .then(function (data) {
        var entries = Object.keys(data).map(function (k) { return data[k]; })
          .filter(function (e) { return e.name === STOP.line && e.last_stop !== expectedLastStop; });
        if (entries.length === 0) {
          el.innerHTML = '<p class="muted">No live buses currently tracked for this line here.</p>';
          return;
        }
        el.innerHTML = entries.map(function (e) {
          var mins = e.details.map(function (d) { return d.t + ' min'; }).join(', ');
          return '<p class="live-entry"><strong>' + e.route_name + '</strong>: ' + mins + '</p>';
        }).join('');
      })
      .catch(function () {
        el.innerHTML = '<p class="muted">Live data unavailable right now.</p>';
      });
  }
  load();
  setInterval(load, 20000);
});
</script>
"""

PWA_HEAD = """
<link rel="manifest" href="{base}/manifest.webmanifest">
<meta name="theme-color" content="#BD202E">
<link rel="icon" href="{base}/icons/icon-192.png">
<link rel="apple-touch-icon" href="{base}/icons/icon-192.png">
<script>
if ('serviceWorker' in navigator) {{
  window.addEventListener('load', function () {{
    navigator.serviceWorker.register('{base}/service-worker.js').catch(function () {{}});
  }});
}}
</script>
"""

MANIFEST = {
    "name": "Sofia Transit Mirror",
    "short_name": "SofiaTransit",
    "description": "Static + live Sofia public transport schedules",
    "start_url": "./index.html",
    "scope": "./",
    "display": "standalone",
    "background_color": "#ffffff",
    "theme_color": "#BD202E",
    "icons": [
        {"src": "icons/icon-192.png", "sizes": "192x192", "type": "image/png"},
        {"src": "icons/icon-512.png", "sizes": "512x512", "type": "image/png"},
    ],
}

SERVICE_WORKER_JS = """
const CACHE = 'stm-v1';
self.addEventListener('install', (e) => { self.skipWaiting(); });
self.addEventListener('activate', (e) => { self.clients.claim(); });
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (url.pathname.startsWith('/api/')) return; // never cache live/favorites data
  event.respondWith(
    caches.open(CACHE).then(function (cache) {
      return cache.match(event.request).then(function (cached) {
        const fetchPromise = fetch(event.request).then(function (res) {
          if (res.ok) cache.put(event.request, res.clone());
          return res;
        }).catch(function () { return cached; });
        return cached || fetchPromise;
      });
    })
  );
});
"""


def esc(s):
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def page(title, body, base, extra_head=""):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{base}/style.css">
<script src="{base}/favorites.js"></script>
{PWA_HEAD.format(base=base)}
{extra_head}
</head>
<body>
<header><a class="home-link" href="{base}/index.html">Sofia Transit Mirror</a></header>
<main>
{body}
</main>
<footer>Data mirrored from <a href="https://www.sofiatraffic.bg/en/public-transport" target="_blank" rel="noopener">sofiatraffic.bg</a> (Urban Mobility Center, Sofia Municipality). Unofficial, static snapshot. Departure times are the official fixed timetable, not live GPS predictions — expect a few minutes' drift from the live "Virtual timetable" on the official site.</footer>
</body>
</html>
"""


def format_times(times):
    """Group a stop's raw time entries by weekend flag, sorted, deduped."""
    by_daytype = defaultdict(set)
    for t in times:
        by_daytype[t["weekend"]].add(t["time"][:5])
    return {k: sorted(v) for k, v in by_daytype.items()}


def times_table(times_by_daytype):
    parts = []
    for wk in (0, 1):
        times = times_by_daytype.get(wk)
        if not times:
            continue
        hours = defaultdict(list)
        for t in times:
            h, m = t.split(":")
            hours[h].append(m)
        parts.append(f'<h3>{DAYTYPE_LABEL[wk]}</h3>')
        parts.append('<table class="timetable"><tbody>')
        for h in sorted(hours):
            times_str = ", ".join(f"{h}:{m}" for m in sorted(hours[h]))
            parts.append(f'<tr data-hour="{h}"><th>{h}:00</th><td>{times_str}</td></tr>')
        parts.append("</tbody></table>")
    if not parts:
        parts.append("<p><em>No scheduled times found.</em></p>")
    return "\n".join(parts)


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
            # departures first, then arrivals, so the useful "board here" list leads
            # Only show sections where you can actually board here — a stop
            # page is a boarding schedule, not an arrivals board.
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
            path = f"lines/{line_name}/stops/{code}/index.html"
            stop_json = json.dumps({
                "line": line_name,
                "code": code,
                "name": s["name_en"],
                "lat": float(s["lat"]) if s["lat"] else None,
                "lon": float(s["lon"]) if s["lon"] else None,
                "path": path,
            })
            fav_script = f"""
<script>
window.STOP = {stop_json};
document.addEventListener('DOMContentLoaded', function () {{
  var btn = document.getElementById('fav-btn');
  function render() {{
    STM.getFavorites().then(function (favs) {{
      var fav = STM.isFavorite(STOP.line, STOP.code, favs);
      btn.textContent = fav ? '\\u2605 Remove from favorites' : '\\u2606 Save to favorites';
      btn.classList.toggle('is-fav', fav);
      btn.disabled = false;
      btn.onclick = function () {{
        btn.disabled = true;
        var action = fav ? STM.removeFavorite(STOP.line, STOP.code) : STM.addFavorite(STOP);
        action.then(render);
      }};
    }}).catch(function () {{ btn.textContent = 'Favorites unavailable'; btn.disabled = true; }});
  }}
  render();
}});
</script>
"""
            body = (
                f'<p class="breadcrumb"><a href="../../index.html">Line {esc(line_name)}</a></p>'
                f'<h1>{esc(s["name_en"])} <span class="code">#{esc(code)}</span></h1>'
                f'<p class="native-name">{esc(s["name"])}</p>'
                f'<p class="stop-actions">{map_link} <button id="fav-btn" class="fav-btn" disabled>☆ Save to favorites</button></p>'
                f"{other_lines_html}"
                '<section class="line-block live-block"><h2>Live now</h2>'
                '<div id="live-times" class="live-times"><p class="muted">Loading live data…</p></div></section>'
                + "\n".join(sections)
            )
            stop_dir = OUT_DIR / "lines" / line_name / "stops" / code
            stop_dir.mkdir(parents=True, exist_ok=True)
            (stop_dir / "index.html").write_text(
                page(
                    f"Line {line_name} — {s['name_en']} ({code})",
                    body,
                    base="../../../..",
                    extra_head=CURRENT_HOUR_SCRIPT + LIVE_SCRIPT + fav_script,
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
        "sourced from sofiatraffic.bg. Pick a line below, then a stop — each "
        "stop's URL includes its line, since the same physical stop can be "
        "served by several lines with different timetables.</p>"
        '<div id="geo-banner" class="geo-banner" hidden></div>'
        '<button id="geo-cancel" class="geo-cancel" hidden>Stay on this page</button>'
        '<h2>Your favorites</h2><ul id="favorites-list" class="stop-list"></ul>'
        f"<h2>Lines</h2><ul class=\"line-list\">{line_items}</ul>"
        f"<h2>Stops ({len(stops)})</h2><ul class=\"stop-list\">{stop_items}</ul>"
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


CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#666; --accent:#BD202E; --border:#ddd; --now-bg: #fff3cd; --now-fg:#7a5b00; --arrival-bg: rgba(128,128,128,.08); }
@media (prefers-color-scheme: dark) { :root { --bg:#14161a; --fg:#eee; --muted:#999; --border:#333; --now-bg:#4a3b00; --now-fg:#ffe083; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font-family:-apple-system,Segoe UI,Roboto,sans-serif; line-height:1.5; }
header { padding: 1rem 1.5rem; border-bottom:1px solid var(--border); }
.home-link { font-weight:700; text-decoration:none; color:var(--accent); }
main { max-width: 760px; margin: 0 auto; padding: 1.5rem; }
footer { max-width: 760px; margin: 2rem auto; padding: 1rem 1.5rem; color:var(--muted); font-size:.85rem; border-top:1px solid var(--border); }
a { color: var(--accent); }
h1 { margin-top:0; }
.code { color: var(--muted); font-weight:400; font-size:.8em; }
.native-name { color: var(--muted); margin-top:-0.75rem; }
.breadcrumb { margin-bottom: .25rem; font-size: .9rem; }
.also-served { color: var(--muted); font-size: .9rem; }
.muted { color: var(--muted); }
ul.stop-list, ol.stop-list, ul.line-list { list-style:none; padding:0; }
ul.stop-list li, ol.stop-list li, ul.line-list li { padding:.35rem 0; border-bottom:1px solid var(--border); }
.badge { background:var(--accent); color:#fff; border-radius:4px; padding:.1rem .4rem; font-size:.75rem; }
.stop-actions { display:flex; align-items:center; gap:1rem; }
.fav-btn { border:1px solid var(--accent); background:transparent; color:var(--accent); border-radius:6px; padding:.4rem .8rem; font-size:.9rem; cursor:pointer; }
.fav-btn.is-fav { background:var(--accent); color:#fff; }
.remove-fav { border:none; background:none; color:var(--muted); text-decoration:underline; font-size:.8rem; cursor:pointer; padding:0 0 0 .5rem; }
.geo-banner { background:var(--now-bg); color:var(--now-fg); padding:.6rem .9rem; border-radius:6px; margin-bottom:.5rem; }
.geo-cancel { border:none; background:none; color:var(--muted); text-decoration:underline; font-size:.85rem; cursor:pointer; margin-bottom:1rem; padding:0; }
.line-block { margin: 1.5rem 0; padding-top: 1rem; border-top: 1px solid var(--border); }
.line-block h2 { display:flex; align-items:center; gap:.5rem; font-size:1.1rem; }
.kind-badge { font-size:.65rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em; border-radius:3px; padding:.15rem .4rem; }
.kind-badge.departure { background:var(--accent); color:#fff; }
.kind-badge.arrival { background:var(--arrival-bg); color:var(--muted); }
table.timetable { border-collapse:collapse; width:100%; margin:.5rem 0 1rem; font-size: 1.05rem; }
table.timetable tr { border-bottom: 1px solid var(--border); }
table.timetable th { text-align:left; padding:.4rem .75rem .4rem 0; vertical-align:top; color:var(--muted); font-weight:600; white-space:nowrap; width:5rem; }
table.timetable td { padding:.4rem 0; font-variant-numeric: tabular-nums; letter-spacing: .02em; }
tr.current-hour { background: var(--now-bg); }
tr.current-hour th { color: var(--now-fg); }
.now-badge { display:inline-block; background:var(--accent); color:#fff; font-size:.65rem; font-weight:700; text-transform:uppercase; letter-spacing:.03em; border-radius:3px; padding:.1rem .35rem; margin-left:.4rem; vertical-align:middle; }
.live-block { background: rgba(189,32,46,.06); border-radius:8px; padding:1rem; border-top:none; }
.live-entry { margin:.3rem 0; }
"""

if __name__ == "__main__":
    main()
