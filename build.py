#!/usr/bin/env python3
"""Build a static mirror of Sofia Traffic line/stop schedules from the JSON
files in data/ (produced by fetch_line.py) into docs/ (served via GitHub
Pages from the main branch's /docs folder).

Each stop gets its own page at docs/stops/<code>/index.html so it can be
linked to directly. Each line gets an overview page at
docs/lines/<name>/index.html listing its stops in order, per direction.
"""
import json
import shutil
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
OUT_DIR = ROOT / "docs"

DAYTYPE_LABEL = {0: "Weekday", 1: "Weekend / Holiday"}


def esc(s):
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def page(title, body, base=".."):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{base}/style.css">
</head>
<body>
<header><a class="home-link" href="{base}/index.html">Sofia Transit Mirror</a></header>
<main>
{body}
</main>
<footer>Data mirrored from <a href="https://www.sofiatraffic.bg/en/public-transport" target="_blank" rel="noopener">sofiatraffic.bg</a> (Urban Mobility Center, Sofia Municipality). Unofficial, static snapshot.</footer>
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
            mins = " ".join(f'<span class="min">{m}</span>' for m in sorted(hours[h]))
            parts.append(f'<tr><th>{h}</th><td>{mins}</td></tr>')
        parts.append("</tbody></table>")
    if not parts:
        parts.append("<p><em>No scheduled times found.</em></p>")
    return "\n".join(parts)


def main():
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir()
    (OUT_DIR / "stops").mkdir()
    (OUT_DIR / "lines").mkdir()

    # stops[code] = {name_en, name, lat, lon, lines: {line_name: {direction: {"to": str, "times_by_daytype": {...}, "sequence": int}}}}
    stops = {}
    lines_index = []  # (name, tr_name, color)
    line_stop_order = {}  # line_name -> {route_name: [codes in order]}

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

            for code in ordered_codes:
                st = stop_meta_by_code[code]
                stops.setdefault(code, {
                    "name_en": st.get("name_en") or st.get("name"),
                    "name": st.get("name"),
                    "lat": st.get("latitude"),
                    "lon": st.get("longitude"),
                    "lines": {},
                })
                stops[code]["lines"].setdefault(line_name, {})
                stops[code]["lines"][line_name][direction_name] = {
                    "times_by_daytype": format_times(raw_times_by_code[code]),
                }

    # --- render stop pages ---
    for code, s in stops.items():
        sections = []
        for line_name, directions in sorted(s["lines"].items()):
            for direction_name, info in directions.items():
                sections.append(
                    f'<section class="line-block">'
                    f'<h2><a href="../../lines/{line_name}/index.html">Line {esc(line_name)}</a> '
                    f'&rarr; {esc(direction_name.title())}</h2>'
                    f'{times_table(info["times_by_daytype"])}'
                    f"</section>"
                )
        map_link = ""
        if s["lat"] and s["lon"]:
            map_link = (
                f'<p><a href="https://www.openstreetmap.org/?mlat={s["lat"]}&amp;mlon={s["lon"]}#map=18/{s["lat"]}/{s["lon"]}" '
                f'target="_blank" rel="noopener">View on map</a></p>'
            )
        body = (
            f'<h1>{esc(s["name_en"])} <span class="code">#{esc(code)}</span></h1>'
            f'<p class="native-name">{esc(s["name"])}</p>'
            f"{map_link}"
            + "\n".join(sections)
        )
        stop_dir = OUT_DIR / "stops" / code
        stop_dir.mkdir(parents=True, exist_ok=True)
        (stop_dir / "index.html").write_text(page(f"Stop {s['name_en']} ({code})", body))

    # --- render line pages ---
    for line_name, directions in line_stop_order.items():
        blocks = []
        for direction_name, codes in directions.items():
            items = "\n".join(
                f'<li><a href="../../stops/{c}/index.html">{esc(stops[c]["name_en"])}</a> '
                f'<span class="code">#{esc(c)}</span></li>'
                for c in codes
            )
            blocks.append(f"<h2>{esc(direction_name.title())}</h2><ol class=\"stop-list\">{items}</ol>")
        body = f"<h1>Line {esc(line_name)}</h1>" + "\n".join(blocks)
        line_dir = OUT_DIR / "lines" / line_name
        line_dir.mkdir(parents=True, exist_ok=True)
        (line_dir / "index.html").write_text(page(f"Line {line_name}", body))

    # --- home page ---
    line_items = "\n".join(
        f'<li><a href="lines/{esc(name)}/index.html">Line {esc(name)}</a> <span class="badge">{esc(tr)}</span></li>'
        for name, tr, color in lines_index
    )
    stop_items = "\n".join(
        f'<li><a href="stops/{esc(code)}/index.html">{esc(s["name_en"])}</a> <span class="code">#{esc(code)}</span></li>'
        for code, s in sorted(stops.items(), key=lambda kv: kv[1]["name_en"] or "")
    )
    home_body = (
        "<h1>Sofia Transit Mirror</h1>"
        "<p>A static, link-friendly mirror of Sofia public transport schedules "
        "sourced from sofiatraffic.bg. Pick a line or a stop below — each stop "
        "has a permanent URL you can bookmark or share.</p>"
        f"<h2>Lines</h2><ul class=\"line-list\">{line_items}</ul>"
        f"<h2>Stops ({len(stops)})</h2><ul class=\"stop-list\">{stop_items}</ul>"
    )
    (OUT_DIR / "index.html").write_text(page("Sofia Transit Mirror", home_body, base="."))

    (OUT_DIR / "style.css").write_text(CSS)
    (OUT_DIR / ".nojekyll").write_text("")

    print(f"Built {len(stops)} stop pages and {len(line_stop_order)} line pages into {OUT_DIR}")


CSS = """
:root { color-scheme: light dark; --bg:#fff; --fg:#1a1a1a; --muted:#666; --accent:#BD202E; --border:#ddd; }
@media (prefers-color-scheme: dark) { :root { --bg:#14161a; --fg:#eee; --muted:#999; --border:#333; } }
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
ul.stop-list, ol.stop-list, ul.line-list { list-style:none; padding:0; }
ul.stop-list li, ol.stop-list li, ul.line-list li { padding:.35rem 0; border-bottom:1px solid var(--border); }
.badge { background:var(--accent); color:#fff; border-radius:4px; padding:.1rem .4rem; font-size:.75rem; }
.line-block { margin: 1.5rem 0; padding-top: 1rem; border-top: 1px solid var(--border); }
table.timetable { border-collapse:collapse; width:100%; margin:.5rem 0 1rem; }
table.timetable th { text-align:right; padding:.25rem .75rem .25rem 0; vertical-align:top; color:var(--muted); width:2.5rem; }
table.timetable td { padding:.25rem 0; }
.min { display:inline-block; margin:0 .4rem .2rem 0; padding:.1rem .35rem; background:rgba(128,128,128,.12); border-radius:3px; font-variant-numeric: tabular-nums; }
"""

if __name__ == "__main__":
    main()
