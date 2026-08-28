"""IRONBOUND logger blueprint - strength, cardio, and body measurement logging."""

import json
from datetime import datetime, timedelta

from flask import (
    Blueprint, render_template, request, redirect, url_for, session,
    flash, abort, jsonify,
)

from ironbound.core.db import get_db_connection, execute_sql_file, get_db
from ironbound.core.fitness_math import epley_1rm, cooper_vo2max


logger_bp = Blueprint("logger", __name__, url_prefix="/log")


@logger_bp.route("/")
def index():
    """Logger hub - overview of logged data."""
    if "user_id" not in session:
        return redirect(url_for("auth.login"))

    user_id = session["user_id"]

    from ironbound.core.db import get_db
    conn = get_db()

    # Get recent strength logs
    strength_rows = conn.execute(
        "SELECT stat, value, date_logged FROM strength_logs "
        "WHERE user_id = ? ORDER BY date_logged DESC LIMIT 10",
        (user_id,),
    ).fetchall()

    # Get recent cardio logs
    cardio_rows = conn.execute(
        "SELECT duration_min, distance_km, date_logged FROM cardio_logs "
        "WHERE user_id = ? ORDER BY date_logged DESC LIMIT 10",
        (user_id,),
    ).fetchall()

    # Get recent body logs
    body_rows = conn.execute(
        "SELECT neck_cm, waist_cm, hip_cm, height_cm, bodyfat_percent, date_logged FROM body_logs "
        "WHERE user_id = ? ORDER BY date_logged DESC LIMIT 5",
        (user_id,),
    ).fetchall()

    conn.close()

    return render_template(
        "logger.html",
        strength_logs=strength_rows,
        cardio_logs=cardio_rows,
        body_logs=body_rows,
    )


@logger_bp.route("/strength", methods=["POST"])
def log_strength():
    """Log a strength workout entry (new API format)."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    lift = request.form.get("lift", "").strip().lower()
    weight_kg = request.form.get("weight_kg", "0")
    reps = request.form.get("reps", "0")
    logged_at = request.form.get("logged_at", "")

    # Map lift to stat
    lift_to_stat = {
        "bench": "STR",
        "squat": "STR",
        "deadlift": "STR",
        "ohp": "STR",
        "press": "STR",
        "curl": "STR",
        "run": "END",
        "row": "END",
        "pullup": "POW",
    }
    stat = lift_to_stat.get(lift, "STR")

    try:
        weight = float(weight_kg)
        rep_count = int(reps)
        if weight <= 0 or rep_count <= 0:
            return jsonify({"error": "Invalid weight or reps"}), 400
    except ValueError:
        return jsonify({"error": "Invalid weight or reps"}), 400

    # Parse logged_at if provided
    date_logged = datetime.utcnow()
    if logged_at:
        try:
            date_logged = datetime.fromisoformat(logged_at.replace("Z", "+00:00"))
        except ValueError:
            pass

    try:
        from ironbound.core.db import get_db_connection
        with get_db_connection() as conn:
            conn.execute(
                "INSERT INTO strength_logs (user_id, stat, value, weight_kg, reps, notes, date_logged) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, stat, weight * rep_count, weight, rep_count, lift, date_logged),
            )
        flash(f"{lift} logged: {weight}kg x {rep_count}", "success")
        return redirect(url_for("logger.index"))
    except Exception as e:
        flash(f"Error logging strength: {str(e)}", "error")
        return redirect(url_for("logger.index"))


@logger_bp.route("/cardio", methods=["POST"])
def log_cardio():
    """Log a cardio workout entry (new API format)."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    distance_km = request.form.get("distance_km", "0")
    minutes = request.form.get("minutes", "0")
    logged_at = request.form.get("logged_at", "")

    try:
        distance = float(distance_km)
        duration = float(minutes)
        if distance < 0 or duration <= 0:
            return jsonify({"error": "Invalid distance or duration"}), 400
    except ValueError:
        return jsonify({"error": "Invalid distance or duration"}), 400

    date_logged = datetime.utcnow()
    if logged_at:
        try:
            date_logged = datetime.fromisoformat(logged_at.replace("Z", "+00:00"))
        except ValueError:
            pass

    try:
        from ironbound.core.db import get_db
        conn = get_db()
        conn.execute(
            "INSERT INTO cardio_logs (user_id, distance_km, duration_min, date_logged) "
            "VALUES (?, ?, ?, ?)",
            (user_id, distance, duration, date_logged),
        )
        conn.close()
        return jsonify({"success": True, "message": f"Cardio logged: {duration}min, {distance}km"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@logger_bp.route("/body", methods=["POST"])
def log_body():
    """Log body measurements for body fat calculation."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    neck = float(request.form.get("neck", "0") or "0")
    waist = float(request.form.get("waist", "0") or "0")
    hip = float(request.form.get("hip", "0") or "0")
    height = float(request.form.get("height", "0") or "0")

    try:
        from ironbound.core.db import get_db
        from ironbound.core.fitness_math import navy_bodyfat

        conn = get_db()
        # Calculate body fat percentage using US Navy formula
        bf_percent = navy_bodyfat(
            height_cm=height,
            neck_cm=neck,
            waist_cm=waist,
            hip_cm=ip if hip > 0 else 0,
            gender="male",  # In production, detect from user profile
        )

        conn.execute(
            "INSERT INTO body_logs (user_id, neck_cm, waist_cm, hip_cm, height_cm, bodyfat_percent, date_logged) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, neck, waist, hip, height, bf_percent, datetime.utcnow()),
        )
        conn.close()
        return jsonify({
            "success": True,
            "bodyfat_percent": round(bf_percent, 1),
            "message": f"Body fat calculated: {round(bf_percent, 1)}%"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@logger_bp.route("/performance", methods=["POST"])
def log_performance():
    """Log a performance test (VO2max, 1RM, etc.)."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    test_type = request.form.get("test_type", "").strip()
    value = float(request.form.get("value", "0"))

    try:
        from ironbound.core.db import get_db
        conn = get_db()
        conn.execute(
            "INSERT INTO performance_logs (user_id, test_type, value, date_logged) "
            "VALUES (?, ?, ?, ?)",
            (user_id, test_type, value, datetime.utcnow()),
        )
        conn.close()
        return jsonify({"success": True, "message": f"Performance test logged: {test_type}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@logger_bp.route("/export/json")
def export_json():
    """Export user data as JSON."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    from ironbound.core.db import get_db
    conn = get_db()

    try:
        users = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        strengths = conn.execute(
            "SELECT stat, value, date_logged FROM strength_logs WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        cardios = conn.execute(
            "SELECT duration_min, distance_km, date_logged FROM cardio_logs WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        bodies = conn.execute(
            "SELECT neck_cm, waist_cm, hip_cm, height_cm, bodyfat_percent, date_logged FROM body_logs WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        stats_rows = conn.execute(
            "SELECT stat, value FROM stats WHERE user_id = ?",
            (user_id,),
        ).fetchall()

        conn.close()

        export_data = {
            "user": dict(users) if users else {},
            "strength_logs": [dict(s) for s in strengths],
            "cardio_logs": [dict(c) for c in cardios],
            "body_logs": [dict(b) for b in bodies],
            "stats": {s["stat"]: s["value"] for s in stats_rows},
        }

        return jsonify({"success": True, "data": export_data})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500


@logger_bp.route("/import/json", methods=["POST"])
def import_json():
    """Import user data from JSON."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    data = request.get_json()

    if not data:
        return jsonify({"error": "No data provided"}), 400

    try:
        from ironbound.core.db import get_db
        conn = get_db()

        # Import stats
        if "stats" in data:
            for stat, value in data["stats"].items():
                # Check if entry exists
                existing = conn.execute(
                    "SELECT id FROM stats WHERE user_id = ? AND stat = ?",
                    (user_id, stat),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE stats SET value = ? WHERE user_id = ? AND stat = ?",
                        (value, user_id, stat),
                    )
                else:
                    conn.execute(
                        "INSERT INTO stats (user_id, stat, value) VALUES (?, ?, ?)",
                        (user_id, stat, value),
                    )

        # Import strength logs
        if "strength_logs" in data:
            for log in data["strength_logs"]:
                conn.execute(
                    "INSERT INTO strength_logs (user_id, stat, value, notes, date_logged) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (user_id, log.get("stat"), log.get("value"), log.get("notes"), log.get("date_logged")),
                )

        # Import cardio logs
        if "cardio_logs" in data:
            for log in data["cardio_logs"]:
                conn.execute(
                    "INSERT INTO cardio_logs (user_id, distance_km, duration_min, date_logged) "
                    "VALUES (?, ?, ?, ?)",
                    (user_id, log.get("distance_km"), log.get("duration_min"), log.get("date_logged")),
                )

        # Import body logs
        if "body_logs" in data:
            for log in data["body_logs"]:
                # Calculate body fat if not provided
                bf = log.get("bodyfat_percent")
                if bf is None:
                    from ironbound.core.fitness_math import navy_bodyfat
                    bf = navy_bodyfat(
                        height_cm=log.get("height_cm", 175),
                        neck_cm=log.get("neck_cm", 35),
                        waist_cm=log.get("waist_cm", 80),
                        ip=log.get("hip_cm", 0),
                        gender="male",
                    )

                conn.execute(
                    "INSERT INTO body_logs (user_id, neck_cm, waist_cm, hip_cm, height_cm, bodyfat_percent, date_logged) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, log.get("neck_cm", 35), log.get("waist_cm", 80),
                     log.get("hip_cm", 0), log.get("height_cm", 175), bf, log.get("date_logged")),
                )

        conn.commit()
        conn.close()

        return jsonify({"success": True, "message": "Data imported successfully"})
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        return jsonify({"error": str(e)}), 500


@logger_bp.route("/body", methods=["POST"])
def log_body_new():
    """Log body measurements (new API format)."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    neck = float(request.form.get("neck_cm", "0") or "0")
    waist = float(request.form.get("waist_cm", "0") or "0")
    hip = float(request.form.get("hip_cm", "0") or "0")
    height = float(request.form.get("height_cm", "0") or "0")
    logged_at = request.form.get("logged_at", "")

    try:
        from ironbound.core.fitness_math import navy_bodyfat
        bf_percent = navy_bodyfat(
            height_cm=height,
            neck_cm=neck,
            waist_cm=waist,
            hip_cm=hip if hip > 0 else 0,
            gender="male",
        )
    except Exception:
        bf_percent = None

    date_logged = datetime.utcnow()
    logged_at = request.form.get("logged_at", "")
    if logged_at:
        try:
            date_logged = datetime.fromisoformat(logged_at.replace("Z", "+00:00"))
        except ValueError:
            pass

    try:
        from ironbound.core.db import get_db
        conn = get_db()
        conn.execute(
            "INSERT INTO body_logs (user_id, neck_cm, waist_cm, hip_cm, height_cm, bodyfat_percent, date_logged) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, neck, waist, hip, height, bf_percent, date_logged),
        )
        conn.close()
        return jsonify({
            "success": True,
            "bodyfat_percent": round(bf_percent, 1) if bf_percent else None,
            "message": f"Body fat calculated: {round(bf_percent, 1)}%" if bf_percent else "Body measurements logged"
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@logger_bp.route("/performance", methods=["POST"])
def log_performance_new():
    """Log a performance test (new API format)."""
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session["user_id"]
    test_type = request.form.get("test_type", "").strip()
    value = float(request.form.get("value", "0"))
    logged_at = request.form.get("logged_at", "")

    date_logged = datetime.utcnow()
    if logged_at:
        try:
            date_logged = datetime.fromisoformat(logged_at.replace("Z", "+00:00"))
        except ValueError:
            pass

    try:
        from ironbound.core.db import get_db
        conn = get_db()
        conn.execute(
            "INSERT INTO performance_logs (user_id, test_type, value, date_logged) "
            "VALUES (?, ?, ?, ?)",
            (user_id, test_type, value, date_logged),
        )
        conn.close()
        return jsonify({"success": True, "message": f"Performance test logged: {test_type}"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500