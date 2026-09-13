"""Shared HTML/CSS/JS building blocks used both by build.py (static per-line
pages, for the GitHub Pages mirror) and server/app.py (dynamic per-stop pages
with live data + lazy per-line schedule fetching, on the VPS deployment).
"""
import json
from collections import defaultdict

DAYTYPE_LABEL = {0: "Weekday", 1: "Weekend / Holiday"}


def esc(s):
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


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


def extract_departures_for_stop(schedule_json, stop_code):
    """Given a raw getSchedule response for one line, return the departure-only
    sections (direction label -> times_by_daytype) for a single stop code.

    Mirrors build.py's per-line extraction, but scoped to one stop instead of
    building the whole line's stop list — this is what powers lazy, on-demand
    per-line lookups from a stop-centric page.
    """
    routes_by_direction = defaultdict(list)
    for r in schedule_json.get("routes", []):
        routes_by_direction[r["name"]].append(r)

    sections = []
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

        if stop_code not in ordered_codes:
            continue
        # Use the LAST occurrence: some routes list a stop twice at the tail
        # (a real arrival segment, then a zero-length self-loop segment) —
        # only the final position tells us it's actually the terminus.
        idx = len(ordered_codes) - 1 - ordered_codes[::-1].index(stop_code)
        is_terminus_only = idx == len(ordered_codes) - 1 and len(ordered_codes) > 1
        if is_terminus_only:
            continue  # arrival-only at this stop for this direction — not boardable

        def _name(st):
            return st.get("name_en") or st.get("name") or ""

        dest_name = _name(stop_meta_by_code[ordered_codes[-1]])
        sections.append({
            "label": f"Departs toward {dest_name}",
            "times_by_daytype": format_times(raw_times_by_code[stop_code]),
        })
    return sections


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
      if (!global.__stmSessionId) global.__stmSessionId = String(Date.now()) + Math.random().toString(16).slice(2);
      return global.__stmSessionId;
    }
  }

  function getFavorites() {
    return fetch('/api/favorites?user_id=' + encodeURIComponent(getUserId()))
      .then(function (r) { if (!r.ok) throw new Error('bad status'); return r.json(); });
  }

  function isFavorite(code, list) {
    return list.some(function (f) { return f.code === code; });
  }

  function addFavorite(stop) {
    return fetch('/api/favorites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ user_id: getUserId() }, stop)),
    });
  }

  function removeFavorite(code) {
    var params = new URLSearchParams({ user_id: getUserId(), code: code });
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

# Renders the "Live now" box. Works two ways: if window.STOP.line is set,
# shows only that line's departures (used on the old per-line static pages);
# otherwise shows every line currently reported for this physical stop (used
# on the new stop-centric /stops/{code} pages). Arrivals-only entries (this
# stop is the end of that route) are always excluded either way.
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
          .filter(function (e) { return e.last_stop !== expectedLastStop; })
          .filter(function (e) { return !STOP.line || e.name === STOP.line; });
        if (entries.length === 0) {
          el.innerHTML = '<p class="muted">No live buses currently tracked here' + (STOP.line ? ' for this line' : '') + '.</p>';
          return;
        }
        el.innerHTML = entries.map(function (e) {
          var mins = e.details.map(function (d) { return d.t + ' min'; }).join(', ');
          var lineLabel = STOP.line ? '' : ('<span class="badge">' + e.name + '</span> ');
          return '<p class="live-entry">' + lineLabel + '<strong>' + e.route_name + '</strong>: ' + mins + '</p>';
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
<link rel="manifest" href="{prefix}manifest.webmanifest">
<meta name="theme-color" content="#BD202E">
<link rel="icon" href="{prefix}icons/icon-192.png">
<link rel="apple-touch-icon" href="{prefix}icons/icon-192.png">
<script>
if ('serviceWorker' in navigator) {{
  window.addEventListener('load', function () {{
    navigator.serviceWorker.register('{prefix}service-worker.js').catch(function () {{}});
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
.stop-actions { display:flex; align-items:center; gap:1rem; flex-wrap:wrap; }
.fav-btn { border:1px solid var(--accent); background:transparent; color:var(--accent); border-radius:6px; padding:.4rem .8rem; font-size:.9rem; cursor:pointer; }
.fav-btn.is-fav { background:var(--accent); color:#fff; }
.remove-fav { border:none; background:none; color:var(--muted); text-decoration:underline; font-size:.8rem; cursor:pointer; padding:0 0 0 .5rem; }
.geo-banner { background:var(--now-bg); color:var(--now-fg); padding:.6rem .9rem; border-radius:6px; margin-bottom:.5rem; }
.geo-cancel { border:none; background:none; color:var(--muted); text-decoration:underline; font-size:.85rem; cursor:pointer; margin-bottom:1rem; padding:0; }
.nearest-btn { display:inline-flex; align-items:center; gap:.5rem; border:none; background:var(--accent); color:#fff; border-radius:8px; padding:.7rem 1.1rem; font-size:1rem; cursor:pointer; margin: .5rem 0 1.5rem; }
.nearest-status { color: var(--muted); font-size:.9rem; margin: .25rem 0 1rem; }
.search-box { width:100%; padding:.6rem .8rem; font-size:1rem; border:1px solid var(--border); border-radius:6px; background:var(--bg); color:var(--fg); }
.search-results { list-style:none; padding:0; margin-top:.5rem; }
.search-results li { padding:.35rem 0; border-bottom:1px solid var(--border); }
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
.line-toggle { display:inline-block; margin:.2rem .3rem .2rem 0; padding:.25rem .6rem; border:1px solid var(--border); border-radius:999px; background:transparent; color:var(--fg); font-size:.85rem; cursor:pointer; }
.line-toggle.open { border-color:var(--accent); color:var(--accent); }
.line-schedule { margin-top:.5rem; }
"""


def page(title, body, base=None, extra_head="", static_prefix=None):
    """Render a full HTML page.

    `base` (relative, e.g. "../..") is used by build.py's pre-baked pages,
    which are nested at varying depths. Dynamic server routes pass
    `static_prefix="/"` (absolute) instead since they don't live under docs/.
    """
    prefix = static_prefix if static_prefix is not None else f"{base}/"
    home_href = f"{prefix}index.html"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{prefix}style.css">
<script src="{prefix}favorites.js"></script>
{PWA_HEAD.format(prefix=prefix)}
{extra_head}
</head>
<body>
<header><a class="home-link" href="{home_href}">Sofia Transit Mirror</a></header>
<main>
{body}
</main>
<footer>Data mirrored from <a href="https://www.sofiatraffic.bg/en/public-transport" target="_blank" rel="noopener">sofiatraffic.bg</a> (Urban Mobility Center, Sofia Municipality). Unofficial, static snapshot. Departure times are the official fixed timetable, not live GPS predictions — expect a few minutes' drift from the live "Virtual timetable" on the official site.</footer>
</body>
</html>
"""
