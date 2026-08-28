"""IRONBOUND core module - fitness mathematics and configuration."""
from .config import Config
from .db import get_db, get_db_connection
from .fitness_math import (
    epley_1rm,
    cooper_vo2max,
    navy_bodyfat,
    interpolate_tier,
    weakest_stat_recommendation,
    level_from_xp,
)
from ..models.gamification import compute_xp, get_rank, get_badges, weakest_stat_recommendation