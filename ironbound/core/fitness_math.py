"""IRONBOUND fitness mathematics - exercise physiology calculations."""

import math


# --- Strength: Epley 1RM Formula ---
def epley_1rm(weight: float, reps: int) -> float:
    """Calculate one-rep max using the Epley formula.

    1RM = w × (1 + r/30)

    Args:
        weight: Weight lifted in kg or lbs
        reps: Number of repetitions performed

    Returns:
        Estimated one-rep maximum
    """
    if reps <= 0:
        return weight
    if reps == 1:
        return weight
    return weight * (1 + reps / 30)


# --- Endurance: Cooper VO2max Estimation ---
def cooper_vo2max(distance_meters: float) -> float:
    """Estimate VO2max using the Cooper 12-minute test formula.

    VO2max = (distance_meters - 504.9) / 44.73

    Args:
        distance_meters: Distance covered in 12 minutes (meters)

    Returns:
        Estimated VO2max in mL/kg/min
    """
    if distance_meters <= 0:
        return 0.0
    return (distance_meters - 504.9) / 44.73


# --- Vitality: US Navy Body Fat Percentage ---
def navy_bodyfat(
    height_cm: float,
    neck_cm: float,
    waist_cm: float,
    hip_cm: float = 0.0,
    gender: str = "male",
) -> float:
    """Calculate body fat percentage using the US Navy circumference method.

    For men: %BodyFat = 86.010 * log(abdomen - neck) - 70.041 * log(height) + 30.30
    For women: %BodyFat = 163.205 * log(waist + hip - neck) - 121.606 * log(height) + 5.11

    Args:
        height_cm: Height in centimeters
        neck_cm: Neck circumference in centimeters
        waist_cm: Waist circumference in centimeters
        hip_cm: Hip circumference in centimeters (women only)
        gender: "male" or "female"

    Returns:
        Body fat percentage (0-100)
    """
    height_m = height_cm / 100.0

    if gender.lower() == "male":
        # abdomen-neck for men
        if neck_cm <= 0 or waist_cm <= 0:
            return 0.0
        abdomen = waist_cm
        log_val = math.log(abdomen - neck_cm)
        bf = 86.010 * log_val - 70.041 * math.log(height_m) + 30.30
    else:
        # waist+hip-neck for women
        if neck_cm <= 0 or waist_cm <= 0 or hip_cm <= 0:
            return 0.0
        val = waist_cm + hip_cm - neck_cm
        if val <= 0:
            return 0.0
        log_val = math.log(val)
        bf = 163.205 * log_val - 121.606 * math.log(height_m) + 5.11

    # Clamp to valid range
    return max(0.0, min(100.0, bf))


# --- Tier Interpolation ---
TIER_BENCHMARKS = {
    "STR": {"Average": 100, "Healthy": 150, "Enthusiast": 200, "Pro": 250, "Elite": 300},
    "END": {"Average": 2000, "Healthy": 3000, "Enthusiast": 4000, "Pro": 5000, "Elite": 6000},
    "AGI": {"Average": 50, "Healthy": 80, "Enthusiast": 100, "Pro": 120, "Elite": 150},
    "VIT": {"Average": 20, "Healthy": 25, "Enthusiast": 30, "Pro": 35, "Elite": 40},
    "POW": {"Average": 100, "Healthy": 150, "Enthusiast": 200, "Pro": 250, "Elite": 300},
    "FLX": {"Average": 30, "Healthy": 50, "Enthusiast": 70, "Pro": 90, "Elite": 110},
}


def interpolate_tier(stat: str, value: float) -> str:
    """Interpolate a tier category based on a stat value.

    Args:
        stat: Stat name (STR, END, AGI, VIT, POW, FLX)
        value: Numeric value of the stat

    Returns:
        Tier name (Average, Healthy, Enthusiast, Pro, Elite)
    """
    if stat not in TIER_BENCHMARKS:
        return "Average"

    benchmarks = TIER_BENCHMARKS[stat]
    if value <= benchmarks["Average"]:
        return "Average"
    if value <= benchmarks["Healthy"]:
        return "Healthy"
    if value <= benchmarks["Enthusiast"]:
        return "Enthusiast"
    if value <= benchmarks["Pro"]:
        return "Pro"
    return "Elite"


# --- XP and Level Calculation ---
XP_PER_LEVEL = 100
XP_MULTIPLIER = 1.5


def compute_xp(current_values: dict, base_xp: int = 0) -> int:
    """Compute total XP based on current stat values.

    Args:
        current_values: Dict of stat_name -> value
        base_xp: Base XP to start from

    Returns:
        Total computed XP
    """
    total = base_xp
    for stat, value in current_values.items():
        if stat in TIER_BENCHMARKS:
            # Award XP based on tier proximity
            tier = interpolate_tier(stat, value)
            tier_points = {"Average": 10, "Healthy": 25, "Enthusiast": 50, "Pro": 75, "Elite": 100}
            total += tier_points.get(tier, 0)
    return int(total)


def level_from_xp(total_xp: int) -> int:
    """Calculate player level from total XP.

    Uses exponential progression: level = floor(log_{1.5}(xp / 100 + 1))

    Args:
        total_xp: Total experience points

    Returns:
        Player level (1-indexed)
    """
    if total_xp <= 0:
        return 1
    # level = floor(log_{1.5}(xp/100 + 1)) + 1
    import math
    level = math.floor(math.log(max(total_xp, 1) / XP_PER_LEVEL + 1, XP_MULTIPLIER)) + 1
    return max(1, level)


def xp_for_next_level(current_level: int, current_xp: int) -> int:
    """Calculate XP needed to reach the next level.

    Args:
        current_level: Current player level
        current_xp: Current XP total

    Returns:
        XP needed for next level
    """
    xp_to_current = sum(int(XP_PER_LEVEL * (XP_MULTIPLIER ** i)) for i in range(current_level - 1))
    xp_needed = xp_to_current + XP_PER_LEVEL - current_xp
    return max(1, xp_needed)


# --- Weakest Stat Recommendation ---
def weakest_stat_recommendation(user_stats: dict) -> dict:
    """Recommend which stat to train for maximum XP gain.

    Args:
        user_stats: Dict of stat_name -> current_value

    Returns:
        Dict with recommendation including stat, current_value, target_value, xp_gain
    """
    best_gain = 0
    best_stat = None

    for stat, value in user_stats.items():
        if stat in TIER_BENCHMARKS:
            # Calculate how far from next tier
            benchmarks = TIER_BENCHMARKS[stat]
            if value < benchmarks["Pro"]:
                # Distance to Pro tier
                next_tier_value = benchmarks["Pro"]
                gap = next_tier_value - value
                # XP gain estimate (simplified)
                if gap > best_gain:
                    best_gain = gap
                    best_stat = stat

    if best_stat is None:
        return {"stat": None, "reason": "No stats available for recommendation"}

    return {
        "stat": best_stat,
        "current_value": user_stats.get(best_stat, 0),
        "recommendation": f"Focus on {best_stat} to reach Pro tier",
        "xp_gain_estimate": int(best_gain / 10),
    }