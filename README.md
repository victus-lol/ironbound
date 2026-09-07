# IRONBOUND

**Train. Level up. Dominate.** — now as an offline-first static app (v3.0).

IRONBOUND turns real gym data — lifts, runs, field tests, body measurements —
into RPG stats (**STR / END / AGI / VIT / POW / FLX**), ranks you against
published athletic standards (Average → Healthy → Enthusiast → Pro → Elite),
and tells you exactly what to train next.

No accounts, no server, no build step. Your data lives in your browser
(localStorage + IndexedDB mirror) and can be exported any time.

## Run it

Just open `index.html` — or serve the folder (enables PWA install):

```bash
python -m http.server 8000
# → http://localhost:8000
```

Install it as an app via your browser's *Install / Add to Home Screen*.

## Features

- **RPG stat engine** — Epley 1RM, Cooper VO₂max, US-Navy body fat → six live
  stat cards with tiers, trends and points-to-next-rank
- **Next-tier coach** — weakest-stat detection prescribes the fastest rank gain
- **Streaks & badges** — daily fire + weekly streak with monthly freeze, 13-week
  heatmap, volume series, PR detection, 16 badges, monthly challenges
- **Logging** — strength / cardio / body / field tests, back-dating, edit & delete,
  offline queue, validated JSON import, JSON/CSV export
- **Analytics** — 30-day strength, VO₂max, body-fat and bodyweight charts, radar, PR timeline
- **Planning** — goal-based Mon–Sun timetable, allergy-aware food chart with
  Mifflin-St Jeor macros, free-text rules
- **Outdoors** — weather-smart runs (Open-Meteo, no key), nearby spots map
  (OpenStreetMap + Overpass), OSRM circular route planner with GPX export
- **Sharing** — one-click HUD stat-card PNG, copy-stats text

See `docs/AUDIT.md` for the full engineering log.

## Project history

- Up to v2.x this repo hosted a Flask server (`app.py`, Jinja templates, SQLite).
  That generation is preserved on the **`archive/flask`** branch.
- `main` is now the static app (previously developed as “GymRat”, rebranded back
  to IRONBOUND). Storage keys kept their old names so existing user data survives.

## Deploy

Pushes to `main` deploy to GitHub Pages via `.github/workflows/pages.yml`.
First time only: repo Settings → Pages → Source → **GitHub Actions**.

## Roadmap

- Accounts + sync (Next.js generation, in development)
- OAuth (Google/GitHub), live workout timers, leaderboards, Strava/Health sync
