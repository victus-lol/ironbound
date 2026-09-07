# IRONBOUND — Professional Audit
> Project history: built as the static `gymrat` SPA (v2.x), rebranded to IRONBOUND v3.0
> when it replaced the Flask app on `main`. The Flask generations live on `archive/flask`.
> Old `gymrat_*` localStorage keys and file paths in this log refer to the pre-rename layout;
> user data migrates automatically (keys unchanged).

## 8. Feature round 2026-09-07 (all six approved → shipped)
Researched Strava/NRC/route-planner patterns (2026 gamification data: weekly > daily streaks,
day-one achievements +64% retention, segments/Local Legend, OSRM keyless routing). Shipped:
1. **Route planner + GPX** — circular foot loops via OSRM `router.project-osrm.org` (no key),
   3/5/10 km, 3-attempt distance fit, elevation gain via Open-Meteo elevation API, green
   polyline + fitBounds, GPX download (Garmin/Strava), “Log this run →” prefills cardio.
   Static `js/app.js` route section; Next `app/api/route/route.ts` proxy + `lib/route.ts` + SpotsMap button.
2. **Monthly challenges** — 4 parallel tracks (12 days / 50 km / 40t / 7-day streak),
   month-scoped, celebrated once with 🏆 toast, persisted `gymrat_chal`. Next `lib/challenges.ts`.
3. **Weekly streak + freeze** — Mon-Sun weeks ≥1 log; sidebar shows week streak; 1 auto-freeze/month
   covers a single blank week (`gymrat_freeze`). Daily fire untouched (loss aversion preserved).
4. **Air quality advisor** — `air-quality-api.open-meteo.com` AQI+UV appended to weather verdict
   (indoor redirect >150, easy-only >100, UV≥8 caution). CSP updated both apps.
5. **Home turf 👑** — `visits[]` (schema v4) via “✓ Trained here” on spots; sidebar turf card crowns
   top lift + top spot; 16th badge (≥10 same lift or ≥5 same spot); denominator now dynamic.
6. **IndexedDB upgrade** — zero-dep `idb` wrapper (`gymrat` DB, `kv` store); `save()` mirrors to both,
   `hydrateFromIdb()` newest-`savedAt`-wins on boot. localStorage stays sync truth (Next compat).
Verify: static `node --check` clean; Next `vitest 50/50`, `tsc` clean, `next build 9/9`.

## 9. Step 5 — local accounts + E2E proof (2026-09-07)
Brief §4A, zero new deps (Web Crypto PBKDF2 + hand-rolled HS256 JWT, HttpOnly cookie).
`POST /api/auth/{signup,login,logout}`, `GET /api/auth/{me,verify}`, `POST /api/auth/{change-password,forgot,reset}`;
`/login`, `/signup`, `/account` pages; writes require a session once any account exists
(single-user open mode preserved); login rate-limited; generic auth errors (no enumeration);
reset tokens sha256-hashed, single-use, 1h expiry; SMTP hook stubbed (`GYMRAT_SMTP`), dev tokens
only with `GYMRAT_DEV=1`. OAuth (Google/GitHub) stays Step-5b behind env keys.
Live E2E (node script, throwaway DB): **14/14 PASS** — signup→me→verify→anon-write-401→
authed-insert→replay-skip→logout→login→change-pw→forgot→reset→login-new→bad-token-400→cleanup.
Bugs the E2E caught and fixed:
1. **POST /api/logs inserted NOTHING** — `validateImport` mutates its id set, starving the follow-up
   insert filter (`added:0` always). Now passes a copy + `lib/logs-route.test.ts` regression test.
2. **`/api/health` served a STALE count** — prerendered static at build time (traced root DB).
   Added `export const dynamic='force-dynamic'`.
3. **Secure cookie by default** — broke plain-HTTP LAN self-hosts. Now opt-in `GYMRAT_COOKIE_SECURE=1`
   (mirrors Ironbound); startup DB path logged once (`[gymrat] sqlite open:`) + `*.db` tracing
   exclusions so deploys never read a stale bundled copy.
Verify: `vitest 63/63`, `tsc` clean, `next build` 19 routes, live E2E 14/14.

Date: 2026-09-07 • Auditor: senior full-stack • Scope: `gymrat/` static SPA + `gymrat-next/` Next.js

## 1. Verdict
- Static: **A- (ship-ready demo)**. Best UX, zero ops, correct physiology. Gaps closed this round: outdoor spots, BW trend, goals, share PNG.
- Next: **B+ (production foundation)**. Real SQLite + idempotent API + rate-limit + Docker. Needs NextAuth + E2E before multi-user launch.
- Ironbound comparison: Ironbound wins on server maturity (PBKDF2, CSRF, CI). GymRat now wins on interactivity + offline + maps. Converge via Next.

## 2. Scores
| Area | Static | Next | Notes |
|---|---|---|---|
| Physiology correctness | 9/10 | 9/10 | Epley/Cooper/Navy verified by vitest 25/25 + self-tests. VIT invert fixed. |
| UX / UI polish | 9/10 | 7/10 | Fixed header, equal-height cards, FAB, focus-visible, reduced-motion. No image-scale bugs (`img{max-width:100%}`). |
| Performance | 9/10 | 8/10 | <50KB static, no CDN except Leaflet (defer). Next First Load 91.7kB. |
| Accessibility | 8/10 | 7/10 | aria labels, toast live-region, dialog modal. Missing: skip-link, chart text alternatives. |
| Security | 8/10 | 8/10 | CSP, esc(), validators, 8/min limit, API-key gate. Missing: server HttpOnly auth (Step 5). |
| Data / Offline | 9/10 | 8/10 | v3 migrations, client_id idempotent, queue, 24h spots cache. Missing: IndexedDB >5MB. |
| Testing | 8/10 | 9/10 | 40/40 vitest (rpg 25, db 3, sync 2, auth 4, spots 6). Missing: Playwright E2E. |
| PWA | 8/10 | 6/10 | manifest + sw cache. Missing: icons 192/512, screenshots. |

## 3. Issues found + fixed this round
1. **No outdoor discovery** — FIXED: `view-spots` + Leaflet + Overpass (parks/tracks/pools, run/sprint/train filter, GPS→IP→Bengaluru fallback, 24h cache, Directions links). Files: `index.html:192`, `js/app.js:727`, `css/style.css:110`.
2. **No BW trend** — FIXED: `chartBw` 30d + `bwVals` in `renderAnalytics` (`js/app.js:465`).
3. **No weekly goals** — FIXED: `goalWrap` 3 sessions + 8k volume bars (`js/app.js:480`, dashboard card).
4. **No shareable proof** — FIXED: `sharePngBtn` 900×520 canvas HUD PNG + `copyStatsBtn` (`js/app.js:800`).
5. **Next had no spots proxy** — FIXED: `app/api/spots/route.ts:1` (Overpass POST, kind mapping, 24h Map cache, 502 on fail) + `components/SpotsMap.tsx:1` + `lib/spots.test.ts:1` (6/6).
6. **CSP blocked maps** — FIXED: allow `unpkg.com`, `*.tile.openstreetmap.org`, `overpass-*.de/systems`, `nominatim` in both `index.html:7` and `next.config.js:8`.

## 4. Remaining risks (honest)
- Leaflet CDN breaks offline-first on first load (mitigated: defer, graceful “needs network”, cached spots still list). Full offline tiles = MBs, not worth it.
- Overpass rate-limits under load (mitigated: proxy cache 24h, 40-result cap, dual endpoints).
- GPS denial → IP → fallback chain can mislocate (surfaced via `src` label).
- Static still localStorage 5MB (next: Dexie IndexedDB).
- No multi-user auth yet (next: NextAuth).

## 5. What to build next (ranked)
### P0 — retention
1. **Route planner**: click 2 spots → OSRM `route.osrm.org` foot profile → distance/elev → “Log this run” prefill. No key.
2. **Strava auto-import**: OAuth + webhook → `POST /api/logs`. Kills manual entry.
3. **Weekly goal notifications**: `Notification API` + PWA push when 1 session short on Sunday.
### P1 — differentiation
4. **Air-quality + heat advisor**: Open-Meteo `air_quality` + UV → merge with weather card (“run at 6am, AQI 42 good”).
5. **Crowd + safety layer**: Overpass `lit=yes` + park hours → “lit evening loop” badge; user ratings stored in SQLite.
6. **Segment leaderboard**: per-spot best 1km (opt-in, anonymized) — brief 4C.
7. **ExerciseDB demos**: Wger GIFs in Plan `Log it`.
### P2 — ecosystem
8. **Health Connect / Apple Health**: steps/sleep → VIT auto-feed.
9. **AI coach (opt-in)**: Gemini/OpenAI weekly recap from `/api/stats` (key in env, never committed).
10. **Share PNG v2**: radar + map snapshot composite for Instagram.

## 6. How to verify
```powershell
# static
Start-Process C:\Users\Aarya\Downloads\gymrat\index.html
# Plan → Spots → Find spots near me (allow location) → filter run/sprint/train → Zoom/Directions
# Analytics → BW chart + PR timeline • Dashboard → goals + Share PNG
# Log → Run self-tests (?test=1)

# next
cd C:\Users\Aarya\Downloads\gymrat-next
npx vitest run   # 40/40
npx tsc --noEmit # clean
npx next build   # 8/8
curl http://localhost:3000/api/spots?lat=12.97&lon=77.59&radius=3000
curl http://localhost:3000/api/stats
```

## 7b. Bugfix + UX hardening round (2026-09-07)
Verified: all 100+ element IDs cross-checked JS↔HTML (0 missing), `node --check` clean,
Next `vitest 40/40`, `tsc` clean, `next build 8/8`.
Fixed:
1. **UTC date bug** — streak/heatmap/volume/analytics/seed used `toISOString()` (UTC); IST evenings logged to the wrong day. New `isoLocal()` helper (`js/app.js:4`) used everywhere.
2. **Self-test false failure** — Navy assertion `<20` vs true `21.33`; corrected to `12–23%`.
3. **Overpass double-fetch** — `endpoints.concat(tries)` hit each mirror twice; single `tries` list + proxy-shape handling (`{spots}` vs `{elements}`).
4. **Dead search button** — `findSpotsBtn` disabled on click, never re-enabled; `finally` restore + early-return guard when Leaflet CDN slow.
5. **Share PNG crash on older browsers** — `roundRect` fallback to `rect`.
6. **BW chart same color as STR** — now amber `#ffb800`.
7. **Stale PWA** — `sw.js` cache bumped `v2→v3` so installed users receive fixes.
8. **Spots grid mobile** — fragile attribute selector replaced with `.spots-grid` class + stacking.
9. **Next Dashboard** — seed used UTC dates (→local), profile now read from `localStorage` instead of hardcoded 78kg, stat grid auto-fit for mobile.
10. **Next SpotsMap** — activity filter now refilters instantly client-side + redraws markers, no refetch; grid auto-fit for mobile.
Meaningfulness check: Spots map directly powers “weather-smart runs” + END/AGI training (explicitly requested); BW trend completes VIT tracking; weekly goals close the motivation loop; share PNG implements brief 4C. No novelty features added.

## 7. Files changed this round
- `gymrat/index.html`: CSP, Leaflet, Spots nav/view, goals/share cards, BW + PR cards
- `gymrat/css/style.css`: spots/goal styles
- `gymrat/js/app.js`: views.spots, renderAnalytics BW/PR/goals, spots engine, share PNG
- `gymrat-next/next.config.js`: CSP for maps
- `gymrat-next/app/api/spots/route.ts`: proxy (new)
- `gymrat-next/components/SpotsMap.tsx`: client map (new)
- `gymrat-next/app/page.tsx`: SpotsMap wired
- `gymrat-next/lib/spots.test.ts`: 6 tests (new)
