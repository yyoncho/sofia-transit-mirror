# Sofia Transit Mirror

A static, link-friendly mirror of Sofia public transport schedules, sourced
from [sofiatraffic.bg](https://www.sofiatraffic.bg/en/public-transport).

Unlike the official site (a map-driven SPA), every stop here has a permanent
URL you can open, bookmark, or share directly — e.g. `/stops/1512/` for stop
1512 (Selo Bistritsa).

## How it works

- `fetch_line.py` calls the same backend endpoint the official site's
  "Search timetables" feature uses (`POST /bg/trip/getSchedule`) to pull the
  full route/stop/departure-time data for one line, and saves it to
  `data/line_<name>.json`.
- `build.py` reads every `data/line_*.json` file and generates a fully static
  site into `docs/`: one page per line, one page per stop (grouped by line +
  direction + weekday/weekend), and a home page listing everything.
- `docs/` is served by GitHub Pages directly from the `main` branch.

## Adding more lines

Find a line's `line_id`/`ext_id`/`type`/`color`/`icon` by opening the
official site's "Timetables" tab, picking a line, and inspecting the
`POST /bg/trip/getSchedule` request body in your browser's network tab.
Then:

```bash
python3 fetch_line.py <name> <line_id> <ext_id> <type> <color> <icon>
python3 build.py
```

## Disclaimer

Schedule data belongs to Urban Mobility Center / Sofia Municipality. This is
an unofficial, static snapshot for convenience — always verify against the
official site or in-vehicle displays for anything time-critical.
