import hashlib
import json
import os
import re
import secrets
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from io import BytesIO

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# Point the app at a throwaway database BEFORE importing it.
_fd, _db_path = tempfile.mkstemp(suffix=".db")
os.environ["IRONBOUND_DB"] = _db_path

import app as app_module  # noqa: E402

USER = {"username": "test_hero", "password": "hunter21", "gender": "male",
        "password_confirm": "hunter21"}


def csrf_of(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    if not m:
        return ""
    return m.group(1)


class TestIronbound(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app_module.init_db()
        cls.app = app_module.app
        cls.app.config["TESTING"] = True
        cls.client = cls.app.test_client()
        cls.client.post("/signup", data={**USER, "csrf_token": csrf_of(cls.client.get("/signup").get_data(as_text=True))})

    @staticmethod
    def user_id(username=USER["username"]):
        with app_module.db_conn() as conn:
            row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
            return row["id"] if row else None

    def post(self, client, url, data=None):
        """Fetch a page to get the session's CSRF token, then POST with it."""
        token = csrf_of(client.get("/login").get_data(as_text=True))
        payload = dict(data or {})
        payload.setdefault("csrf_token", token)
        return client.post(url, data=payload)

    def signup(self, client, user):
        data = {**user, "password_confirm": user.get("password_confirm", user["password"])}
        return self.post(client, "/signup", data)

    def login(self, username=None):
        c = self.app.test_client()
        c.post("/login", data={
            "username": username or USER["username"],
            "password": USER["password"],
            "csrf_token": csrf_of(c.get("/login").get_data(as_text=True)),
        })
        return c

    def test_core_calculations(self):
        self.assertAlmostEqual(app_module.epley_1rm(80, 5), 93.3, places=1)
        self.assertEqual(app_module.epley_1rm(100, 1), 100)
        # Cooper: 2.4 km in 12 min -> VO2max 42.4
        self.assertAlmostEqual(app_module.estimate_vo2max_cooper(2.4), 42.4, places=1)
        self.assertIsNone(app_module.estimate_body_fat_navy(85, 90, 178))  # invalid
        self.assertIsNotNone(app_module.estimate_body_fat_navy(85, 38, 178))
        self.assertIsNotNone(app_module.estimate_body_fat_navy(85, 38, 178, 95, "female"))
        self.assertEqual(app_module.score_to_rank(95), "S")
        self.assertEqual(app_module.score_to_rank(80), "A")
        self.assertEqual(app_module.score_to_rank(65), "B")
        self.assertEqual(app_module.level_from_score(74)[0], 8)
        self.assertIsNone(app_module.overall_score({"STR": None, "END": None, "VIT": None}))

    def test_interpolation(self):
        b = app_module.BENCHMARKS_MALE
        self.assertEqual(app_module.interpolate_score(0.2, "bench_ratio", b), 0)
        self.assertEqual(app_module.interpolate_score(2.5, "bench_ratio", b), 100)
        self.assertAlmostEqual(app_module.interpolate_score(0.75, "bench_ratio", b), 33, places=1)
        # body fat is inverted: lower is better
        self.assertEqual(app_module.interpolate_score(30, "body_fat", b, inverted=True), 0)
        self.assertEqual(app_module.interpolate_score(8, "body_fat", b, inverted=True), 100)
        # sprint is inverted too: lower is better
        self.assertEqual(app_module.interpolate_score(10.0, "sprint_40m", b, inverted=True), 0)
        self.assertEqual(app_module.interpolate_score(3.0, "sprint_40m", b, inverted=True), 100)

    def test_auth_flow(self):
        # duplicate username rejected (test_hero exists from setUpClass)
        c = self.app.test_client()
        r = self.signup(c, USER)
        self.assertIn(b"taken", r.data)
        # wrong password rejected
        c3 = self.app.test_client()
        r = c3.post("/login", data={"username": USER["username"], "password": "nope",
                                    "csrf_token": csrf_of(c3.get("/login").get_data(as_text=True))})
        self.assertIn(b"Invalid", r.data)
        # correct login works
        r = self.login().get("/")
        self.assertEqual(r.status_code, 200)
        # logout requires POST (GET must not log out)
        c4 = self.app.test_client()
        self.assertEqual(c4.get("/logout", follow_redirects=False).status_code, 405)
        c5 = self.login()
        r = c5.post("/logout", data={"csrf_token": csrf_of(c5.get("/").get_data(as_text=True))})
        self.assertEqual(r.status_code, 302)

    def test_csrf_protection(self):
        c = self.app.test_client()
        c.post("/signup", data=USER)  # no token -> 400
        r = c.post("/login", data={"username": USER["username"], "password": USER["password"]})  # no token
        self.assertEqual(r.status_code, 400)
        c2 = self.login()
        r = c2.post("/log/strength", data={"lift": "bench", "weight_kg": 80, "reps": 5})  # no token
        self.assertEqual(r.status_code, 400)

    def test_protected_routes_require_login(self):
        for path in ["/standards", "/exercises", "/plan", "/log", "/logs", "/settings",
                     "/achievements",
                     "/log/strength", "/log/cardio", "/log/body", "/log/performance"]:
            r = self.app.test_client().get(path)
            self.assertEqual(r.status_code, 302, f"{path} should redirect to login")

    def test_landing_page_public(self):
        c = self.app.test_client()
        r = c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"IRONBOUND", r.data)
        self.assertIn(b"Turn your gym data", r.data)
        self.assertIn(b"core features", r.data.lower())
        self.assertIn(b"/signup", r.data)
        self.assertIn(b"/login", r.data)
        # the app shell must not leak to anonymous visitors
        self.assertNotIn(b"logout", r.data.lower())

    def test_landing_hides_when_logged_in(self):
        c = self.login()
        r = c.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b"Create your character", r.data)
        self.assertIn(b"dashboard", r.data.lower())

    def test_input_validation_no_crash(self):
        c = self.login()
        # bad floats return 400, not a 500 crash
        r = self.post(c, "/log/strength", {"lift": "bench", "weight_kg": "abc", "reps": 5})
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"number", r.data.lower())
        # negative weight rejected
        r = self.post(c, "/log/strength", {"lift": "bench", "weight_kg": "-50", "reps": 5})
        self.assertEqual(r.status_code, 400)
        # absurd values rejected
        r = self.post(c, "/log/strength", {"lift": "bench", "weight_kg": "9999999", "reps": "99999999"})
        self.assertEqual(r.status_code, 400)
        # missing required field
        r = self.post(c, "/log/cardio", {"distance_km": "2.4"})
        self.assertEqual(r.status_code, 400)
        # invalid lift name
        r = self.post(c, "/log/strength", {"lift": "curl", "weight_kg": 80, "reps": 5})
        self.assertEqual(r.status_code, 400)
        # invalid bodyweight on dashboard
        r = self.post(c, "/", {"bodyweight_kg": "abc"})
        self.assertEqual(r.status_code, 302)  # flash + redirect, no crash

    def test_full_pipeline_and_pages(self):
        c = self.login()
        self.assertEqual(self.post(c, "/log/strength", {"lift": "bench", "weight_kg": 80, "reps": 5}).status_code, 302)
        self.assertEqual(self.post(c, "/log/strength", {"lift": "squat", "weight_kg": 100, "reps": 5}).status_code, 302)
        self.assertEqual(self.post(c, "/log/cardio", {"distance_km": 2.4, "minutes": 12}).status_code, 302)
        self.assertEqual(self.post(c, "/log/body", {"waist_cm": 85, "neck_cm": 38, "height_cm": 178}).status_code, 302)
        self.assertEqual(self.post(c, "/log/performance", {"vertical_jump": 50, "sprint": 6.5}).status_code, 302)
        self.assertEqual(self.post(c, "/", {"bodyweight_kg": 78}).status_code, 302)

        # all pages render
        for path in ["/", "/analytics", "/standards", "/exercises", "/plan", "/log", "/logs", "/settings",
                     "/log/strength", "/log/cardio", "/log/body", "/log/performance"]:
            r = c.get(path)
            self.assertEqual(r.status_code, 200, f"{path} should render 200")

        # dashboard reflects logged data (charts live on /analytics)
        html = c.get("/").get_data(as_text=True)
        self.assertNotIn("chart-data", html)
        self.assertIn("hero-ring", html)
        self.assertIn("stat-ring", html)
        self.assertIn("Level", html)
        self.assertIn("Power", html)
        self.assertIn("Agility", html)
        self.assertIn("Flexibility", html)
        # bodyweight history was recorded
        self.assertTrue(len(app_module.bodyweight_history(self.user_id())) >= 1)

        # analytics page renders the charts + data embed (contains stat codes)
        html = c.get("/analytics").get_data(as_text=True)
        self.assertIn("chart-data", html)
        self.assertIn("barChart", html)
        self.assertIn("donutChart", html)
        self.assertIn("radarChart", html)
        self.assertIn("historyChart", html)
        self.assertIn("weightChart", html)
        self.assertIn("POW", html)
        self.assertIn("AGI", html)
        self.assertIn("FLX", html)
        # Chart.js is self-hosted, not pulled from a CDN
        self.assertIn("vendor/chart.umd.min.js", html)

        # standards page shows the "You" column and per-tier gaps
        html = c.get("/standards").get_data(as_text=True)
        self.assertIn(">You<", html)
        self.assertIn("Path to", html)

        # invalid tier falls back to enthusiast
        r = c.get("/standards?tier=banana")
        self.assertEqual(r.status_code, 200)

    def test_backdating(self):
        c = self.login()
        r = self.post(c, "/log/strength",
                      {"lift": "bench", "weight_kg": 80, "reps": 5, "logged_at": "2026-01-02T08:30"})
        self.assertEqual(r.status_code, 302)
        with app_module.db_conn() as conn:
            row = conn.execute("SELECT logged_at FROM strength_logs ORDER BY id DESC LIMIT 1").fetchone()
        self.assertTrue(row["logged_at"].startswith("2026-01-02T08:30"))

    def test_legacy_hash_migration(self):
        uid = self.user_id("legacy_user")
        if uid is None:
            self.signup(self.app.test_client(), {"username": "legacy_user", "password": "oldpass12", "gender": "male"})
            uid = self.user_id("legacy_user")
        # simulate an old SHA-256 hash row (what the pre-upgrade app stored)
        salt = secrets.token_hex(16)
        h = hashlib.sha256((salt + "oldpass12").encode()).hexdigest()
        with app_module.db_conn() as conn:
            conn.execute("UPDATE users SET password_hash = ?, salt = ? WHERE id = ?", (h, salt, uid))
            stored = conn.execute("SELECT password_hash FROM users WHERE id = ?", (uid,)).fetchone()["password_hash"]
        self.assertNotIn("$", stored)
        # old password still logs in
        c = self.app.test_client()
        r = c.post("/login", data={"username": "legacy_user", "password": "oldpass12",
                                   "csrf_token": csrf_of(c.get("/login").get_data(as_text=True))})
        self.assertEqual(r.status_code, 302)
        # and the hash has been upgraded to the new scheme
        with app_module.db_conn() as conn:
            new_hash = conn.execute("SELECT password_hash FROM users WHERE id = ?", (uid,)).fetchone()["password_hash"]
        self.assertIn("$", new_hash)

    def test_export_import_roundtrip(self):
        c = self.login()
        self.post(c, "/log/strength", {"lift": "bench", "weight_kg": 80, "reps": 5})
        self.post(c, "/", {"bodyweight_kg": 78})
        r = c.get("/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/json", r.headers["Content-Type"])
        self.assertIn("attachment", r.headers["Content-Disposition"])
        payload = r.get_json()
        # no internal ids leak
        self.assertNotIn("user_id", payload["strength_logs"][0])
        self.assertTrue(payload["bodyweight_logs"])

        # import into a fresh account
        c2 = self.app.test_client()
        self.signup(c2, {"username": "importer", "password": "pass1234", "gender": "male"})
        data = {"file": (BytesIO(json.dumps(payload).encode()), "backup.json"), "csrf_token": csrf_of(c2.get("/logs").get_data(as_text=True))}
        r = c2.post("/import", data=data, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 302)
        with app_module.db_conn() as conn:
            n = conn.execute("SELECT COUNT(*) as c FROM strength_logs WHERE user_id = ?", (self.user_id("importer"),)).fetchone()["c"]
        self.assertEqual(n, len(payload["strength_logs"]))
        self.assertGreaterEqual(n, 1)

    def test_settings_and_account_deletion(self):
        c = self.app.test_client()
        self.signup(c, {"username": "settings_guy", "password": "s3tupPass", "gender": "male"})
        html = c.get("/settings").get_data(as_text=True)
        self.assertEqual(c.get("/settings").status_code, 200)
        self.assertIn("Change password", html)
        self.assertIn("Danger zone", html)
        # change password
        self.post(c, "/settings", {"action": "password", "old_password": "s3tupPass",
                                   "new_password": "newpassword1"})
        self.assertIn("Password updated", c.get("/settings").get_data(as_text=True))
        # old password no longer works
        c2 = self.app.test_client()
        r = c2.post("/login", data={"username": "settings_guy", "password": "s3tupPass",
                                    "csrf_token": csrf_of(c2.get("/login").get_data(as_text=True))})
        self.assertIn(b"Invalid", r.data)
        # new password works
        c3 = self.app.test_client()
        r = c3.post("/login", data={"username": "settings_guy", "password": "newpassword1",
                                    "csrf_token": csrf_of(c3.get("/login").get_data(as_text=True))})
        self.assertEqual(r.status_code, 302)

    def test_performance_logs_and_stats(self):
        c = self.login()
        uid = self.user_id()
        r = self.post(c, "/log/performance", {"vertical_jump": 55, "sprint": 6.1, "sit_and_reach": 22})
        self.assertEqual(r.status_code, 302)
        stats, details, ranks = app_module.compute_stats(uid)
        self.assertIsNotNone(stats["POW"])
        self.assertIsNotNone(stats["AGI"])
        self.assertIsNotNone(stats["FLX"])
        self.assertGreaterEqual(details["vertical_jump"]["value"], 55)
        self.assertEqual(details["sprint"]["value"], 6.1)
        self.assertEqual(details["sit_and_reach"]["value"], 22)
        # empty submission is rejected with 400
        r = self.post(c, "/log/performance", {"vertical_jump": "", "sprint": "", "sit_and_reach": ""})
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"at least one", r.data)
        # dashboard shows the field-test rows
        html = c.get("/").get_data(as_text=True)
        self.assertIn("Vertical jump", html)
        self.assertIn("40 m sprint", html)

    def test_log_hub_and_logs_pages(self):
        c = self.login()
        html = c.get("/log").get_data(as_text=True)
        self.assertEqual(c.get("/log").status_code, 200)
        self.assertIn("What are you logging today", html)
        self.assertIn("/log/performance", html)
        r = c.get("/logs")
        self.assertEqual(r.status_code, 200)
        self.assertIn("My logs", r.get_data(as_text=True))
        self.assertIn("Import", r.get_data(as_text=True))

    def test_edit_and_delete_logs(self):
        c = self.login()
        self.post(c, "/log/strength", {"lift": "bench", "weight_kg": 80, "reps": 5})
        uid = self.user_id()
        with app_module.db_conn() as conn:
            row = conn.execute(
                "SELECT id FROM strength_logs WHERE user_id = ? ORDER BY id DESC LIMIT 1", (uid,)
            ).fetchone()
        log_id = row["id"]

        # edit page prefills the values
        html = c.get(f"/logs/strength/{log_id}/edit").get_data(as_text=True)
        self.assertEqual(c.get(f"/logs/strength/{log_id}/edit").status_code, 200)
        self.assertIn('value="80.0"', html)
        self.assertIn("Save changes", html)

        # update it
        r = self.post(c, f"/logs/strength/{log_id}/edit", {"lift": "squat", "weight_kg": 100, "reps": 3})
        self.assertEqual(r.status_code, 302)
        with app_module.db_conn() as conn:
            row = conn.execute("SELECT lift, weight_kg, reps FROM strength_logs WHERE id = ?", (log_id,)).fetchone()
        self.assertEqual(row["lift"], "squat")
        self.assertEqual(row["weight_kg"], 100)
        self.assertEqual(row["reps"], 3)

        # logs page reflects the edited summary
        self.assertIn("Squat", c.get("/logs").get_data(as_text=True))

        # another user can neither edit nor delete it
        c2 = self.app.test_client()
        self.signup(c2, {"username": "intruder", "password": "xpass1234", "gender": "male"})
        self.assertEqual(c2.get(f"/logs/strength/{log_id}/edit").status_code, 302)
        self.post(c2, f"/logs/strength/{log_id}/delete", {})
        with app_module.db_conn() as conn:
            still = conn.execute("SELECT id FROM strength_logs WHERE id = ?", (log_id,)).fetchone()
        self.assertIsNotNone(still)

        # owner deletes it
        self.post(c, f"/logs/strength/{log_id}/delete", {})
        with app_module.db_conn() as conn:
            gone = conn.execute("SELECT id FROM strength_logs WHERE id = ?", (log_id,)).fetchone()
        self.assertIsNone(gone)

    def test_edit_performance_and_cardio(self):
        c = self.login()
        self.post(c, "/log/cardio", {"distance_km": 2.0, "minutes": 10})
        self.post(c, "/log/performance", {"vertical_jump": 50})
        uid = self.user_id()
        with app_module.db_conn() as conn:
            cardio = conn.execute(
                "SELECT id FROM cardio_logs WHERE user_id = ? ORDER BY id DESC LIMIT 1", (uid,)
            ).fetchone()
            perf = conn.execute(
                "SELECT id FROM performance_logs WHERE user_id = ? ORDER BY id DESC LIMIT 1", (uid,)
            ).fetchone()

        r = self.post(c, f"/logs/cardio/{cardio['id']}/edit", {"distance_km": 3.0, "minutes": 12})
        self.assertEqual(r.status_code, 302)
        r = self.post(c, f"/logs/performance/{perf['id']}/edit",
                      {"vertical_jump": 60, "sprint": "", "sit_and_reach": ""})
        self.assertEqual(r.status_code, 302)

        with app_module.db_conn() as conn:
            cc = conn.execute("SELECT distance_km, minutes FROM cardio_logs WHERE id = ?", (cardio["id"],)).fetchone()
            pp = conn.execute("SELECT vertical_jump_cm, sprint_40m_s FROM performance_logs WHERE id = ?",
                              (perf["id"],)).fetchone()
        self.assertEqual(cc["distance_km"], 3.0)
        self.assertEqual(cc["minutes"], 12)
        self.assertEqual(pp["vertical_jump_cm"], 60)
        self.assertIsNone(pp["sprint_40m_s"])

        # clearing every field is rejected
        r = self.post(c, f"/logs/performance/{perf['id']}/edit",
                      {"vertical_jump": "", "sprint": "", "sit_and_reach": ""})
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"at least one", r.data)

    def test_scores_are_sane(self):
        c = self.login()
        uid = self.user_id()
        stats, details, ranks = app_module.compute_stats(uid)
        self.assertEqual(set(stats.keys()), {"STR", "END", "AGI", "VIT", "POW", "FLX"})
        for v in stats.values():
            if v is not None:
                self.assertTrue(0 <= v <= 100)
        self.assertTrue(0 <= app_module.overall_score(stats) <= 100)

    def test_gamification_helpers(self):
        c = self.app.test_client()
        self.signup(c, {"username": "virgin_gamer", "password": "fresh1234", "gender": "male"})
        uid = self.user_id("virgin_gamer")
        self.assertEqual(app_module.compute_streak(uid), {"current": 0, "best": 0, "last_log": None})
        self.assertEqual(app_module.weekly_volume(uid)["volume"], 0)
        self.assertEqual(app_module.personal_records(uid), {})
        badges, ctx = app_module.check_badges(uid)
        self.assertEqual(len(badges), len(app_module.BADGES))
        self.assertTrue(all("earned" in b for b in badges))
        self.assertTrue(all(not b["earned"] for b in badges))

    def test_streak_volume_and_achievements_page(self):
        from datetime import datetime, timedelta
        c = self.login()
        uid = self.user_id()
        today = datetime.now().date()
        # three consecutive days ending today
        for offset in (2, 1, 0):
            day = (today - timedelta(days=offset)).isoformat()
            r = self.post(c, "/log/strength",
                          {"lift": "bench", "weight_kg": 80, "reps": 5, "logged_at": day + "T08:00"})
            self.assertEqual(r.status_code, 302)
            r = self.post(c, "/log/cardio", {"distance_km": 2.0, "minutes": 11, "logged_at": day + "T08:00"})
            self.assertEqual(r.status_code, 302)
        streak = app_module.compute_streak(uid)
        self.assertEqual(streak["best"], 3)
        self.assertGreaterEqual(streak["current"], 1)
        vol = app_module.weekly_volume(uid)
        self.assertGreaterEqual(vol["volume"], 3 * 80 * 5)
        self.assertGreaterEqual(vol["sessions"], 3)
        prs = app_module.personal_records(uid)
        self.assertIn("lift_bench", prs)
        self.assertIn("longest_run", prs)
        self.assertGreaterEqual(prs["lift_bench"]["value"], 90)
        # achievements page renders and shows earned badges
        html = c.get("/achievements").get_data(as_text=True)
        self.assertEqual(c.get("/achievements").status_code, 200)
        self.assertIn("Training streak", html)
        self.assertIn("Personal records", html)
        self.assertIn("badge-grid", html)
        badges, ctx = app_module.check_badges(uid)
        earned = {b["key"] for b in badges if b["earned"]}
        self.assertIn("first_steps", earned)
        self.assertIn("iron_lover", earned)
        self.assertIn("on_the_move", earned)
        self.assertIn("record_breaker", earned)
        # dashboard exposes streak / volume / badge counts
        dash = c.get("/").get_data(as_text=True)
        self.assertIn("day streak", dash)
        self.assertIn("badges earned", dash)

    def test_exercises_page_has_body_maps(self):
        c = self.login()
        html = c.get("/exercises").get_data(as_text=True)
        self.assertEqual(c.get("/exercises").status_code, 200)
        self.assertIn("body-fig", html)
        self.assertIn("m-pecs on", html)     # Chest entry highlighted
        self.assertIn("m-heart on", html)    # Cardio entry highlighted
        self.assertIn("m-lats on", html)     # Back entry highlighted
        # every library entry maps to at least one visible region
        for item in app_module.EXERCISE_LIBRARY:
            self.assertTrue(app_module.MAP_BY_MUSCLE.get(item["muscle"]), item["muscle"])

    def test_volume_series(self):
        uid = self.user_id()
        self.post(self.login(), "/log/strength", {"lift": "squat", "weight_kg": 100, "reps": 3})
        series = app_module.volume_series(uid, weeks=4)
        self.assertEqual(len(series), 4)
        self.assertTrue(all(0 <= v["volume"] for v in series))
        self.assertTrue(series[-1]["is_current"])

    def test_training_heatmap_structure(self):
        from datetime import date, timedelta
        uid = self.user_id()
        self.post(self.login(), "/log/strength", {"lift": "bench", "weight_kg": 60, "reps": 5})
        grid = app_module.training_heatmap(uid, weeks=13)
        self.assertEqual(len(grid), 13)          # 13 weeks
        self.assertEqual(len(grid[0]), 7)        # Mon..Sun each
        today = date.today().isoformat()
        seen_today = any(d["today"] for w in grid for d in w)
        self.assertTrue(seen_today)
        # weeks are contiguous and Monday-first
        monday = date.fromisoformat(grid[0][0]["iso"])
        self.assertEqual(monday.weekday(), 0)
        nxt = date.fromisoformat(grid[0][1]["iso"])
        self.assertEqual((nxt - monday).days, 1)
        # today's entry (just logged) carries a count of at least 1
        for d in grid:
            for day in d:
                if day["today"]:
                    self.assertGreaterEqual(day["count"], 1)

    def test_rank_progress_and_dashboard_nudges(self):
        c = self.login()
        self.post(c, "/", {"bodyweight_kg": 80})
        self.post(c, "/log/strength", {"lift": "bench", "weight_kg": 60, "reps": 5})
        self.assertIsNotNone(app_module.rank_progress(55))   # D -> C
        self.assertEqual(app_module.rank_progress(55)["next"], "C")
        self.assertEqual(app_module.rank_progress(100)["next"], None)  # maxed
        html = c.get("/").get_data(as_text=True)
        self.assertIn("stat-next", html)
        self.assertIn("hm-cell", html)      # training rhythm renders
        self.assertIn("top-level", html)    # player HUD renders
        self.assertIn("day streak", html)
        self.assertIn("badges earned", html)

    def test_pr_detection(self):
        c = self.app.test_client()
        self.signup(c, {"username": "pr_hero", "password": "prpass123", "gender": "male"})
        # first set ever = automatic PR
        self.post(c, "/log/strength", {"lift": "bench", "weight_kg": 60, "reps": 5})
        self.assertIn("PR", c.get("/").get_data(as_text=True))
        # same set again = no PR
        self.post(c, "/log/strength", {"lift": "bench", "weight_kg": 60, "reps": 5})
        self.assertNotIn("New bench", c.get("/").get_data(as_text=True))
        # heavier set = new PR
        self.post(c, "/log/strength", {"lift": "bench", "weight_kg": 90, "reps": 3})
        html = c.get("/").get_data(as_text=True)
        self.assertIn("PR", html)
        self.assertIn("Bench Press PR", html)

    def test_csv_export(self):
        c = self.login()
        self.post(c, "/log/strength", {"lift": "deadlift", "weight_kg": 140, "reps": 5})
        self.post(c, "/log/cardio", {"distance_km": 2.0, "minutes": 11})
        r = c.get("/export.csv")
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/csv", r.headers["Content-Type"])
        self.assertIn("attachment", r.headers["Content-Disposition"])
        body = r.get_data(as_text=True)
        self.assertIn("type,metric", body)
        self.assertIn("strength,deadlift,140", body)
        self.assertIn("cardio,2.0,11", body)

    def test_level_up_banner_and_next_up(self):
        c = self.login()
        # no banner on first load (no previous level stored)
        self.assertNotIn("LEVEL UP", c.get("/").get_data(as_text=True))
        # after logging data, dashboard shows a "train next" nudge
        self.post(c, "/log/strength", {"lift": "bench", "weight_kg": 80, "reps": 5})
        self.post(c, "/", {"bodyweight_kg": 75})
        html = c.get("/").get_data(as_text=True)
        self.assertIn("weakest stat", html)
        self.assertIn("nextup-card", html)

    def test_exercise_logging_route_and_stats(self):
        # use a throwaway user so we start from a genuinely empty sheet
        c = self.app.test_client()
        self.signup(c, {"username": "ex_fresh", "password": "hunter21", "gender": "male"})
        uid = self.user_id("ex_fresh")
        # no data yet: stats are all None
        stats, _, _ = app_module.compute_stats(uid)
        self.assertIsNone(stats["STR"])
        # log an accessory exercise that feeds STR
        r = self.post(c, "/log/exercise", {"exercise_key": "Biceps", "sets": 3, "reps": 12, "weight_kg": 15})
        self.assertEqual(r.status_code, 302)
        # the log appears in My Logs and in the DB
        self.assertIn("Curl", c.get("/logs").get_data(as_text=True))
        with app_module.db_conn() as conn:
            row = conn.execute("SELECT * FROM exercise_logs WHERE user_id = ?", (uid,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["feeds_stat"], "STR")
        self.assertEqual(row["reps"], 12)
        # a pure exercise log now gives a small training-only stat (bounded, non-negative)
        stats, details, _ = app_module.compute_stats(uid)
        self.assertIsNotNone(stats["STR"])
        self.assertTrue(0 <= stats["STR"] <= 10)
        self.assertGreaterEqual(details["training"]["STR"], 0)
        # timed exercises also work and validation rejects mixing reps + minutes
        self.post(c, "/log/exercise", {"exercise_key": "Cardio — Steady State (Base)", "minutes": 30})
        r = self.post(c, "/log/exercise", {"exercise_key": "Biceps", "sets": 3, "reps": 10, "minutes": 20})
        self.assertEqual(r.status_code, 400)
        # a nonexistent exercise is rejected
        r = self.post(c, "/log/exercise", {"exercise_key": "Not A Real One", "reps": 5})
        self.assertEqual(r.status_code, 400)

    def test_big3_exercise_dual_path(self):
        # Big-3 lifts logged through the general exercise library should also
        # write a strength_logs row, so one entry counts for credit AND 1RM.
        c = self.app.test_client()
        self.signup(c, {"username": "dual_hero", "password": "hunter21", "gender": "male"})
        uid = self.user_id("dual_hero")
        r = self.post(c, "/log/exercise", {"exercise_key": "Legs (Quads/Glutes)", "sets": 3, "reps": 5, "weight_kg": 100})
        self.assertEqual(r.status_code, 302)
        with app_module.db_conn() as conn:
            ex = conn.execute("SELECT * FROM exercise_logs WHERE user_id = ?", (uid,)).fetchone()
            sl = conn.execute("SELECT * FROM strength_logs WHERE user_id = ? AND lift = 'squat'", (uid,)).fetchone()
        self.assertIsNotNone(ex)
        self.assertIsNotNone(sl)
        self.assertEqual(sl["source_exercise_log_id"], ex["id"])
        self.assertEqual(sl["weight_kg"], 100)
        self.assertEqual(sl["reps"], 5)
        # the 1RM is now visible to the stat engine (with a bodyweight to score against)
        self.post(c, "/", {"bodyweight_kg": 80})
        self.assertAlmostEqual(app_module.best_1rm(uid, "squat"), app_module.epley_1rm(100, 5), places=1)
        stats, _, _ = app_module.compute_stats(uid)
        self.assertIsNotNone(stats["STR"])
        # minutes-based big-3 log does NOT dual-write a 1RM
        self.post(c, "/log/exercise", {"exercise_key": "Legs (Quads/Glutes)", "minutes": 30})
        with app_module.db_conn() as conn:
            n = conn.execute(
                "SELECT COUNT(*) AS c FROM strength_logs WHERE user_id = ? AND source_exercise_log_id IS NOT NULL",
                (uid,),
            ).fetchone()["c"]
        self.assertEqual(n, 1)
        # editing the big-3 log out of the big-3 removes the linked 1RM row
        self.post(c, f"/logs/exercise/{ex['id']}/edit",
                  {"exercise_key": "Biceps", "sets": 3, "reps": 10, "weight_kg": 15})
        with app_module.db_conn() as conn:
            linked = conn.execute(
                "SELECT id FROM strength_logs WHERE source_exercise_log_id = ?", (ex["id"],)
            ).fetchone()
        self.assertIsNone(linked)
        self.assertIsNone(app_module.best_1rm(uid, "squat"))
        # and editing a normal log INTO a big-3 adds one
        self.post(c, f"/logs/exercise/{ex['id']}/edit",
                  {"exercise_key": "Legs (Quads/Glutes)", "sets": 3, "reps": 5, "weight_kg": 110})
        with app_module.db_conn() as conn:
            linked = conn.execute(
                "SELECT * FROM strength_logs WHERE source_exercise_log_id = ?", (ex["id"],)
            ).fetchone()
        self.assertIsNotNone(linked)
        self.assertEqual(linked["weight_kg"], 110)
        # deleting the exercise log deletes its linked 1RM row too
        c2 = self.login("dual_hero")
        self.post(c2, f"/logs/exercise/{ex['id']}/delete")
        with app_module.db_conn() as conn:
            linked = conn.execute(
                "SELECT id FROM strength_logs WHERE source_exercise_log_id = ?", (ex["id"],)
            ).fetchone()
        self.assertIsNone(linked)
        self.assertIsNone(app_module.best_1rm(uid, "squat"))

    def test_interpolation_boundaries(self):
        b = app_module.BENCHMARKS_MALE
        # exact tier thresholds map to exactly 33 / 66 / 100
        self.assertEqual(app_module.interpolate_score(0.75, "bench_ratio", b), 33.0)
        self.assertEqual(app_module.interpolate_score(1.25, "bench_ratio", b), 66.0)
        self.assertEqual(app_module.interpolate_score(2.0, "bench_ratio", b), 100.0)
        # midpoints between tiers
        self.assertAlmostEqual(app_module.interpolate_score(1.0, "bench_ratio", b), 49.5, places=1)
        # inverted metrics (body fat: lower is better) still hit exact tiers
        self.assertEqual(app_module.interpolate_score(20, "body_fat", b, inverted=True), 33.0)
        self.assertEqual(app_module.interpolate_score(15, "body_fat", b, inverted=True), 66.0)
        self.assertEqual(app_module.interpolate_score(9, "body_fat", b, inverted=True), 100.0)
        # below the bottom tier floors at 0, above the top caps at 100
        self.assertEqual(app_module.interpolate_score(0.2, "bench_ratio", b), 0)
        self.assertEqual(app_module.interpolate_score(0.05, "bench_ratio", b), 0)
        self.assertEqual(app_module.interpolate_score(3.0, "bench_ratio", b), 100)

    def test_import_adversarial(self):
        c = self.login()
        # non-JSON file -> friendly error, no crash (flash shows on My Logs)
        r = self.post(c, "/import", {"file": (BytesIO(b"not json at all"), "x.json")})
        self.assertEqual(r.status_code, 302)
        self.assertIn(b"valid JSON", c.get("/logs").get_data())
        # JSON that is not an object -> friendly error, no crash
        r = self.post(c, "/import", {"file": (BytesIO(b"[1,2,3]"), "x.json")})
        self.assertEqual(r.status_code, 302)
        self.assertIn(b"backup", c.get("/logs").get_data())
        # hostile rows: non-dicts, wrong numeric types, absurd values are skipped
        payload = {
            "strength_logs": [
                "junk",
                None,
                {"weight_kg": "not-a-number", "reps": 5},
                {"weight_kg": 70, "reps": "many"},
                {"weight_kg": 10 ** 30, "reps": 5},
                {"weight_kg": 60, "reps": 5, "logged_at": "2026-03-01T08:00"},
            ]
        }
        r = self.post(c, "/import", {"file": (BytesIO(json.dumps(payload).encode()), "x.json")})
        self.assertEqual(r.status_code, 302)
        with app_module.db_conn() as conn:
            rows = conn.execute(
                "SELECT weight_kg, reps FROM strength_logs WHERE logged_at LIKE '2026-03-01%'"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["weight_kg"], 60)

    def test_standards_tier_highlight(self):
        c = self.login()
        self.post(c, "/log/strength", {"lift": "bench", "weight_kg": 80, "reps": 5})
        self.post(c, "/", {"bodyweight_kg": 78})
        html = c.get("/standards?tier=pro").get_data(as_text=True)
        self.assertIn("Path to Pro", html)
        # the Pro column in the reference tables gets the active highlight
        self.assertEqual(html.count("t-col-active"), 18)  # header + 8 metric cells, ×2 genders
        html2 = c.get("/standards?tier=healthy").get_data(as_text=True)
        self.assertIn("Path to Healthy", html2)

    def test_streak_danger_nudge(self):
        # fresh user, so we control the entire history
        c = self.app.test_client()
        self.signup(c, {"username": "streak_guy", "password": "hunter21", "gender": "male"})
        uid = self.user_id("streak_guy")
        # no history -> no nudge
        self.assertNotIn("on the line", c.get("/").get_data(as_text=True))
        # a log two days ago means the streak is currently 0 with history
        self.post(c, "/log/strength",
                  {"lift": "bench", "weight_kg": 60, "reps": 5,
                   "logged_at": (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%dT08:00")})
        streak = app_module.compute_streak(uid)
        self.assertEqual(streak["current"], 0)
        self.assertIsNotNone(streak["last_log"])
        html = c.get("/").get_data(as_text=True)
        self.assertIn("on the line", html)

    def test_edit_exercise_log(self):
        c = self.login()
        uid = self.user_id()
        self.post(c, "/log/exercise", {"exercise_key": "Biceps", "sets": 3, "reps": 10, "weight_kg": 12})
        with app_module.db_conn() as conn:
            row = conn.execute("SELECT id FROM exercise_logs WHERE user_id = ? ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
        log_id = row["id"]
        html = c.get(f"/logs/exercise/{log_id}/edit").get_data(as_text=True)
        self.assertEqual(c.get(f"/logs/exercise/{log_id}/edit").status_code, 200)
        self.assertIn("Biceps", html)
        r = self.post(c, f"/logs/exercise/{log_id}/edit", {"exercise_key": "Shoulders", "sets": 4, "reps": 8, "weight_kg": 20})
        self.assertEqual(r.status_code, 302)
        with app_module.db_conn() as conn:
            updated = conn.execute("SELECT exercise_name, feeds_stat FROM exercise_logs WHERE id = ?", (log_id,)).fetchone()
        self.assertEqual(updated["feeds_stat"], "STR")
        self.assertEqual(updated["exercise_name"], "Overhead Press")

    def test_exercise_logging_integration(self):
        c = self.login()
        uid = self.user_id()
        # exercise logs count toward streaks / heatmap / gamification totals
        self.post(c, "/log/exercise", {"exercise_key": "Biceps", "sets": 3, "reps": 10})
        streak = app_module.compute_streak(uid)
        self.assertGreaterEqual(streak["current"], 1)
        grid = app_module.training_heatmap(uid, weeks=13)
        self.assertTrue(any(d["count"] > 0 for w in grid for d in w))
        badges, ctx = app_module.check_badges(uid)
        self.assertGreaterEqual(ctx["total_logs"], 1)
        self.assertGreaterEqual(ctx["exercise_logs"], 1)
        # export round-trips exercise logs
        payload = json.loads(c.get("/export").get_data(as_text=True))
        self.assertTrue(payload["exercise_logs"])
        self.post(c, "/import", {"file": (BytesIO(json.dumps({
            "exercise_logs": [{"exercise_key": "Calves", "exercise_name": "Standing Calf Raise",
                               "feeds_stat": "STR", "sets": 4, "reps": 15, "logged_at": "2026-01-01T08:00:00"}]
        }).encode()), "backup.json")})
        with app_module.db_conn() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM exercise_logs WHERE user_id = ? AND exercise_name = ?",
                             (uid, "Standing Calf Raise")).fetchone()["c"]
        self.assertEqual(n, 1)

    def test_glossary_tooltips_render(self):
        c = self.login()
        self.post(c, "/log/strength", {"lift": "bench", "weight_kg": 80, "reps": 5})
        html = c.get("/").get_data(as_text=True)
        self.assertIn('class="help', html)          # cue-card bubble markup
        self.assertIn("Raw lifting power", html)    # glossary copy rendered
        self.assertIn("To raise it", html)
        # exercises page shows per-card help and a Log it button
        ex = c.get("/exercises").get_data(as_text=True)
        self.assertIn("/log/exercise?exercise=", ex)
        self.assertIn("Log it", ex)

    def test_plan_save_and_render(self):
        c = self.app.test_client()
        self.signup(c, {"username": "planner", "password": "planPass1", "gender": "male"})
        # no prefs yet → setup form, no timetable
        html = c.get("/plan").get_data(as_text=True)
        self.assertEqual(c.get("/plan").status_code, 200)
        self.assertIn("Set up your plan", html)
        self.assertNotIn("Weekly training timetable", html)
        # save a 4-day muscle-building plan with a focus + diet preferences
        r = self.post(c, "/plan", {
            "goal": "muscle_building", "training_days": 4,
            "rest_days": [0, 3, 6], "focus_muscles": ["Calves"],
            "diet_type": "pescatarian", "allergies": ["seafood"],
            "diet_rules": "Fast on Friday",
        })
        self.assertEqual(r.status_code, 302)
        with app_module.db_conn() as conn:
            row = conn.execute("SELECT * FROM plan_prefs WHERE user_id = ?",
                               (self.user_id("planner"),)).fetchone()
        self.assertIsNotNone(row)
        # rendered plan: timetable + diet chart with the saved details
        html = c.get("/plan").get_data(as_text=True)
        self.assertIn("Weekly training timetable", html)
        self.assertIn("General food chart", html)
        self.assertIn("Muscle building", html)
        self.assertIn("Bench Press", html)      # from the muscle-building split
        self.assertIn("Fish", html)             # pescatarian chart includes fish
        self.assertIn("Special days", html)     # "Friday" got flagged from rules
        self.assertIn("seafood", html)          # allergy surfaced
        self.assertEqual(len(app_module.build_weekly_plan(dict(row))["days"]), 7)

    def test_plan_validation(self):
        c = self.app.test_client()
        self.signup(c, {"username": "plan_valid", "password": "planPass1", "gender": "male"})
        # rest-day count must match training days
        self.post(c, "/plan", {"goal": "all_round", "training_days": 3,
                               "rest_days": [0], "diet_type": "veg"})
        self.assertIn("exactly 4 rest day", c.get("/plan").get_data(as_text=True))
        # unknown goal rejected
        self.post(c, "/plan", {"goal": "xyz", "training_days": 3,
                               "rest_days": [0, 3, 6, 5], "diet_type": "veg"})
        self.assertIn("valid training goal", c.get("/plan").get_data(as_text=True))
        # bad diet type rejected
        self.post(c, "/plan", {"goal": "all_round", "training_days": 3,
                               "rest_days": [0, 3, 6, 5], "diet_type": "carnivore"})
        self.assertIn("valid diet type", c.get("/plan").get_data(as_text=True))
        # nothing gets saved on validation failure
        with app_module.db_conn() as conn:
            row = conn.execute("SELECT * FROM plan_prefs WHERE user_id = ?",
                               (self.user_id("plan_valid"),)).fetchone()
        self.assertIsNone(row)

    def test_plan_diet_generation(self):
        prefs = {"goal": "weight_loss", "training_days": 3, "rest_days": [0, 5, 6],
                 "focus_muscles": [], "diet_type": "veg", "allergies": ["gluten", "seafood"],
                 "diet_rules": "No onion or garlic on Tuesday"}
        chart = app_module.build_diet_chart(prefs)
        self.assertEqual(len(chart["days"]), 7)
        self.assertIn("Gluten", chart["excluded"])
        self.assertIn("Seafood", chart["excluded"])
        banned = app_module.ALLERGY_FOODS["gluten"] + app_module.ALLERGY_FOODS["seafood"]
        for day in chart["days"]:
            for slot in day["slots"]:
                meal = slot["meal"].lower()
                self.assertFalse(any(word in meal for word in banned),
                                 f"allergen meal selected: {slot['meal']}")
                # veg diet never suggests meat, fish or egg
                self.assertNotRegex(slot["meal"], r"(?i)\b(chicken|mutton|fish|egg)\b")
        self.assertIn(1, chart["special_days"])  # Tuesday flagged
        # all four slots exist each day
        self.assertEqual([s["slot"] for s in chart["days"][0]["slots"]],
                         ["Breakfast", "Lunch", "Evening snack", "Dinner"])

    def test_plan_daily_macro_targets(self):
        prefs = {"goal": "weight_loss", "training_days": 3, "rest_days": [0, 5, 6],
                 "focus_muscles": [], "diet_type": "non_veg", "allergies": [], "diet_rules": ""}
        # no bodyweight → generic fallback numbers
        chart = app_module.build_diet_chart(prefs)
        self.assertFalse(chart["targets"]["uses_bw"])
        self.assertEqual(chart["targets"]["kcal"], 2000)
        # with bodyweight → scaled per-slot estimates that sum to the daily total
        chart = app_module.build_diet_chart(prefs, bodyweight_kg=80)
        t = chart["targets"]
        self.assertTrue(t["uses_bw"])
        self.assertGreaterEqual(t["kcal"], 2000)
        self.assertGreater(t["protein"], 100)
        self.assertGreater(t["carbs"], 150)
        self.assertGreater(t["fat"], 40)
        day = chart["days"][0]
        total = sum(s["kcal"] for s in day["slots"])
        self.assertEqual(total, t["kcal"])

    def test_plan_today_dashboard_card(self):
        # no plan saved → no today card
        c = self.login()
        self.assertNotIn("today-card", c.get("/").get_data(as_text=True))
        # save a plan and the dashboard shows a "today" card with links
        c2 = self.app.test_client()
        self.signup(c2, {"username": "today_guy", "password": "planPass1", "gender": "male"})
        self.post(c2, "/plan", {"goal": "muscle_building", "training_days": 3,
                                "rest_days": [0, 3, 5, 6], "diet_type": "veg"})
        html = c2.get("/").get_data(as_text=True)
        self.assertIn("today-card", html)
        self.assertIn("Muscle building plan", html)
        if "Rest day" in html:
            self.assertIn("Full week", html)
        else:
            self.assertIn("Log it", html)
            self.assertIn("/log/exercise?exercise=", html)
        # the today helper agrees on the weekday index
        t = app_module.today_plan(self.user_id("today_guy"))
        self.assertEqual(t["has_plan"], True)
        from datetime import datetime as _dt
        self.assertEqual(t["day"]["index"], _dt.now().weekday())
        if not t["day"]["is_rest"]:
            for s in t["day"]["sessions"]:
                self.assertTrue(app_module.EXERCISE_BY_KEY.get(s["key"]))

    def test_today_checklist_tracks_logged_exercises(self):
        c = self.app.test_client()
        self.signup(c, {"username": "check_guy", "password": "planPass1", "gender": "male"})
        self.post(c, "/plan", {"goal": "muscle_building", "training_days": 3,
                               "rest_days": [0, 3, 5, 6], "diet_type": "veg"})
        info = app_module.today_plan(self.user_id("check_guy"))
        self.assertIsNotNone(info)
        if info["day"]["is_rest"]:
            self.assertIsNone(info["total"])
            return
        self.assertEqual(info["done_count"], 0)
        self.assertGreater(info["total"], 0)
        first = info["day"]["sessions"][0]
        key = first["key"]
        self.post(c, "/log/exercise", {"exercise_key": key, "sets": 3, "reps": 5, "weight_kg": 60})
        info2 = app_module.today_plan(self.user_id("check_guy"))
        done = {s["key"] for s in info2["day"]["sessions"] if s.get("done")}
        self.assertIn(key, done)
        self.assertEqual(info2["done_count"], len(done))
        # dashboard renders the checklist with progress + completed state
        html = c.get("/").get_data(as_text=True)
        self.assertIn("today-progress", html)
        self.assertIn("done today", html)
        self.assertIn("done-check", html)
        self.assertIn("logged", html)

    def test_plan_roundtrip(self):
        c = self.app.test_client()
        self.signup(c, {"username": "plan_backup", "password": "planPass1", "gender": "male"})
        self.post(c, "/plan", {"goal": "fat_loss", "training_days": 5,
                               "rest_days": [0, 6], "diet_type": "eggetarian",
                               "allergies": ["peanuts"], "diet_rules": "Non-veg on Saturday"})
        payload = c.get("/export").get_json()
        parts = payload["plan_prefs"][0]
        self.assertNotIn("user_id", parts)
        self.assertEqual(parts["goal"], "fat_loss")
        # import into a fresh account restores the preference set
        c2 = self.app.test_client()
        self.signup(c2, {"username": "plan_imported", "password": "planPass1", "gender": "male"})
        data = {"file": (BytesIO(json.dumps(payload).encode()), "backup.json"),
                "csrf_token": csrf_of(c2.get("/logs").get_data(as_text=True))}
        self.assertEqual(c2.post("/import", data=data, content_type="multipart/form-data").status_code, 302)
        with app_module.db_conn() as conn:
            row = conn.execute("SELECT goal, training_days, diet_type FROM plan_prefs WHERE user_id = ?",
                               (self.user_id("plan_imported"),)).fetchone()
        self.assertEqual(row["goal"], "fat_loss")
        self.assertEqual(row["diet_type"], "eggetarian")

    def test_plan_nav_present(self):
        c = self.login()
        html = c.get("/").get_data(as_text=True)
        self.assertIn('href="/plan"', html)
        self.assertIn(">Plan</a>", html)

    def test_onboarding_renders_steps(self):
        c = self.app.test_client()
        self.signup(c, {"username": "ob_guy", "password": "planPass1", "gender": "male"})
        r = c.get("/onboarding")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn('name="goal"', html)
        self.assertIn('name="training_days"', html)
        self.assertIn('name="rest_days"', html)
        self.assertIn('name="diet_type"', html)
        self.assertIn('name="allergies"', html)
        self.assertIn('name="bodyweight_kg"', html)
        self.assertIn("Build your training week", html)

    def test_onboarding_creates_plan_and_bodyweight(self):
        c = self.app.test_client()
        self.signup(c, {"username": "ob_complete", "password": "planPass1", "gender": "male"})
        r = self.post(c, "/onboarding", {
            "goal": "muscle_building", "training_days": 3,
            "rest_days": [0, 3, 5, 6], "diet_type": "veg",
            "bodyweight_kg": "80.5",
        })
        self.assertEqual(r.status_code, 302)
        uid = self.user_id("ob_complete")
        prefs = app_module.get_plan_prefs(uid)
        self.assertIsNotNone(prefs)
        self.assertEqual(prefs["goal"], "muscle_building")
        self.assertEqual(app_module.get_bodyweight(uid), 80.5)
        # dashboard now surfaces the today-card and plan exists
        html = c.get("/").get_data(as_text=True)
        self.assertIn("today-card", html)
        self.assertIn("Muscle building plan", html)

    def test_onboarding_validation_guards(self):
        c = self.app.test_client()
        self.signup(c, {"username": "ob_bad", "password": "planPass1", "gender": "male"})
        # wrong rest-day count → flash + redirect, no plan saved
        r = self.post(c, "/onboarding", {
            "goal": "fat_loss", "training_days": 4, "rest_days": [0],
            "diet_type": "veg",
        })
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(app_module.get_plan_prefs(self.user_id("ob_bad")))
        # bad bodyweight → redirect, no crash
        r = self.post(c, "/onboarding", {
            "goal": "fat_loss", "training_days": 4, "rest_days": [0, 3, 6],
            "diet_type": "veg", "bodyweight_kg": "abc",
        })
        self.assertEqual(r.status_code, 302)

    def test_offline_page_public_and_sw_shell(self):
        r = self.app.test_client().get("/offline")
        self.assertEqual(r.status_code, 200)
        self.assertIn("You're offline", r.get_data(as_text=True))
        sw = self.app.test_client().get("/static/sw.js")
        self.assertEqual(sw.status_code, 200)
        sw_text = sw.get_data(as_text=True)
        self.assertIn("/offline", sw_text)
        self.assertIn("/login", sw_text)
        self.assertIn("/signup", sw_text)
        self.assertIn("network-first", sw_text)

    # ---------- offline sync queue ----------

    def _sync_post(self, c, items, token=None, with_token=True):
        if token is None:
            token = csrf_of(c.get("/logs").get_data(as_text=True))
        headers = {"X-CSRF-Token": token} if with_token else {}
        return c.post(
            "/sync/queue",
            data=json.dumps({"items": items}),
            content_type="application/json",
            headers=headers,
        )

    def test_sync_queue_creates_all_log_types(self):
        c = self.app.test_client()
        self.signup(c, {"username": "sync_hero", "password": "syncPass1", "gender": "male"})
        r = self._sync_post(c, [
            {"type": "strength", "data": {"lift": "bench", "weight_kg": "90", "reps": "5",
                                          "logged_at": "2026-08-01T10:00"}},
            {"type": "cardio", "data": {"distance_km": "5", "minutes": "25",
                                        "logged_at": "2026-08-01T11:00"}},
            {"type": "body", "data": {"waist_cm": "85", "neck_cm": "38", "height_cm": "178",
                                      "logged_at": "2026-08-01T12:00"}},
            {"type": "performance", "data": {"vertical_jump": "55",
                                             "logged_at": "2026-08-01T13:00"}},
            {"type": "exercise", "client_id": "ex-cid-1",
             "data": {"exercise_key": "Legs (Quads/Glutes)", "sets": "3", "reps": "5",
                      "weight_kg": "100", "logged_at": "2026-08-01T14:00"}},
        ])
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.get_data(as_text=True))
        self.assertTrue(body["ok"])
        self.assertEqual(body["created"], 5)
        self.assertTrue(all(res["ok"] for res in body["results"]))
        uid = self.user_id("sync_hero")
        with app_module.db_conn() as conn:
            # bench set + the squat dual-path row from the queued exercise log
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) AS n FROM strength_logs WHERE user_id = ?", (uid,)
            ).fetchone()["n"], 2)
            dual = conn.execute(
                "SELECT * FROM strength_logs WHERE user_id = ? AND lift = 'squat'", (uid,)
            ).fetchone()
            ex = conn.execute(
                "SELECT * FROM exercise_logs WHERE user_id = ?", (uid,)
            ).fetchone()
            self.assertIsNotNone(dual)
            self.assertEqual(dual["source_exercise_log_id"], ex["id"])
            self.assertEqual(dual["client_id"], "ex-cid-1-s")
            for table in ("cardio_logs", "body_logs", "performance_logs"):
                self.assertEqual(conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table} WHERE user_id = ?", (uid,)
                ).fetchone()["n"], 1)

    def test_sync_queue_skips_bad_items_without_failing_batch(self):
        c = self.app.test_client()
        self.signup(c, {"username": "sync_mixed", "password": "syncPass1", "gender": "male"})
        r = self._sync_post(c, [
            {"type": "strength", "data": {"lift": "bench", "weight_kg": "60", "reps": "5"}},
            {"type": "strength", "data": {"lift": "bench", "weight_kg": "-5", "reps": "5"}},  # invalid
            {"type": "alien", "data": {}},              # unknown type
            {"type": "cardio"},                          # missing data
            "not-a-dict",                                # malformed entry
            {"type": "cardio", "data": {"distance_km": "3", "minutes": "15"}},
        ])
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.get_data(as_text=True))
        self.assertTrue(body["ok"])
        self.assertEqual(body["created"], 2)
        flags = [res["ok"] for res in body["results"]]
        self.assertEqual(flags, [True, False, False, False, False, True])
        uid = self.user_id("sync_mixed")
        with app_module.db_conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) AS n FROM strength_logs WHERE user_id = ?", (uid,)
            ).fetchone()["n"], 1)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) AS n FROM cardio_logs WHERE user_id = ?", (uid,)
            ).fetchone()["n"], 1)

    def test_sync_queue_is_idempotent_on_client_id(self):
        c = self.app.test_client()
        self.signup(c, {"username": "sync_dupe", "password": "syncPass1", "gender": "male"})
        item = {"type": "cardio", "client_id": "same-cid",
                "data": {"distance_km": "4", "minutes": "20"}}
        self._sync_post(c, [item])
        r = self._sync_post(c, [item])  # replay after a dropped response
        body = json.loads(r.get_data(as_text=True))
        self.assertTrue(body["ok"])
        uid = self.user_id("sync_dupe")
        with app_module.db_conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) AS n FROM cardio_logs WHERE user_id = ?", (uid,)
            ).fetchone()["n"], 1)

    def test_sync_queue_requires_auth_and_csrf(self):
        # anonymous POST without a token -> rejected by the global CSRF hook
        anon = self.app.test_client()
        r = anon.post("/sync/queue", data=json.dumps({"items": []}),
                      content_type="application/json")
        self.assertEqual(r.status_code, 400)
        # anonymous POST WITH a valid token -> passes CSRF, then @login_required
        # redirects to the login page instead of creating anything
        r = anon.post("/sync/queue", data=json.dumps({"items": []}),
                      content_type="application/json",
                      headers={"X-CSRF-Token": csrf_of(anon.get("/login").get_data(as_text=True))})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers.get("Location", ""))
        # logged in but no CSRF token -> rejected by the global hook
        c = self.app.test_client()
        self.signup(c, {"username": "sync_csrf", "password": "syncPass1", "gender": "male"})
        r = self._sync_post(c, [{"type": "cardio", "data": {"distance_km": "2", "minutes": "10"}}],
                            with_token=False)
        self.assertEqual(r.status_code, 400)

    # ---------- legacy schema + stale session regressions ----------

    def test_signup_on_legacy_not_null_salt_schema(self):
        """Early-version databases declared users.salt NOT NULL while modern auth
        no longer writes salt — every signup then failed with a bogus 'username
        taken' error. init_db must rebuild the table so signups work again."""
        fd, legacy_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        original_path = app_module.DB_PATH
        app_module.DB_PATH = legacy_path
        try:
            app_module.init_db()  # modern schema...
            with app_module.db_conn() as conn:
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.execute(
                    "CREATE TABLE users_old ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "username TEXT UNIQUE NOT NULL, "
                    "password_hash TEXT NOT NULL, "
                    "salt TEXT NOT NULL, "
                    "gender TEXT DEFAULT 'male', "
                    "created_at TEXT)"
                )
                conn.execute(
                    "INSERT INTO users_old (username, password_hash, salt, gender, created_at) "
                    "VALUES ('legacyguy', 'oldhash', 'oldsalt', 'male', '2026-01-01T00:00')"
                )
                conn.execute("DROP TABLE users")
                conn.execute("ALTER TABLE users_old RENAME TO users")
            # ...downgraded to the legacy DDL behind the app's back: signup must
            # now fail LOUDLY (schema error) instead of claiming the name is taken
            with self.assertRaises(sqlite3.IntegrityError):
                app_module.create_user("freshguy", "password123")
            # restart the app -> init_db repairs the table, preserving rows
            app_module.init_db()
            with app_module.db_conn() as conn:
                kept = conn.execute(
                    "SELECT username, salt FROM users WHERE username = 'legacyguy'"
                ).fetchone()
            self.assertIsNotNone(kept)
            self.assertEqual(kept["salt"], "oldsalt")
            self.assertTrue(app_module.create_user("freshguy", "password123"))
            # and through the full HTTP flow too
            c = self.app.test_client()
            r = self.signup(c, {"username": "freshgal", "password": "freshPass1", "gender": "female"})
            self.assertEqual(r.status_code, 302)
            self.assertIsNotNone(self.user_id("freshgal"))
        finally:
            app_module.DB_PATH = original_path
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(legacy_path + suffix)
                except OSError:
                    pass

    def test_deleted_account_kills_stale_session(self):
        """Sessions are signed cookies that survive restarts; after account
        deletion they must not keep granting access (zombie session)."""
        c = self.app.test_client()
        self.signup(c, {"username": "ghost_user", "password": "ghostPass1", "gender": "male"})
        self.post(c, "/", {"bodyweight_kg": 80})
        cookie = c.get_cookie("ironbound_session")
        self.assertIsNotNone(cookie)
        r = self.post(c, "/settings", {"action": "delete"})
        self.assertEqual(r.status_code, 302)
        self.assertIsNone(self.user_id("ghost_user"))
        # replay the pre-deletion cookie exactly as a browser that kept it would
        c.set_cookie("ironbound_session", cookie.value)
        r = c.get("/logs")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers.get("Location", ""))
        r = c.get("/")
        html = r.get_data(as_text=True)
        self.assertNotIn('id="queueBadge"', html)  # no logged-in chrome on landing
        # and the credentials are truly gone
        self.assertIsNone(app_module.verify_user("ghost_user", "ghostPass1"))
        uid = self.user_id("sync_csrf")
        with app_module.db_conn() as conn:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) AS n FROM cardio_logs WHERE user_id = ?", (uid,)
            ).fetchone()["n"], 0)


if __name__ == "__main__":
    unittest.main()