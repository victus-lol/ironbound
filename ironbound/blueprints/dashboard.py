"""IRONBOUND dashboard blueprint - HUD dashboard, analytics, standards, and main user interface."""

import json
from datetime import datetime, timedelta
from io import StringIO

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend for server-side plotting

import matplotlib.pyplot as plt
import numpy as np

from flask import (
    Blueprint, render_template, request, redirect, url_for, session,
    flash, abort, g, current_app, make_response, jsonify,
)

from ironbound.core.db import get_db_connection, execute_sql_file, migrate_db, init_db
from ironbound.core.fitness_math import (
    epley_1rm,
    cooper_vo2max,
    navy_bodyfat,
    interpolate_tier,
    TIER_BENCHMARKS,
)
from ironbound.models.gamification import (
    compute_xp,
    level_from_xp,
    get_badges,
    get_rank,
    weakest_stat_recommendation,
)

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="")


def _get_user_stats(user_id: int) -> dict:
    """Fetch all stats for a user from the database."""
    from ironbound.core.db import get_db
    conn = get_db()
    stats = {}
    try:
        rows = conn.execute(
            "SELECT stat, value FROM stats WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        for row in rows:
            stats[row["stat"]] = row["value"]
    finally:
        conn.close()
    return stats or {
        "STR": 100, "END": 2000, "AGI": 50, "VIT": 20, "POW": 100, "FLX": 30
    }


def _get_user_streak(user_id: int) -> int:
    """Calculate current training streak in days."""
    from ironbound.core.db import get_db
    conn = get_db()
    try:
        # Get all logged dates ordered descending
        rows = conn.execute(
            "SELECT date_logged FROM strength_logs WHERE user_id = ? "
            "UNION ALL "
            "SELECT date_logged FROM cardio_logs WHERE user_id = ? "
            "ORDER BY date_logged DESC",
            (user_id, user_id),
        ).fetchall()

        if not rows:
            return 0

        # Calculate streak
        streak = 0
        today = datetime.utcnow().date()
        logged_dates = set()
        for row in rows:
            if row["date_logged"]:
                try:
                    d = row["date_logged"].date() if hasattr(row["date_logged"], 'date') else row["date_logged"]
                    logged_dates.add(d)
                except (ValueError, AttributeError):
                    continue

        # Count consecutive days including today if logged
        check_date = today
        while check_date in logged_dates:
            streak += 1
            check_date = (today - timedelta(days=streak)).date() if streak <= 365 else today

        return streak
    except Exception:
        return 0
    finally:
        conn.close()


def _get_user_badges(user_id: int) -> list:
    """Get earned badges for a user."""
    from ironbound.models.gamification import get_badges as _gb
    conn = get_db()
    try:
        # Get stats for badge calculation
        stats = _get_user_stats(user_id)
        # Get streak
        streak = _get_user_streak(user_id)
        # Get total workouts
        total = conn.execute(
            "SELECT COUNT(*) FROM strength_logs WHERE user_id = ? "
            "UNION ALL SELECT COUNT(*) FROM cardio_logs WHERE user_id = ?",
            (user_id, user_id),
        ).fetchone()
        total_count = (total[0] + total[1]) if total else 0

        # Get XP
        xp = compute_xp(stats)

        return _gb(xp, stats, streak, total_count)
    finally:
        conn.close()


@dashboard_bp.route("/", methods=["GET", "POST"])
def index():
    """Main dashboard - HUD with RPG stats and quick actions."""
    if "user_id" not in session:
        return render_template("landing.html")

    user_id = session["user_id"]

    # Handle POST for bodyweight logging
    if request.method == "POST":
        bodyweight_kg = request.form.get("bodyweight_kg", "").strip()
        if bodyweight_kg:
            try:
                weight = float(bodyweight_kg)
                if weight > 0:
                    from ironbound.core.db import get_db
                    from datetime import datetime
                    conn = get_db()
                    conn.execute(
                        "INSERT INTO body_logs (user_id, weight_kg, date_logged) VALUES (?, ?, ?)",
                        (session["user_id"], weight, datetime.utcnow()),
                    )
                    conn.close()
            except (ValueError, Exception):
                pass
        return redirect(url_for("dashboard.index"))

    stats = _get_user_stats(user_id)
    streak = _get_user_streak(user_id)
    badges = _get_user_badges(user_id)

    # Compute XP and level
    total_xp = compute_xp(stats)
    player_level = level_from_xp(total_xp)
    player_rank = get_rank(total_xp)

    # Get weakest stat recommendation
    weakest = weakest_stat_recommendation(stats)

    # Compute next level XP
    xp_needed = xp_needed_for_next_level(player_level, total_xp)

    # Calculate body fat if available
    bodyfat = None
    try:
        from ironbound.core.db import get_db
        conn = get_db()
        body_row = conn.execute(
            "SELECT bodyfat_percent FROM body_logs WHERE user_id = ? ORDER BY date_logged DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        conn.close()
        if body_row and body_row["bodyfat_percent"] is not None:
            bodyfat = round(body_row["bodyfat_percent"], 1)
    except Exception:
        pass

    return render_template(
        "dashboard.html",
        stats=stats,
        streak=streak,
        badges=badges,
        total_xp=total_xp,
        player_level=player_level,
        player_rank=player_rank,
        weakest_stat=weakest,
        xp_needed=xp_needed,
        bodyfat=bodyfat,
        now=datetime.utcnow,
    )


@dashboard_bp.route("/standards")
def standards():
    """Fitness standards reference with tier interpolation."""
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user_id = session["user_id"]
    stats = _get_user_stats(user_id)

    # Build standards data with user's tier for each stat
    standards_data = []
    for stat in ["STR", "END", "AGI", "VIT", "POW", "FLX"]:
        value = stats.get(stat, 0)
        tier = interpolate_tier(stat, value)
        benchmark = TIER_BENCHMARKS.get(stat, {})

        # Determine user's position
        if value <= benchmark["Average"]:
            position = "Average"
        elif value <= benchmark["Healthy"]:
            position = "Healthy"
        elif value <= benchmark["Enthusiast"]:
            position = "Enthusiast"
        elif value <= benchmark["Pro"]:
            position = "Pro"
        else:
            position = "Elite"

        standards_data.append({
            "stat": stat,
            "value": value,
            "tier": tier,
            "position": position,
            "benchmarks": benchmark,
        })

    return render_template("standards.html", standards=standards_data)


@dashboard_bp.route("/analytics")
def analytics():
    """Analytics page with charts and trends."""
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user_id = session["user_id"]
    stats = _get_user_stats(user_id)
    streak = _get_user_streak(user_id)

    # Generate trend charts
    chart_data = _generate_chart_data(user_id)

    # Body fat trend
    bodyfat_trend = _get_bodyfat_trend(user_id)

    return render_template(
        "analytics.html",
        chart_data=chart_data,
        bodyfat_trend=bodyfat_trend,
        streak=streak,
        now=datetime.utcnow,
    )


def _generate_chart_data(user_id: int) -> dict:
    """Generate chart data for analytics page."""
    from ironbound.core.db import get_db

    conn = get_db()

    # Strength logs trend (last 30 days)
    try:
        from datetime import datetime as dt_module
        thirty_days_ago = (dt_module.utcnow() - timedelta(days=30)).isoformat()
        strength_rows = conn.execute(
            "SELECT date_logged, value FROM strength_logs "
            "WHERE user_id = ? AND date_logged >= ? "
            "ORDER BY date_logged",
            (user_id, thirty_days_ago),
        ).fetchall()

        dates = []
        values = []
        for row in strength_rows:
            try:
                d = row["date_logged"]
                if hasattr(d, 'strftime'):
                    dates.append(d.strftime("%m-%d"))
                else:
                    dates.append(str(d)[:5] if d else "N/A")
                values.append(row["value"] if row["value"] is not None else 0)
            except (ValueError, AttributeError, TypeError):
                dates.append("N/A")
                values.append(0)
    except Exception:
        dates = ["W1", "W2", "W3", "W4"]
        values = [0, 0, 0, 0]

    # Cardio logs
    try:
        cardio_rows = conn.execute(
            "SELECT duration_min, distance_km FROM cardio_logs "
            "WHERE user_id = ? ORDER BY date_logged",
            (user_id,),
        ).fetchall()

        cardio_dates = []
        cardio_durations = []
        for row in cardio_rows:
            if row["duration_min"] is not None:
                cardio_dates.append(row["date_logged"][:10] if row["date_logged"] else "N/A")
                cardio_durations.append(row["duration_min"] if row["duration_min"] else 0)
    except Exception:
        cardio_dates = []
        cardio_durations = []

    conn.close()

    return {
        "strength_dates": json.dumps(dates[-10:]),
        "strength_values": json.dumps(values[-10:]),
        "cardio_dates": json.dumps(cardio_dates[-10:]),
        "cardio_durations": json.dumps(cardio_durations[-10:]),
        "bodyfat_trend": json.dumps(bodyfat_trend or [[0], [0]]),
    }


def _get_bodyfat_trend(user_id: int) -> list:
    """Get body fat percentage history for charting."""
    from ironbound.core.db import get_db
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT date_logged, bodyfat_percent FROM body_logs "
            "WHERE user_id = AND bodyfat_percent IS NOT NULL "
            "ORDER BY date_logged",
            (user_id,),
        ).fetchall()

        if not rows:
            return [[r["date_logged"][:10] if r["date_logged"] else "N/A" for r in rows],
                    [r["bodyfat_percent"] for r in rows]]

        dates = [r["date_logged"][:10] if r["date_logged"] else "N/A" for r in rows]
        values = [r["bodyfat_percent"] for r in rows]
        return [dates, values]
    except Exception:
        return [[], []]
    finally:
        conn.close()


@dashboard_bp.route("/logger")
def logger():
    """Workout logging hub."""
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    return render_template("logger.html")


@dashboard_bp.route("/plan")
def plan():
    """Training plan generator."""
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user_id = session["user_id"]
    stats = _get_user_stats(user_id)

    # Generate goal-based training timetable
    from ironbound.models.gamification import compute_xp, get_rank
    total_xp = compute_xp(stats)
    level = level_from_xp(total_xp)

    # Simple diet plan generator based on stats
    plan_data = {
        "level": level,
        "rank": get_rank(total_xp),
        "stats": stats,
        "weekly_timetable": _generate_weekly_timetable(stats),
        "food_chart_data": _generate_food_chart_data(user_id),
    }

    return render_template("plan.html", plan=plan_data)


def _generate_weekly_timetable(stats: dict) -> list:
    """Generate a goal-based training timetable."""
    timetable = []
    stats_order = ["STR", "END", "AGI", "VIT", "POW", "FLX"]
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    for i, day in enumerate(days):
        stat = stats_order[i % len(stats_order)]
        # Determine focus based on current value and tier
        value = stats.get(stat, 0)
        tier = interpolate_tier(stat, value)

        # Assign workout type based on tier
        if tier in ["Average", "Healthy"]:
            focus = "Build foundation"
        elif tier == "Enthusiast":
            focus = "Increase volume"
        else:
            focus = "Peak performance"

        timetable.append({
            "day": day,
            "focus_stat": stat,
            "focus_value": value,
            "focus_tier": tier,
            "workout_type": focus,
        })

    return timetable


def _generate_food_chart_data(user_id: int) -> dict:
    """Generate allergy-aware food chart data."""
    from ironbound.core.db import get_db

    conn = get_db()
    try:
        # Get body logs for bodyweight scaling
        body_rows = conn.execute(
            "SELECT height_cm, weight_kg FROM body_logs WHERE user_id = ? ORDER BY date_logged DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        # Default scaling if no data
        height = 175.0  # cm
        weight = 80.0   # kg

        if body_row:
            height = body_row["height_cm"] or 175.0
            weight = body_row["weight_kg"] or 80.0

        # Generate weekly food chart data (calorie estimates)
        import numpy as np
        dates = []
        calories = []

        for week in range(4):
            date = (datetime.utcnow() - timedelta(weeks=week)).strftime("%m-%d")
            dates.insert(0, date)
            # Mifflin-St Jeor equation BMR * activity factor
            bmr = 10 * weight + 6.25 * height - 5 * (weight / 2.2) + 5  # simplified
            # Moderate activity factor
            cal = bmr * 1.55
            calories.insert(0, round(cal))

        return {
            "labels": json.dumps(dates),
            "data": json.dumps(calories),
        }
    except Exception:
        return {
            "labels": json.dumps(["Week 1", "Week 2", "Week 3", "Week 4"]),
            "data": json.dumps([2500, 2500, 2500, 2500]),
        }
    finally:
        conn.close()


@dashboard_bp.route("/sync/queue")
def sync_queue():
    """Check for pending sync operations."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    # Return sync status
    return jsonify({
        "pending_exports": 0,
        "pending_imports": 0,
        "last_sync": datetime.utcnow().isoformat(),
    })


@dashboard_bp.route("/exercises")
def exercises():
    """Exercises page with body maps."""
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("exercises.html")


@dashboard_bp.route("/achievements")
def achievements():
    """Achievements page."""
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("achievements.html")


@dashboard_bp.route("/onboarding")
def onboarding():
    """Onboarding page."""
    if "user_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("onboarding.html")