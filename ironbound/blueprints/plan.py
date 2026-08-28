"""IRONBOUND plan blueprint - training timetable and diet plan generator."""

import json
from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for, session,
    flash, abort, jsonify,
)

from ironbound.core.db import get_db_connection, get_db
from ironbound.models.gamification import compute_xp, get_rank, weakest_stat_recommendation
from ironbound.core.fitness_math import interpolate_tier, TIER_BENCHMARKS

plan_bp = Blueprint("plan", __name__, url_prefix="/plan")


@plan_bp.route("/")
def index():
    """Training plan page - goal-based timetable and food chart."""
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user_id = session["user_id"]
    from ironbound.core.db import get_db
    conn = get_db()

    # Get current stats
    stats_rows = conn.execute(
        "SELECT stat, value FROM stats WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    stats = {row["stat"]: row["value"] for row in stats_rows}

    # Get user's rank and level
    total_xp = compute_xp(stats)
    rank = get_rank(total_xp)
    level = weakest_stat_recommendation.stats if hasattr(weakest_stat_recommendation, 'stats') else 1

    # Get weakest stat recommendation
    weakest = weakest_stat_recommendation(stats)

    # Generate weekly timetable
    timetable = _generate_weekly_timetable(stats)

    # Generate food chart data
    food_chart = _generate_food_chart_data(user_id, conn)

    # Generate daily workout suggestions
    daily_suggestions = _generate_daily_suggestions(stats)

    conn.close()

    return render_template(
        "plan.html",
        rank=rank,
        level=level,
        weakest_stat=weakest,
        timetable=timetable,
        food_chart=food_chart,
        daily_suggestions=daily_suggestions,
    )


def _generate_weekly_timetable(stats: dict) -> list:
    """Generate a goal-based training timetable."""
    timetable = []
    stats_order = ["STR", "END", "AGI", "VIT", "POW", "FLX"]
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for i, day in enumerate(days):
        stat = stats_order[i % len(stats_order)]
        value = stats.get(stat, 0)
        tier = interpolate_tier(stat, value)

        # Assign focus based on tier
        if tier in ["Average", "Healthy"]:
            focus = "Build foundation"
            volume = "3 sets x 8-10 reps"
        elif tier == "Enthusiast":
            focus = "Increase volume"
            volume = "4 sets x 6-8 reps"
        else:
            focus = "Peak performance"
            volume = "5 sets x 3-5 reps"

        # Rest days logic
        is_rest = value < 30 and i >= 5  # Low stats get more rest

        timetable.append({
            "day": day,
            "focus_stat": stat,
            "focus_value": value,
            "focus_tier": tier,
            "workout_type": focus,
            "volume": volume if not is_rest else "Rest day",
            "is_rest": is_rest,
        })

    return timetable


def _generate_food_chart_data(user_id: int, conn) -> dict:
    """Generate allergy-aware food chart data."""
    try:
        # Get body logs for bodyweight scaling
        body_row = conn.execute(
            "SELECT height_cm, weight_kg FROM body_logs "
            "WHERE user_id = ? ORDER BY date_logged DESC LIMIT 1",
            (user_id,),
        ).fetchone()

        height = 175.0  # cm default
        weight = 80.0   # kg default

        if body_row:
            height = body_row["height_cm"] or 175.0
            weight = body_row["weight_kg"] or 80.0

        # Generate weekly food chart data
        import numpy as np
        dates = []
        calories = []

        for week in range(4):
            date = (datetime.utcnow() - timedelta(weeks=week)).strftime("%m-%d")
            dates.insert(0, date)
            # Mifflin-St Jeor BMR * activity factor
            bmr = 10 * weight + 6.25 * height - 5 * (weight / 2.2) + 5
            cal = bmr * 1.55  # Moderate activity
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


def _generate_daily_suggestions(stats: dict) -> list:
    """Generate daily workout suggestions based on current stats."""
    suggestions = []
    stats_order = ["STR", "END", "AGI", "VIT", "POW", "FLX"]

    for i, stat in enumerate(stats_order):
        value = stats.get(stat, 0)
        tier = interpolate_tier(stat, value)

        # Generate suggestion based on tier and stat
        if tier == "Average":
            suggestion = f"Focus on {stat} fundamentals - start with compound movements"
        elif tier == "Healthy":
            suggestion = f"Maintain {stat} with regular training - progressive overload"
        elif tier == "Enthusiast":
            suggestion = f"Peak {stat} training - focus on form and technique"
        else:
            suggestion = f"Elite {stat} maintenance - prioritize recovery and mobility"

        suggestions.append({
            "stat": stat,
            "value": value,
            "tier": tier,
            "suggestion": suggestion,
        })

    return suggestions


@plan_bp.route("/timetable")
def timetable():
    """Specific timetable view."""
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user_id = session["user_id"]
    from ironbound.core.db import get_db
    conn = get_db()

    stats_rows = conn.execute(
        "SELECT stat, value FROM stats WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    stats = {row["stat"]: row["value"] for row in stats_rows}

    conn.close()

    return jsonify(_generate_weekly_timetable(stats))


@plan_bp.route("/daily-suggestion")
def daily_suggestion():
    """Daily workout suggestion API."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    from ironbound.core.db import get_db
    conn = get_db()

    stats_rows = conn.execute(
        "SELECT stat, value FROM stats WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    stats = {row["stat"]: row["value"] for row in stats_rows}

    conn.close()

    return jsonify(_generate_daily_suggestions(stats))