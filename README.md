# IRONBOUND

Train. Level up. Dominate.

A fitness stat tracker that turns real gym data — lifts, runs, field tests, and body
measurements — into RPG-style stats (**STR / END / AGI / VIT / POW / FLX**), ranks you
against published fitness standards (Average → Healthy → Enthusiast → Pro), and shows
exactly what you need to reach the next tier.

## Quick start

```bash
pip install -r requirements.txt
python app.py
```

Open http://localhost:5000, create an account, and log your first set.

The **dashboard is a clean game HUD** — your level, rank, and six stat cards
(STR / END / AGI / VIT / POW / FLX) at a glance, each showing its score, tier,
trend, and how many points you need for the next rank. A **Training rhythm**
heatmap shows your last 13 weeks of activity, a persistent player bar (level +
rank + XP) sits in the top bar, and a "train your weakest stat" card always
points to the single best thing to log next.

Everything you record lives in **My Logs** — edit or delete any entry you got
wrong. The **+ Log** button takes you to a hub where each activity (strength,
cardio, body, field tests) is a big card showing exactly which stats it feeds.

### Server settings (environment variables)

The app stays **private by default** — it only listens on your own machine
(`127.0.0.1`), which is the safe choice.

| Variable                | Default      | Meaning                                                                  |
| ----------------------- | ------------ | ------------------------------------------------------------------------ |
| `IRONBOUND_HOST`        | `127.0.0.1`  | Set `0.0.0.0` to accept connections on your home network                 |
| `IRONBOUND_PORT`        | `5000`       | Port to listen on                                                        |
| `IRONBOUND_DEBUG`       | `0`          | `1` turns on the debugger — **local development only**                   |
| `IRONBOUND_DB`          | `ironbound.db`| Path to the SQLite database file                                          |
| `IRONBOUND_SECRET_KEY`  | random       | Secret key for signing sessions. Set a fixed one so logins survive restarts |
| `IRONBOUND_COOKIE_SECURE`| `0`         | `1` marks the session cookie `Secure` (use when serving over HTTPS)       |
| `IRONBOUND_SERVER`      | `flask`      | `waitress` runs a production-grade server (multi-threaded, stable)        |

### Production & security

The app is hardened out of the box:

- **Passwords** are hashed with PBKDF2 (`werkzeug`); legacy SHA-256 accounts are
  auto-upgraded on next successful login.
- **CSRF protection** on every form — logins, sign-ups, log/edit/delete, import,
  and settings. Logout is a POST action.
- **Login rate-limiting** (8 failures / 5 minutes per IP+user) slows down
  password guessing without blocking legitimate users.
- **Security headers** on every response: CSP (self-hosted scripts only),
  `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`,
  and `Cache-Control: no-store` for private pages.
- **Input validation** everywhere: bad numbers return a friendly 400, not a crash.
- **SQLite hardening**: foreign keys enabled and WAL mode for safer concurrent access.

To serve over the internet from your own machine, prefer the tunnel option below,
or set `IRONBOUND_SERVER=waitress`, a strong `IRONBOUND_SECRET_KEY`, and
`IRONBOUND_COOKIE_SECURE=1` if you terminate HTTPS at a reverse proxy.

Examples (PowerShell):

```powershell
$env:IRONBOUND_HOST = "0.0.0.0"   # allow your phone / other PCs on the same Wi-Fi
python app.py
```

> ⚠️ Never run with `IRONBOUND_DEBUG=1` while exposed to the internet — the
> Werkzeug debugger lets anyone run code on your PC.

## Share it with people outside your network

Two easy free ways to get a public link (your PC stays the server, no cloud
account needed for the first option):

### Option 1 — Cloudflare Quick Tunnel (recommended, no sign-up)

1. Download `cloudflared` from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
2. Start the app (default settings are fine):
   ```bash
   python app.py
   ```
3. In a second terminal, run:
   ```bash
   cloudflared tunnel --url http://localhost:5000
   ```
4. It prints a `https://<random>.trycloudflare.com` URL — share that with anyone.
   It stays up as long as both your app and cloudflared are running.

### Option 2 — ngrok

1. Install ngrok from https://ngrok.com (free account + `ngrok config add-authtoken ...`)
2. `python app.py`
3. `ngrok http 5000` → it shows a `https://xxxx.ngrok-free.app` URL.

Notes for both:
- The link changes every time you restart the tunnel (free tier).
- Anyone with the link can reach the app, so keep passwords strong.
- Your data still lives only in your `ironbound.db` file — the tunnel is just a
  pipe; nothing is stored by the tunnel provider.

### Want an always-on public site instead?

Deploy to a free host such as Render, Railway, or Fly.io. The app is a normal
Flask app, so you just point the host at `app.py`, set `IRONBOUND_DB` to a
persistent volume path, and it serves HTTPS automatically.

## Where your data lives & how to back it up

All data is stored in a single SQLite file, `ironbound.db`, right in the project
folder. Accounts, bodyweight, and every log are rows in that one file.

- **Back up:** copy `ironbound.db` somewhere safe (or zip the whole folder).
- **Export:** **My Logs → ⬇️ Export (JSON)** downloads a complete backup that
  can be re-imported into any account. Prefer a spreadsheet? **📄 Export (CSV)**
  gives you every entry as a plain table for Excel / Google Sheets.
- **Restore / import:** on a fresh account, click **My Logs → 📥 Import** and
  upload that JSON file — all your entries are recreated (and you can keep your
  old account as an archive).
- **Reset:** delete `ironbound.db` while the app is stopped for a fresh start.

The database is created automatically on first run — no setup needed. Schema
upgrades are applied automatically on start (a small migration system tracks the
version), so you never have to rebuild your database when the app is updated.

## Settings & account

The **⚙️ Settings** page lets you change your password, delete your account
(Danger zone), and — combined with back-dating on every log form — rewrite your
history: pick a date/time on any log to record yesterday's workout accurately.

## Bodyweight history

Every bodyweight entry is kept, so your STR/relative-strength and VIT scores
follow your weight over time. The dashboard chart and **Analytics** page plot
your bodyweight trend alongside your 1RM and overall score.

## Gamification

Training is a game now: the **🏆 Achievements** page tracks your **training
streak** (consecutive days with any log), **weekly lifting volume** (kg × reps,
with an 8-week trend), **personal records** (best 1RM per lift, longest run,
field-test bests), and **15 badges** — from "First steps" up to "Legend".
The dashboard shows your live streak, this week's volume, and badge count at a
glance, with trophies one click away. Every log feeds these stats automatically;
no extra setup.

Little motivators along the way: beat a lift/run/field-test best and you get a
**"New PR 🎉"** toast; level up or gain a rank and a banner celebrates it; and
the **"Train your weakest stat"** card tells you exactly what to log next to
move your overall rank most. The goal is simple — open the app, see one thing
worth doing, do it, get praised, come back tomorrow.

## Install it as an app (PWA)

IRONBOUND is a **progressive web app** — on Android (Chrome) or iOS (Safari) use
the browser menu's *Add to Home Screen*, or hit the install icon in Chrome/Edge
on desktop. It opens full-screen like a native app, shows an app icon, and caches
its styles/scripts for fast repeat loads. No app store, no download beyond the
page you already use.

## Day & night mode

The site has both themes. The **☀️ / 🌙 button** in the top bar (and on the
login/sign-up pages) switches instantly. Your choice is remembered, and the
first visit follows your device's own light/dark setting.

## Weather advisor (no API key)

The **Log a run** page shows a compact "Good day to run?" card that uses your
location (browser GPS, falling back to IP-based lookup) and the free
**Open-Meteo** API to suggest training: rain/snow/heat → "hit the gym",
clear/mild → "go for a run". Everything runs in your browser, so no key and no
server-side calls.

## API ideas to make it better

Everything below can be added without breaking the app — pick what's useful:

| API | Key needed | What you'd gain |
| --- | ---------- | --------------- |
| **Open-Meteo** (done) | No | Weather-aware training advice |
| **Open Food Facts** | No | Scan barcodes / log calories alongside workouts |
| **ExerciseDB** or **Wger API** | No (free) | Richer exercise library with images/video demos |
| **OpenAI / Gemini / Claude** | Yes (paid) | AI coach: weekly plan + feedback from your logs |
| **Strava API** | OAuth (free) | Auto-import runs/rides instead of typing them |
| **Fitbit / Apple Health** | OAuth (free) | Pull steps, heart rate, sleep → feed VIT automatically |
| **ipwho.is** (done) | No | IP-based location fallback for the weather card |

To add a key-based API, store the key in an environment variable (never commit
it), e.g. `OPENAI_API_KEY`, and read it with `os.environ.get(...)` in `app.py`.

## Run the tests

```bash
python -m unittest discover tests -v
```

Tests run against a temporary database, so they never touch your real data.

## How stats are computed

| Metric                 | Inputs                    | Formula / source                        | Feeds |
| ---------------------- | ------------------------- | --------------------------------------- | ----- |
| 1RM (each lift)        | weight × reps             | Epley: `w × (1 + reps/30)`              | STR   |
| Relative strength      | 1RM ÷ bodyweight          | Standards: STR ranking                  | STR   |
| VO₂max                 | run distance & time       | Cooper 12-min test, normalized          | END   |
| Vertical jump          | jump height               | Field-test standards                    | POW   |
| 40 m sprint            | sprint time               | Field-test standards (inverted)         | AGI   |
| Sit & reach            | reach distance            | Field-test standards                    | FLX   |
| Body fat               | waist, neck, height       | US Navy formula                         | VIT   |

Each raw metric is interpolated across the 4 benchmark tiers (scored 0 → 100),
the six stats are combined into an **overall rank** (E→S), and a **level**
is derived from your average score.

Benchmarks sourced from StrengthLevel/NSCA aggregates, ACSM/Cooper Institute
VO₂max norms, ACE body-fat categories, and general athletic field-test
references. For guidance, not medical advice.

## Project structure

```
ironbound/
  app.py            # Flask app: engine + routes (incl. /export, /export.csv, /import)
  ironbound.db      # all data (SQLite) — created on first run
  static/css/       # design system: day & night themes (style.css)
  static/js/        # charts, weather, theme toggle, form previews (main.js, theme.js)
  static/sw.js      # service worker — installable app, static-asset caching
  static/manifest.webmanifest  # PWA manifest (app icon, standalone mode)
  static/icons/     # app icons (PNG, generated)
  static/vendor/    # self-hosted third-party libs (Chart.js — no CDN needed)
  templates/        # Jinja2 pages (+ log hub, my-logs, settings, achievements)
  tests/            # unit + integration tests
  requirements.txt
```
