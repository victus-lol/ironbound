"""IRONBOUND gamification module - XP, levels, ranks, and badges."""

import math
from datetime import datetime, timedelta


# --- XP Calculation ---
def compute_xp(current_values: dict, base_xp: int = 0) -> int:
    """Compute total XP from stat values.

    Awards XP based on tier proximity:
    - Average: 10 XP
    - Healthy: 25 XP
    - Enthusiast: 50 XP
    - Pro: 75 XP
    - Elite: 100 XP

    Args:
        current_values: Dict of stat_name -> numeric_value
        base_xp: Base XP to start from

    Returns:
        Total integer XP
    """
    from .fitness_math import TIER_BENCHMARKS, interpolate_tier

    total = base_xp
    for stat, value in current_values.items():
        if stat in TIER_BENCHMARKS:
            tier = interpolate_tier(stat, value)
            tier_points = {
                "Average": 10,
                "Healthy": 25,
                "Enthusiast": 50,
                "Pro": 75,
                "Elite": 100,
            }
            total += tier_points.get(tier, 0)
    return int(total)


# --- Level Calculation ---
def level_from_xp(total_xp: int) -> int:
    """Calculate player level from total XP.

    Uses exponential progression with base 1.5:
    level = floor(log_{1.5}(xp / 100 + 1)) + 1

    Args:
        total_xp: Total experience points

    Returns:
        Player level (1-indexed, minimum 1)
    """
    if total_xp <= 0:
        return 1
    import math
    level = math.floor(math.log(max(total_xp, 1) / 100 + 1, 1.5)) + 1
    return max(1, level)


def xp_for_level(level: int) -> int:
    """Calculate total XP required to reach a specific level.

    Args:
        level: Target level (1-indexed)

    Returns:
        Total XP needed to reach that level
    """
    if level <= 1:
        return 0
    return int(100 * ((1.5 ** (level - 1)) - 1) / 0.5)


def xp_needed_for_next_level(current_level: int, current_xp: int) -> int:
    """Calculate XP needed to reach the next level.

    Args:
        current_level: Current player level
        current_xp: Current XP total

    Returns:
        XP needed for next level
    """
    xp_to_current = xp_for_level(current_level)
    return max(1, xp_to_current - current_xp)


# --- Ranks ---
RANK_THRESHOLDS = [
    (0, "Initiate"),
    (10, "Disciple"),
    (25, "Adept"),
    (50, "Champion"),
    (100, "Grandmaster"),
    (200, "Legend"),
    (500, "Mythic"),
]


def get_rank(total_xp: int) -> str:
    """Determine player rank based on total XP.

    Args:
        total_xp: Total experience points

    Returns:
        Rank name string
    """
    rank = "Initiate"  # default
    for threshold, rank_name in sorted(RANK_THRESHOLDS, reverse=True):
        if total_xp >= threshold:
            rank = rank_name
            break
    return rank


# --- Badges ---
BADGE_DEFINITIONS = [
    {"id": "first_steps", "name": "First Steps", "description": "Log your first workout", "xp_threshold": 1},
    {"id": "steady_streak", "name": "Steady Streak", "description": "Maintain a 7-day training streak", "xp_threshold": 0},
    {"id": "iron_will", "name": "Iron Will", "description": "Complete 30 workouts", "xp_threshold": 500},
    {"id": "endurance_king", "name": "Endurance King", "description": "Run 100km total", "xp_threshold": 0},
    {"id": "strength_god", "name": "Strength God", "description": "Epley 1RM > 300", "xp_threshold": 0},
    {"id": "body_optimization", "name": "Body Optimization", "description": "Body fat % < 20", "xp_threshold": 0},
]


def get_badges(user_xp: int, stats: dict, streak_days: int = 0, total_workouts: int = 0) -> list:
    """Determine which badges a user has earned.

    Args:
        user_xp: Total XP
        stats: Dict of stat_name -> value
        streak_days: Current training streak in days
        total_workouts: Total number of workouts logged

    Returns:
        List of badge dicts earned by the user
    """
    earned = []
    for badge in BADGE_DEFINITIONS:
        earned_badge = False

        if badge["id"] == "first_steps" and user_xp >= badge["xp_threshold"]:
            earned_badge = True
        elif badge["id"] == "steady_streak" and streak_days >= 7:
            earned_badge = True
        elif badge["id"] == "iron_will" and total_workouts >= 30:
            earned_badge = True
        elif badge["id"] == "endurance_king" and stats.get("END", 0) >= 10000:
            earned_badge = True
        elif badge["id"] == "strength_god" and stats.get("STR", 0) >= 300:
            earned_badge = True
        elif badge["id"] == "body_optimization" and stats.get("VIT", 0) <= 20:
            earned_badge = True

        if earned_badge:
            earned.append({
                "id": badge["id"],
                "name": badge["name"],
                "description": badge["description"],
                "earned_at": datetime.utcnow().isoformat(),
            })

    return earned


# --- Weakest Stat Coach ---
def weakest_stat_recommendation(user_stats: dict) -> dict:
    """Recommend which stat to train for maximum XP upgrade.

    Analyzes which stat is closest to the next tier boundary
    and yields the highest marginal XP gain.

    Args:
        user_stats: Dict of stat_name -> current_value

    Returns:
        Dict with recommendation details
    """
    from .fitness_math import TIER_BENCHMARKS

    best_gain = 0
    best_stat = None
    best_target = None

    for stat, value in user_stats.items():
        if stat in TIER_BENCHMARKS:
            benchmarks = TIER_BENCHMARKS[stat]
            # Check distance to next tier above current
            if value < benchmarks["Pro"]:
                next_tier_value = benchmarks["Pro"]
                gap = next_tier_value - value
                # Estimate XP gain (reaching Pro gives 75 XP vs Average 10)
                xp_gain_estimate = 75 - 10  # 65 XP difference
                # Weight by how close we are
                weighted_gain = xp_gain_estimate * (1 - value / next_tier_value)

                if weighted_gain > best_gain:
                    best_gain = weighted_gain
                    best_stat = stat
                    best_target = next_tier_value

    if best_stat is None:
        return {
            "stat": None,
            "reason": "No analysable stats available",
            "recommendation": "Log more workouts to unlock recommendations",
        }

    return {
        "stat": best_stat,
        "current_value": user_stats.get(best_stat, 0),
        "target_value": best_target,
        "recommendation": f"Focus on {best_stat} to reach Pro tier (~{best_target}+)",
        "xp_gain_estimate": 65,
    }