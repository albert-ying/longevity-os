#!/usr/bin/env python3
"""
TaiYiYuan (太医院) — Demo Data Generator

Generates 90 days (2026-01-01 to 2026-03-31) of realistic health-optimization
data for the Longevity OS dashboard demo.

Story arc:
  Phase 1 (Jan): Baseline — moderate diet, inconsistent exercise, avg sleep ~6.5h
  Phase 2 (Feb): Intervention — high protein, strength 4x/week, creatine, sleep ~7.2h
  Phase 3 (Mar): Full optimization — diet dialed in, consistent, added supps, sleep ~7.5h

Usage:
    python scripts/generate_demo_data.py               # Reset DB, then seed data
    python scripts/generate_demo_data.py --skip-reset  # Seed into an already-reset DB
"""

import argparse
import json
import math
import os
import random
import sqlite3
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paths import describe_runtime_paths, get_db_path, get_project_root

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = get_project_root()
DB_PATH = get_db_path()
SETUP_SCRIPT = REPO_ROOT / "scripts" / "setup.py"

# ---------------------------------------------------------------------------
# Deterministic seed for reproducibility
# ---------------------------------------------------------------------------
random.seed(42)

# ---------------------------------------------------------------------------
# Date ranges
# ---------------------------------------------------------------------------
START_DATE = date(2026, 1, 1)
END_DATE = date(2026, 3, 31)
PHASE_1_END = date(2026, 1, 31)
PHASE_2_END = date(2026, 2, 28)

ALL_DAYS = []
d = START_DATE
while d <= END_DATE:
    ALL_DAYS.append(d)
    d += timedelta(days=1)


def day_index(d: date) -> int:
    return (d - START_DATE).days


def phase(d: date) -> int:
    if d <= PHASE_1_END:
        return 1
    elif d <= PHASE_2_END:
        return 2
    return 3


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def ts(d: date, hour: int = 8, minute: int = 0) -> str:
    """Create ISO 8601 timestamp string (UTC-8 Pacific)."""
    dt = datetime(d.year, d.month, d.day, hour, minute, 0, tzinfo=timezone(timedelta(hours=-8)))
    return dt.isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Noise helpers
# ---------------------------------------------------------------------------
def noisy(base: float, std: float, lo: float = None, hi: float = None) -> float:
    v = base + random.gauss(0, std)
    if lo is not None:
        v = max(v, lo)
    if hi is not None:
        v = min(v, hi)
    return round(v, 2)


def smooth_transition(day_idx: int, total_days: int, start_val: float, end_val: float) -> float:
    """Smooth sigmoidal transition between start and end values."""
    t = day_idx / max(total_days - 1, 1)
    # Sigmoid-ish
    s = 1 / (1 + math.exp(-10 * (t - 0.5)))
    return start_val + (end_val - start_val) * s


def phased_value(d: date, p1: float, p2: float, p3: float, noise_std: float = 0) -> float:
    """Get value based on phase with smooth transitions and optional noise."""
    di = day_index(d)
    if phase(d) == 1:
        base = smooth_transition(di, 31, p1, p1)
    elif phase(d) == 2:
        local_di = (d - date(2026, 2, 1)).days
        base = smooth_transition(local_di, 28, p1, p2)
    else:
        local_di = (d - date(2026, 3, 1)).days
        base = smooth_transition(local_di, 31, p2, p3)
    if noise_std > 0:
        base = noisy(base, noise_std)
    return round(base, 2)


# ===========================================================================
# MEAL DATABASE — realistic Chinese and Western meals with full nutrition
# ===========================================================================

def _ing(name, amount_g, cal, pro, carb, fat, fiber=0, **micros):
    """Shorthand for an ingredient dict."""
    d = {
        "ingredient_name": name,
        "normalized_name": name.lower().replace(" ", "_"),
        "amount_g": amount_g,
        "calories": cal,
        "protein_g": pro,
        "carbs_g": carb,
        "fat_g": fat,
        "fiber_g": fiber,
    }
    d.update(micros)
    return d


# --- Breakfasts ---
BREAKFASTS_P1 = [
    ("燕麦粥 with boiled eggs", [
        _ing("Oatmeal", 80, 303, 10.7, 54, 5.3, 8.2, iron_mg=3.4, magnesium_mg=138),
        _ing("Whole milk", 120, 73, 3.8, 5.8, 3.7, 0, calcium_mg=144),
        _ing("Boiled egg", 100, 155, 12.6, 1.1, 10.6, 0, vitamin_b12_mcg=1.1, iron_mg=1.8),
    ]),
    ("Toast with avocado and egg", [
        _ing("Whole wheat toast", 60, 159, 5.9, 26.7, 2.9, 3.6, iron_mg=1.7),
        _ing("Avocado", 70, 112, 1.4, 5.9, 10.3, 4.7, potassium_mg=340, vitamin_k_mcg=14.6),
        _ing("Fried egg", 50, 90, 6.3, 0.4, 7.0, 0, vitamin_b12_mcg=0.6),
    ]),
    ("豆浆油条", [
        _ing("Soy milk (豆浆)", 300, 126, 10.8, 9.0, 5.4, 1.5, calcium_mg=75, iron_mg=2.4),
        _ing("Youtiao (油条)", 80, 312, 6.4, 36.8, 15.2, 0.8, sodium_mg=480),
    ]),
    ("Yogurt with granola and berries", [
        _ing("Greek yogurt", 170, 100, 17.0, 6.0, 0.7, 0, calcium_mg=187, vitamin_b12_mcg=1.3),
        _ing("Granola", 45, 195, 4.5, 28.8, 7.2, 2.7, iron_mg=1.5),
        _ing("Mixed berries", 80, 38, 0.6, 9.0, 0.3, 2.0, vitamin_c_mg=14),
    ]),
    ("Congee with pork floss (皮蛋瘦肉粥)", [
        _ing("Rice congee", 300, 138, 2.4, 30.6, 0.3, 0.3),
        _ing("Lean pork", 40, 58, 9.6, 0, 2.0, 0, zinc_mg=1.3, vitamin_b12_mcg=0.3),
        _ing("Century egg (皮蛋)", 35, 57, 4.5, 0.5, 4.2, 0, iron_mg=0.7),
    ]),
    ("Banana protein smoothie", [
        _ing("Banana", 120, 107, 1.3, 27.0, 0.4, 3.1, potassium_mg=422, vitamin_b6_mg=0.4),
        _ing("Whey protein powder", 30, 117, 24.0, 3.0, 1.5, 0, calcium_mg=120),
        _ing("Whole milk", 200, 122, 6.4, 9.7, 6.2, 0, calcium_mg=240),
    ]),
]

BREAKFASTS_P2P3 = BREAKFASTS_P1 + [
    ("High-protein oatmeal with whey", [
        _ing("Oatmeal", 80, 303, 10.7, 54, 5.3, 8.2, iron_mg=3.4),
        _ing("Whey protein powder", 30, 117, 24.0, 3.0, 1.5, 0),
        _ing("Banana", 60, 53, 0.7, 13.5, 0.2, 1.6, potassium_mg=211),
        _ing("Almond butter", 15, 92, 3.2, 3.1, 7.8, 0.6, magnesium_mg=38),
    ]),
    ("Egg white omelette with spinach", [
        _ing("Egg whites", 150, 78, 16.2, 1.1, 0.3, 0, sodium_mg=250),
        _ing("Spinach", 60, 14, 1.7, 2.2, 0.2, 1.3, iron_mg=1.6, vitamin_k_mcg=290),
        _ing("Feta cheese", 30, 79, 4.3, 1.2, 6.4, 0, calcium_mg=140),
        _ing("Whole wheat toast", 60, 159, 5.9, 26.7, 2.9, 3.6),
    ]),
]

# --- Lunches ---
LUNCHES_BASE = [
    ("红烧肉 with rice", [
        _ing("Pork belly (红烧肉)", 120, 318, 14.4, 8.4, 25.2, 0, zinc_mg=2.4, iron_mg=1.2),
        _ing("White rice", 200, 260, 4.4, 57.2, 0.6, 0.8),
        _ing("Bok choy", 80, 10, 1.1, 1.5, 0.1, 0.8, vitamin_c_mg=30, calcium_mg=74),
    ]),
    ("Salmon poke bowl", [
        _ing("Atlantic salmon", 120, 250, 25.2, 0, 16.2, 0, vitamin_d_mcg=10.9, vitamin_b12_mcg=3.2),
        _ing("Sushi rice", 180, 234, 3.6, 52.2, 0.4, 0.7),
        _ing("Edamame", 50, 60, 5.5, 4.5, 2.5, 2.5, iron_mg=1.1),
        _ing("Avocado", 40, 64, 0.8, 3.4, 5.9, 2.7),
        _ing("Seaweed", 5, 2, 0.3, 0.3, 0, 0.2, iron_mg=0.2),
    ]),
    ("Chicken stir-fry with vegetables", [
        _ing("Chicken breast", 150, 231, 34.5, 0, 10.1, 0, vitamin_b6_mg=0.8, zinc_mg=1.4),
        _ing("Mixed vegetables (bell pepper, broccoli, carrot)", 150, 41, 2.6, 7.8, 0.3, 2.7, vitamin_c_mg=60),
        _ing("White rice", 180, 234, 4.0, 51.5, 0.5, 0.7),
        _ing("Cooking oil", 10, 88, 0, 0, 10.0, 0),
    ]),
    ("麻婆豆腐 with rice", [
        _ing("Firm tofu", 200, 152, 16.4, 4.2, 8.6, 0.6, calcium_mg=350, iron_mg=2.7),
        _ing("Ground pork", 60, 146, 9.6, 0, 11.8, 0, zinc_mg=1.8),
        _ing("White rice", 200, 260, 4.4, 57.2, 0.6, 0.8),
        _ing("Doubanjiang sauce", 15, 12, 0.7, 1.8, 0.3, 0.3, sodium_mg=760),
    ]),
    ("Burrito bowl (Chipotle-style)", [
        _ing("Chicken thigh, grilled", 130, 215, 26.0, 0, 12.0, 0, iron_mg=1.3),
        _ing("Brown rice", 150, 165, 3.5, 34.5, 1.4, 1.8),
        _ing("Black beans", 80, 92, 6.1, 16.4, 0.4, 6.0, iron_mg=1.8, magnesium_mg=48),
        _ing("Salsa", 40, 7, 0.3, 1.4, 0, 0.4, vitamin_c_mg=4),
        _ing("Sour cream", 25, 49, 0.6, 1.0, 4.8, 0),
    ]),
    ("Wonton soup (馄饨)", [
        _ing("Pork wontons", 150, 225, 12.0, 24.0, 9.0, 0.6, sodium_mg=640),
        _ing("Wonton broth", 300, 30, 3.0, 1.5, 1.2, 0),
        _ing("Bok choy", 50, 6.5, 0.7, 0.9, 0.1, 0.5, vitamin_c_mg=18),
    ]),
    ("Turkey club sandwich", [
        _ing("Turkey breast", 100, 104, 20.6, 0, 2.0, 0, zinc_mg=1.8),
        _ing("Whole wheat bread", 80, 212, 7.9, 35.6, 3.9, 4.8),
        _ing("Lettuce, tomato, mayo", 50, 70, 0.4, 1.2, 7.0, 0.3),
        _ing("Swiss cheese", 25, 95, 6.7, 0.4, 7.4, 0, calcium_mg=223),
    ]),
]

LUNCHES_HIGH_PROTEIN = LUNCHES_BASE + [
    ("Grilled chicken breast salad", [
        _ing("Chicken breast, grilled", 200, 308, 46.0, 0, 13.4, 0, vitamin_b6_mg=1.1),
        _ing("Mixed greens", 100, 18, 1.8, 2.6, 0.3, 1.6, vitamin_k_mcg=100),
        _ing("Quinoa", 80, 96, 3.5, 17.1, 1.5, 2.2, iron_mg=1.2, magnesium_mg=50),
        _ing("Olive oil dressing", 15, 119, 0, 0, 13.5, 0, vitamin_e_mg=2.0),
    ]),
    ("Beef and broccoli", [
        _ing("Beef sirloin", 150, 264, 36.0, 0, 12.6, 0, iron_mg=3.6, zinc_mg=5.7, vitamin_b12_mcg=3.0),
        _ing("Broccoli", 120, 41, 3.4, 7.9, 0.4, 3.1, vitamin_c_mg=108, vitamin_k_mcg=125),
        _ing("Brown rice", 150, 165, 3.5, 34.5, 1.4, 1.8),
        _ing("Soy sauce & garlic", 15, 8, 1.0, 1.0, 0, 0, sodium_mg=560),
    ]),
]

# --- Dinners ---
DINNERS_BASE = [
    ("Grilled chicken with roasted vegetables", [
        _ing("Chicken breast, grilled", 160, 246, 36.8, 0, 10.7, 0, vitamin_b6_mg=0.9),
        _ing("Roasted broccoli", 100, 55, 3.7, 11.1, 0.6, 5.1, vitamin_c_mg=89),
        _ing("Sweet potato", 120, 103, 1.9, 24.0, 0.1, 3.6, vitamin_a_mcg=960, potassium_mg=396),
        _ing("Olive oil", 10, 88, 0, 0, 10.0, 0, vitamin_e_mg=1.9),
    ]),
    ("水煮鱼 (Sichuan boiled fish)", [
        _ing("White fish fillet (bass)", 180, 183, 34.2, 0, 4.3, 0, vitamin_b12_mcg=2.1),
        _ing("Bean sprouts & cabbage", 100, 23, 2.4, 3.6, 0.2, 1.1, vitamin_c_mg=12),
        _ing("Chili oil & Sichuan pepper", 20, 162, 0, 0.6, 18.0, 0.2),
        _ing("White rice", 180, 234, 4.0, 51.5, 0.5, 0.7),
    ]),
    ("Pasta with meat sauce", [
        _ing("Spaghetti", 120, 220, 7.7, 43.2, 1.3, 2.5, iron_mg=1.8),
        _ing("Beef bolognese sauce", 150, 180, 15.0, 8.4, 9.6, 1.8, vitamin_b12_mcg=1.8, iron_mg=2.4),
        _ing("Parmesan cheese", 15, 59, 5.4, 0.5, 3.9, 0, calcium_mg=167),
    ]),
    ("Steak with salad", [
        _ing("Ribeye steak", 200, 500, 40.0, 0, 37.0, 0, iron_mg=4.2, zinc_mg=8.0, vitamin_b12_mcg=4.8),
        _ing("Caesar salad", 120, 95, 3.6, 5.4, 7.0, 1.4, vitamin_k_mcg=55),
    ]),
    ("番茄炒蛋 with rice", [
        _ing("Eggs", 100, 155, 12.6, 1.1, 10.6, 0, vitamin_b12_mcg=1.1),
        _ing("Tomatoes", 150, 27, 1.3, 5.8, 0.3, 1.8, vitamin_c_mg=21, potassium_mg=356),
        _ing("Cooking oil", 15, 132, 0, 0, 15.0, 0),
        _ing("White rice", 200, 260, 4.4, 57.2, 0.6, 0.8),
    ]),
    ("Korean bibimbap", [
        _ing("White rice", 200, 260, 4.4, 57.2, 0.6, 0.8),
        _ing("Ground beef", 80, 152, 12.8, 0, 10.8, 0, iron_mg=2.0, zinc_mg=3.5),
        _ing("Spinach & bean sprouts", 80, 18, 2.0, 2.6, 0.2, 1.4, iron_mg=1.0),
        _ing("Fried egg", 50, 90, 6.3, 0.4, 7.0, 0),
        _ing("Gochujang sauce", 15, 18, 0.5, 3.6, 0.2, 0.2, sodium_mg=320),
    ]),
    ("清蒸鱼 (steamed fish) with greens", [
        _ing("Sea bass, steamed", 180, 183, 34.2, 0, 4.3, 0, vitamin_b12_mcg=2.1),
        _ing("Ginger-scallion oil", 15, 120, 0.2, 0.6, 13.5, 0),
        _ing("Chinese greens (菜心)", 100, 16, 1.6, 2.4, 0.2, 1.2, vitamin_c_mg=28, calcium_mg=90),
        _ing("White rice", 180, 234, 4.0, 51.5, 0.5, 0.7),
    ]),
]

DINNERS_HIGH_PROTEIN = DINNERS_BASE + [
    ("Grilled salmon with asparagus", [
        _ing("Atlantic salmon, grilled", 180, 375, 37.8, 0, 24.3, 0, vitamin_d_mcg=16.3, vitamin_b12_mcg=4.8),
        _ing("Asparagus, roasted", 120, 24, 2.6, 4.4, 0.1, 2.5, vitamin_k_mcg=50, iron_mg=2.6),
        _ing("Quinoa", 100, 120, 4.4, 21.3, 1.9, 2.8, magnesium_mg=64),
        _ing("Lemon butter", 10, 72, 0.1, 0, 8.1, 0),
    ]),
    ("Turkey meatballs with zucchini noodles", [
        _ing("Turkey meatballs", 180, 252, 30.6, 4.5, 12.6, 0.3, zinc_mg=3.6),
        _ing("Zucchini noodles", 200, 34, 2.4, 6.4, 0.6, 2.0, potassium_mg=512, vitamin_c_mg=35),
        _ing("Marinara sauce", 80, 33, 1.2, 6.4, 0.4, 1.2, vitamin_a_mcg=40),
        _ing("Olive oil", 10, 88, 0, 0, 10.0, 0),
    ]),
]

# --- Snacks ---
SNACKS = [
    ("Protein shake", [
        _ing("Whey protein powder", 30, 117, 24.0, 3.0, 1.5, 0, calcium_mg=120),
        _ing("Whole milk", 250, 152, 8.0, 12.2, 7.7, 0, calcium_mg=300),
    ]),
    ("Mixed nuts", [
        _ing("Almonds, cashews, walnuts", 40, 228, 7.2, 8.4, 19.6, 2.4, magnesium_mg=80, vitamin_e_mg=7.4),
    ]),
    ("Apple with peanut butter", [
        _ing("Apple", 150, 78, 0.4, 20.7, 0.3, 3.6, vitamin_c_mg=7),
        _ing("Peanut butter", 20, 117, 5.0, 3.6, 9.9, 0.8, magnesium_mg=30),
    ]),
    ("Greek yogurt with honey", [
        _ing("Greek yogurt", 150, 88, 15.0, 5.3, 0.6, 0, calcium_mg=165, vitamin_b12_mcg=1.1),
        _ing("Honey", 10, 30, 0, 8.1, 0, 0),
    ]),
    ("Banana", [
        _ing("Banana", 120, 107, 1.3, 27.0, 0.4, 3.1, potassium_mg=422),
    ]),
    ("String cheese and crackers", [
        _ing("String cheese", 28, 80, 7.0, 0.6, 5.5, 0, calcium_mg=200),
        _ing("Whole wheat crackers", 30, 126, 3.0, 20.4, 3.6, 2.1),
    ]),
    ("Trail mix", [
        _ing("Trail mix (nuts, raisins, chocolate)", 50, 231, 5.0, 24.5, 13.5, 2.0, iron_mg=1.2),
    ]),
    ("Cottage cheese with pineapple", [
        _ing("Cottage cheese", 120, 98, 11.8, 3.4, 4.3, 0, calcium_mg=83),
        _ing("Pineapple chunks", 80, 40, 0.4, 10.4, 0.1, 1.1, vitamin_c_mg=38),
    ]),
]


# ===========================================================================
# EXERCISE DATABASE
# ===========================================================================

def _strength_session(name, exercises, duration=55, rpe=7, avg_hr=115, notes=""):
    return {
        "activity_type": "weightlifting",
        "duration_minutes": duration,
        "rpe": rpe,
        "avg_hr": avg_hr,
        "notes": notes or name,
        "details": exercises,
    }


def gen_push_day(d: date):
    """Push day (chest, shoulders, triceps)."""
    p = phase(d)
    # Progressive overload
    bp_base = 60 if p == 1 else 65 + (d - date(2026, 2, 1)).days * 0.18 if p == 2 else 72 + (d - date(2026, 3, 1)).days * 0.1
    ohp_base = 35 if p == 1 else 37.5 + (d - date(2026, 2, 1)).days * 0.1 if p == 2 else 42 + (d - date(2026, 3, 1)).days * 0.06
    bp_weight = round(noisy(min(bp_base, 80), 1.0, 55, 80), 1)
    ohp_weight = round(noisy(min(ohp_base, 52.5), 1.0, 30, 55), 1)
    return _strength_session("Push Day", [
        {"exercise_name": "Bench Press", "sets": 4, "reps": random.choice([6, 8, 8, 10]), "weight_kg": bp_weight},
        {"exercise_name": "Overhead Press", "sets": 3, "reps": random.choice([8, 10]), "weight_kg": ohp_weight},
        {"exercise_name": "Incline Dumbbell Press", "sets": 3, "reps": 10, "weight_kg": round(bp_weight * 0.35, 1)},
        {"exercise_name": "Lateral Raises", "sets": 3, "reps": 15, "weight_kg": round(noisy(10, 1, 7.5, 15), 1)},
        {"exercise_name": "Tricep Pushdowns", "sets": 3, "reps": 12, "weight_kg": round(noisy(20, 2, 15, 30), 1)},
    ], duration=random.randint(50, 65), rpe=random.randint(6, 8), avg_hr=random.randint(110, 130))


def gen_pull_day(d: date):
    """Pull day (back, biceps)."""
    p = phase(d)
    dl_base = 100 if p == 1 else 105 + (d - date(2026, 2, 1)).days * 0.3 if p == 2 else 117 + (d - date(2026, 3, 1)).days * 0.1
    row_base = 50 if p == 1 else 55 + (d - date(2026, 2, 1)).days * 0.12 if p == 2 else 60 + (d - date(2026, 3, 1)).days * 0.06
    dl_weight = round(noisy(min(dl_base, 125), 2.0, 90, 130), 1)
    row_weight = round(noisy(min(row_base, 70), 1.5, 45, 72.5), 1)
    return _strength_session("Pull Day", [
        {"exercise_name": "Deadlift", "sets": 4, "reps": random.choice([5, 5, 6, 6]), "weight_kg": dl_weight},
        {"exercise_name": "Barbell Row", "sets": 4, "reps": random.choice([8, 10]), "weight_kg": row_weight},
        {"exercise_name": "Lat Pulldown", "sets": 3, "reps": 10, "weight_kg": round(noisy(50, 3, 40, 65), 1)},
        {"exercise_name": "Face Pulls", "sets": 3, "reps": 15, "weight_kg": round(noisy(15, 2, 10, 22.5), 1)},
        {"exercise_name": "Barbell Curl", "sets": 3, "reps": 12, "weight_kg": round(noisy(20, 1.5, 15, 27.5), 1)},
    ], duration=random.randint(50, 65), rpe=random.randint(6, 9), avg_hr=random.randint(115, 135))


def gen_leg_day(d: date):
    """Leg day (quads, hams, glutes)."""
    p = phase(d)
    sq_base = 80 if p == 1 else 85 + (d - date(2026, 2, 1)).days * 0.25 if p == 2 else 95 + (d - date(2026, 3, 1)).days * 0.16
    sq_weight = round(noisy(min(sq_base, 105), 2.0, 70, 110), 1)
    return _strength_session("Leg Day", [
        {"exercise_name": "Barbell Squat", "sets": 4, "reps": random.choice([6, 8, 8]), "weight_kg": sq_weight},
        {"exercise_name": "Romanian Deadlift", "sets": 3, "reps": 10, "weight_kg": round(sq_weight * 0.7, 1)},
        {"exercise_name": "Leg Press", "sets": 3, "reps": 12, "weight_kg": round(sq_weight * 1.6, 1)},
        {"exercise_name": "Walking Lunges", "sets": 3, "reps": 12, "weight_kg": round(noisy(16, 2, 10, 24), 1)},
        {"exercise_name": "Calf Raises", "sets": 4, "reps": 15, "weight_kg": round(noisy(40, 3, 30, 55), 1)},
    ], duration=random.randint(55, 70), rpe=random.randint(7, 9), avg_hr=random.randint(120, 145))


def gen_run(d: date):
    """Running session with improving pace."""
    p = phase(d)
    # Pace improvement: 6:00/km -> 5:30/km -> 5:10/km
    pace_sec = phased_value(d, 360, 330, 310, noise_std=15)  # sec/km
    distance = noisy(5.0 if not is_weekend(d) else 7.0, 0.5, 3.0, 10.0)
    duration = round(distance * pace_sec / 60, 1)
    avg_hr = round(noisy(phased_value(d, 160, 155, 148), 4, 135, 175))
    return {
        "activity_type": "running",
        "duration_minutes": duration,
        "distance_km": round(distance, 2),
        "rpe": random.randint(5, 8),
        "avg_hr": avg_hr,
        "notes": f"{'Morning' if random.random() < 0.6 else 'Evening'} run, {round(pace_sec/60)}:{int(pace_sec%60):02d}/km pace",
        "details": [
            {"exercise_name": "Running", "duration_seconds": round(duration * 60), "notes": f"{round(distance, 1)}km"},
        ],
    }


def gen_walk(d: date):
    """Walking session."""
    duration = random.randint(25, 50)
    distance = round(duration / 60 * noisy(5.5, 0.3, 4.5, 6.5), 2)
    return {
        "activity_type": "walking",
        "duration_minutes": duration,
        "distance_km": distance,
        "rpe": random.randint(2, 4),
        "avg_hr": random.randint(85, 105),
        "notes": "Afternoon walk around campus",
        "details": [],
    }


def gen_yoga(d: date):
    """Yoga/flexibility session."""
    return {
        "activity_type": "flexibility",
        "duration_minutes": random.randint(30, 50),
        "rpe": random.randint(3, 5),
        "avg_hr": random.randint(75, 95),
        "notes": "Yoga — focus on hip openers and hamstrings",
        "details": [
            {"exercise_name": "Sun Salutations", "sets": 3, "reps": 5},
            {"exercise_name": "Pigeon Pose", "duration_seconds": 120},
            {"exercise_name": "Forward Fold", "duration_seconds": 90},
        ],
    }


# ===========================================================================
# SCHEDULE GENERATORS
# ===========================================================================

def plan_exercise_schedule(d: date) -> list:
    """Return list of exercise sessions for a given day."""
    p = phase(d)
    dow = d.weekday()  # 0=Mon, 6=Sun

    # Occasional skip days (more in P1)
    skip_prob = 0.15 if p == 1 else 0.05 if p == 2 else 0.03
    if random.random() < skip_prob:
        return []

    if p == 1:
        # Phase 1: 2-3x/week, mostly cardio, occasional strength
        # Mon, Wed, Sat are exercise days (maybe)
        if dow == 0:
            return [gen_run(d)] if random.random() < 0.7 else []
        elif dow == 2:
            return [gen_push_day(d)] if random.random() < 0.5 else [gen_walk(d)]
        elif dow == 4:
            return [gen_run(d)] if random.random() < 0.6 else []
        elif dow == 5:
            return [gen_walk(d)] if random.random() < 0.5 else []
        elif dow == 6:
            return [gen_run(d)] if random.random() < 0.4 else [gen_walk(d)] if random.random() < 0.5 else []
        return []
    elif p == 2:
        # Phase 2: 4x strength (Mon push, Tue pull, Thu legs, Fri push/pull), 2x cardio (Wed, Sat)
        if dow == 0:
            return [gen_push_day(d)]
        elif dow == 1:
            return [gen_pull_day(d)]
        elif dow == 2:
            return [gen_run(d)]
        elif dow == 3:
            return [gen_leg_day(d)]
        elif dow == 4:
            return [gen_push_day(d)] if random.random() < 0.5 else [gen_pull_day(d)]
        elif dow == 5:
            return [gen_run(d)]
        elif dow == 6:
            return [gen_walk(d)] if random.random() < 0.4 else []
        return []
    else:
        # Phase 3: 4x strength, 2x cardio, 1x flexibility
        if dow == 0:
            return [gen_push_day(d)]
        elif dow == 1:
            return [gen_pull_day(d)]
        elif dow == 2:
            return [gen_run(d)]
        elif dow == 3:
            return [gen_leg_day(d)]
        elif dow == 4:
            return [gen_push_day(d)] if random.random() < 0.5 else [gen_pull_day(d)]
        elif dow == 5:
            return [gen_run(d)]
        elif dow == 6:
            return [gen_yoga(d)] if random.random() < 0.7 else [gen_walk(d)]
        return []


def plan_meals(d: date) -> list:
    """Return list of (meal_type, description, ingredients) for a given day."""
    p = phase(d)
    meals = []
    weekend = is_weekend(d)

    # Breakfast — scale portions to hit calorie/protein targets per phase
    # P1: ~1800-2200 cal, ~90g protein | P2: ~2100-2500 cal, ~125g protein | P3: ~2100-2600 cal, ~130g protein
    pool = BREAKFASTS_P2P3 if p >= 2 else BREAKFASTS_P1
    b_name, b_ings = random.choice(pool)
    scale = 1.15 if p == 1 else 1.35 if p == 2 else 1.38
    scaled_ings = _scale_ingredients(b_ings, noisy(scale, 0.08, 0.85, 1.6))
    meals.append(("breakfast", b_name, scaled_ings))

    # Lunch
    pool = LUNCHES_HIGH_PROTEIN if p >= 2 else LUNCHES_BASE
    l_name, l_ings = random.choice(pool)
    scale = 1.15 if p == 1 else 1.35 if p == 2 else 1.4
    if weekend:
        scale *= noisy(1.12, 0.05, 1.0, 1.25)  # Eat more on weekends
    scaled_ings = _scale_ingredients(l_ings, noisy(scale, 0.08, 0.85, 1.6))
    meals.append(("lunch", l_name, scaled_ings))

    # Dinner
    pool = DINNERS_HIGH_PROTEIN if p >= 2 else DINNERS_BASE
    d_name, d_ings = random.choice(pool)
    scale = 1.15 if p == 1 else 1.35 if p == 2 else 1.35
    if weekend:
        scale *= noisy(1.15, 0.05, 1.0, 1.3)
    scaled_ings = _scale_ingredients(d_ings, noisy(scale, 0.08, 0.85, 1.6))
    meals.append(("dinner", d_name, scaled_ings))

    # Snack (more likely in P2/P3 for protein; more junk-ish on weekends in P1)
    snack_prob = 0.4 if p == 1 else 0.8 if p == 2 else 0.85
    if random.random() < snack_prob:
        s_name, s_ings = random.choice(SNACKS)
        snack_scale = 0.9 if p == 1 else 1.1
        meals.append(("snack", s_name, _scale_ingredients(s_ings, noisy(snack_scale, 0.1, 0.7, 1.4))))

    # Occasional second snack in P2/P3 (protein focused)
    if p >= 2 and random.random() < 0.45:
        s_name, s_ings = random.choice(SNACKS[:4])  # Prefer protein snacks
        meals.append(("snack", s_name, _scale_ingredients(s_ings, noisy(1.0, 0.1, 0.7, 1.3))))

    return meals


def _scale_ingredients(ingredients: list[dict], factor: float) -> list[dict]:
    """Scale ingredient amounts and nutrients by a factor."""
    scaled = []
    for ing in ingredients:
        new = dict(ing)
        for key in ["amount_g", "calories", "protein_g", "carbs_g", "fat_g", "fiber_g",
                     "vitamin_a_mcg", "vitamin_b1_mg", "vitamin_b2_mg", "vitamin_b3_mg",
                     "vitamin_b5_mg", "vitamin_b6_mg", "vitamin_b7_mcg", "vitamin_b9_mcg",
                     "vitamin_b12_mcg", "vitamin_c_mg", "vitamin_d_mcg", "vitamin_e_mg",
                     "vitamin_k_mcg", "calcium_mg", "iron_mg", "magnesium_mg",
                     "zinc_mg", "potassium_mg", "sodium_mg"]:
            if key in new and new[key] is not None:
                new[key] = round(new[key] * factor, 2)
        scaled.append(new)
    return scaled


# ===========================================================================
# BODY METRICS generators
# ===========================================================================

def gen_body_metrics(d: date) -> list[tuple]:
    """Return list of (metric_type, value, unit, context, device) for a day."""
    metrics = []
    di = day_index(d)

    # Weight (daily, morning fasted) — 75 -> 73.5 -> 72 with noise
    # Linear trend with daily noise and slight weekend bump
    weight_base = phased_value(d, 75.0, 73.5, 72.0)
    weekend_bump = 0.3 if is_weekend(d) else 0
    weight = noisy(weight_base + weekend_bump, 0.3, 69.0, 77.0)
    metrics.append(("weight", weight, "kg", "morning fasted", "Withings Body+"))

    # Resting HR (daily) — 68 -> 62 -> 58
    rhr = round(noisy(phased_value(d, 68, 62, 58), 2.5, 50, 78))
    metrics.append(("resting_hr", rhr, "bpm", "morning waking", "Apple Watch"))

    # HRV (daily) — 35 -> 45 -> 55
    hrv = round(noisy(phased_value(d, 35, 45, 55), 5, 15, 80))
    metrics.append(("hrv", hrv, "ms", "morning waking", "Apple Watch"))

    # Sleep duration — 6.5 -> 7.2 -> 7.5 with weekend noise
    sleep_base = phased_value(d, 6.5, 7.2, 7.5)
    weekend_sleep_bonus = 0.5 if is_weekend(d) else 0
    # Friday/Saturday nights tend to be later
    if d.weekday() in (4, 5):
        weekend_sleep_bonus -= 0.3  # Stay up late
    sleep_dur = noisy(sleep_base + weekend_sleep_bonus, 0.4, 4.5, 9.5)
    metrics.append(("sleep_duration", round(sleep_dur, 1), "hours", "night sleep", "Apple Watch"))

    # Sleep quality (1-10) — 5.5 -> 7 -> 8
    sq_base = phased_value(d, 5.5, 7.0, 8.0)
    sleep_quality = noisy(sq_base, 0.8, 2, 10)
    metrics.append(("sleep_quality", round(sleep_quality, 1), "score", "subjective rating", "manual"))

    # Blood pressure — 128/82 -> 122/78 -> 118/75 (not every day — ~3x/week)
    if random.random() < 0.4 or d.weekday() == 0:  # Always on Monday
        sys_bp = round(noisy(phased_value(d, 128, 122, 118), 4, 105, 145))
        dia_bp = round(noisy(phased_value(d, 82, 78, 75), 3, 60, 95))
        metrics.append(("blood_pressure_sys", sys_bp, "mmHg", "morning seated", "Withings BPM"))
        metrics.append(("blood_pressure_dia", dia_bp, "mmHg", "morning seated", "Withings BPM"))

    # Fasting glucose — occasional (every 1-2 weeks)
    if d.day in (1, 5, 12, 15, 20, 25) or (d.weekday() == 0 and random.random() < 0.15):
        fg_base = phased_value(d, 95, 91, 88)
        fg = round(noisy(fg_base, 3, 72, 110))
        metrics.append(("fasting_glucose", fg, "mg/dL", "morning fasted", "Keto-Mojo"))

    # Body fat % — occasional (weekly)
    if d.weekday() == 0:  # Every Monday
        bf_base = phased_value(d, 18.5, 17.2, 16.0)
        bf = noisy(bf_base, 0.5, 12, 25)
        metrics.append(("body_fat", bf, "%", "morning fasted", "Withings Body+"))

    return metrics


# ===========================================================================
# BIOMARKERS
# ===========================================================================

def gen_biomarker_panels() -> list[tuple]:
    """Return (date, panel_name, markers_list) for the 3 lab panels."""
    panels = []

    # Jan 5 — Baseline
    jan5_markers = [
        # Lipid Panel
        ("Lipid Panel", "Total Cholesterol", 215, "mg/dL", 125, 200, 140, 180, None),
        ("Lipid Panel", "LDL Cholesterol", 135, "mg/dL", 0, 130, 0, 100, None),
        ("Lipid Panel", "HDL Cholesterol", 52, "mg/dL", 40, None, 60, None, None),
        ("Lipid Panel", "Triglycerides", 140, "mg/dL", 0, 150, 0, 100, None),
        # Inflammation
        ("Inflammation", "CRP (hs-CRP)", 2.1, "mg/L", 0, 3.0, 0, 1.0, "Slightly elevated"),
        ("Inflammation", "Homocysteine", 11.2, "µmol/L", 5, 15, 5, 9, None),
        # Metabolic
        ("Metabolic", "Fasting Glucose", 96, "mg/dL", 65, 100, 70, 90, None),
        ("Metabolic", "HbA1c", 5.4, "%", 4.0, 5.7, 4.0, 5.2, None),
        ("Metabolic", "Fasting Insulin", 8.2, "µIU/mL", 2.6, 24.9, 2.6, 8.0, None),
        # Thyroid
        ("Thyroid", "TSH", 2.1, "mIU/L", 0.4, 4.0, 0.5, 2.5, None),
        ("Thyroid", "Free T4", 1.2, "ng/dL", 0.8, 1.8, 1.0, 1.6, None),
        # Vitamins/Minerals
        ("Vitamins", "Vitamin D (25-OH)", 22, "ng/mL", 30, 100, 40, 60, "Deficient"),
        ("Vitamins", "Vitamin B12", 480, "pg/mL", 200, 900, 400, 800, None),
        ("Vitamins", "Ferritin", 85, "ng/mL", 12, 300, 40, 150, None),
        ("Vitamins", "Folate", 14, "ng/mL", 3, 20, 8, 20, None),
        # CBC
        ("CBC", "WBC", 6.2, "10^3/µL", 4.5, 11.0, 4.5, 8.0, None),
        ("CBC", "RBC", 5.1, "10^6/µL", 4.5, 5.5, 4.5, 5.5, None),
        ("CBC", "Hemoglobin", 15.2, "g/dL", 13.5, 17.5, 14.0, 16.0, None),
        ("CBC", "Hematocrit", 44.5, "%", 38.3, 48.6, 40, 48, None),
        # Liver
        ("Liver", "ALT", 22, "U/L", 7, 56, 7, 30, None),
        ("Liver", "AST", 19, "U/L", 10, 40, 10, 30, None),
        ("Liver", "GGT", 18, "U/L", 0, 65, 0, 30, None),
        # Kidney
        ("Kidney", "Creatinine", 0.95, "mg/dL", 0.74, 1.35, 0.7, 1.1, None),
        ("Kidney", "BUN", 14, "mg/dL", 6, 20, 7, 18, None),
        ("Kidney", "eGFR", 112, "mL/min/1.73m²", 90, None, 90, None, None),
        # Hormones
        ("Hormones", "Testosterone (Total)", 620, "ng/dL", 264, 916, 500, 800, None),
        ("Hormones", "SHBG", 38, "nmol/L", 10, 57, 20, 50, None),
        ("Hormones", "Cortisol (AM)", 14.5, "µg/dL", 6, 23, 8, 15, None),
    ]
    panels.append((date(2026, 1, 5), jan5_markers))

    # Feb 15 — Mid-intervention
    feb15_markers = [
        ("Lipid Panel", "Total Cholesterol", 198, "mg/dL", 125, 200, 140, 180, None),
        ("Lipid Panel", "LDL Cholesterol", 120, "mg/dL", 0, 130, 0, 100, None),
        ("Lipid Panel", "HDL Cholesterol", 56, "mg/dL", 40, None, 60, None, None),
        ("Lipid Panel", "Triglycerides", 110, "mg/dL", 0, 150, 0, 100, None),
        ("Inflammation", "CRP (hs-CRP)", 1.5, "mg/L", 0, 3.0, 0, 1.0, "Improving"),
        ("Inflammation", "Homocysteine", 10.1, "µmol/L", 5, 15, 5, 9, None),
        ("Metabolic", "Fasting Glucose", 91, "mg/dL", 65, 100, 70, 90, None),
        ("Metabolic", "HbA1c", 5.3, "%", 4.0, 5.7, 4.0, 5.2, None),
        ("Metabolic", "Fasting Insulin", 6.8, "µIU/mL", 2.6, 24.9, 2.6, 8.0, None),
        ("Thyroid", "TSH", 1.9, "mIU/L", 0.4, 4.0, 0.5, 2.5, None),
        ("Thyroid", "Free T4", 1.3, "ng/dL", 0.8, 1.8, 1.0, 1.6, None),
        ("Vitamins", "Vitamin D (25-OH)", 35, "ng/mL", 30, 100, 40, 60, "Improved with supplementation"),
        ("Vitamins", "Vitamin B12", 520, "pg/mL", 200, 900, 400, 800, None),
        ("Vitamins", "Ferritin", 92, "ng/mL", 12, 300, 40, 150, None),
        ("CBC", "WBC", 5.8, "10^3/µL", 4.5, 11.0, 4.5, 8.0, None),
        ("CBC", "Hemoglobin", 15.5, "g/dL", 13.5, 17.5, 14.0, 16.0, None),
        ("Liver", "ALT", 20, "U/L", 7, 56, 7, 30, None),
        ("Liver", "AST", 21, "U/L", 10, 40, 10, 30, "Slightly up from creatine — normal"),
        ("Kidney", "Creatinine", 1.05, "mg/dL", 0.74, 1.35, 0.7, 1.1, "Creatine supplementation effect"),
        ("Kidney", "eGFR", 108, "mL/min/1.73m²", 90, None, 90, None, None),
        ("Hormones", "Testosterone (Total)", 665, "ng/dL", 264, 916, 500, 800, "Improving with exercise"),
    ]
    panels.append((date(2026, 2, 15), feb15_markers))

    # Mar 25 — Full optimization
    mar25_markers = [
        ("Lipid Panel", "Total Cholesterol", 185, "mg/dL", 125, 200, 140, 180, None),
        ("Lipid Panel", "LDL Cholesterol", 105, "mg/dL", 0, 130, 0, 100, "Now within optimal range"),
        ("Lipid Panel", "HDL Cholesterol", 62, "mg/dL", 40, None, 60, None, "Above optimal threshold"),
        ("Lipid Panel", "Triglycerides", 90, "mg/dL", 0, 150, 0, 100, "Excellent"),
        ("Inflammation", "CRP (hs-CRP)", 0.8, "mg/L", 0, 3.0, 0, 1.0, "Within optimal range"),
        ("Inflammation", "Homocysteine", 8.8, "µmol/L", 5, 15, 5, 9, "Now within optimal range"),
        ("Metabolic", "Fasting Glucose", 87, "mg/dL", 65, 100, 70, 90, "Within optimal range"),
        ("Metabolic", "HbA1c", 5.1, "%", 4.0, 5.7, 4.0, 5.2, "Now within optimal range"),
        ("Metabolic", "Fasting Insulin", 5.5, "µIU/mL", 2.6, 24.9, 2.6, 8.0, "Excellent insulin sensitivity"),
        ("Metabolic", "HOMA-IR", 1.18, "", 0, 2.5, 0, 1.5, "Calculated: glucose*insulin/405"),
        ("Thyroid", "TSH", 1.8, "mIU/L", 0.4, 4.0, 0.5, 2.5, None),
        ("Vitamins", "Vitamin D (25-OH)", 48, "ng/mL", 30, 100, 40, 60, "Within optimal range with supplementation"),
        ("Vitamins", "Vitamin B12", 555, "pg/mL", 200, 900, 400, 800, None),
        ("Vitamins", "Ferritin", 98, "ng/mL", 12, 300, 40, 150, None),
        ("Vitamins", "Omega-3 Index", 6.8, "%", 4, 12, 8, 12, "Improving since starting omega-3"),
        ("Inflammation", "IL-6", 1.2, "pg/mL", 0, 5, 0, 2, None),
        ("CBC", "WBC", 5.5, "10^3/µL", 4.5, 11.0, 4.5, 8.0, None),
        ("CBC", "Hemoglobin", 15.8, "g/dL", 13.5, 17.5, 14.0, 16.0, None),
        ("Liver", "ALT", 18, "U/L", 7, 56, 7, 30, None),
        ("Liver", "AST", 17, "U/L", 10, 40, 10, 30, None),
        ("Kidney", "Creatinine", 1.08, "mg/dL", 0.74, 1.35, 0.7, 1.1, "Stable on creatine"),
        ("Kidney", "eGFR", 106, "mL/min/1.73m²", 90, None, 90, None, None),
        ("Hormones", "Testosterone (Total)", 710, "ng/dL", 264, 916, 500, 800, "Significant improvement"),
        ("Hormones", "SHBG", 36, "nmol/L", 10, 57, 20, 50, None),
        ("Hormones", "IGF-1", 215, "ng/mL", 100, 360, 120, 250, None),
        ("Hormones", "DHEA-S", 380, "µg/dL", 211, 492, 250, 450, None),
    ]
    panels.append((date(2026, 3, 25), mar25_markers))

    return panels


# ===========================================================================
# SUPPLEMENTS
# ===========================================================================

SUPPLEMENTS = [
    # Phase 1: just multivitamin
    ("Multivitamin", 1, "tablet", "daily", "morning with breakfast", "2026-01-01", None, "General health", "Thorne Basic Nutrients"),
    # Phase 2 additions
    ("Creatine Monohydrate", 5, "g", "daily", "morning with water", "2026-02-01", None, "Strength & cognition", "Thorne Creatine"),
    ("Vitamin D3", 4000, "IU", "daily", "morning with breakfast", "2026-02-01", None, "Deficiency correction (baseline 22 ng/mL)", "Thorne D3"),
    # Phase 3 additions
    ("Omega-3 (EPA/DHA)", 2, "g", "daily", "morning with breakfast", "2026-03-01", None, "Inflammation reduction & cardiovascular", "Nordic Naturals Ultimate Omega"),
    ("Magnesium Glycinate", 400, "mg", "daily", "before bed", "2026-03-01", None, "Sleep quality & recovery", "Thorne Magnesium Bisglycinate"),
]


# ===========================================================================
# TRIALS
# ===========================================================================

def gen_trial_1_observations():
    """Protein-Sleep Quality Trial (Feb 1-28, ABA design)."""
    observations = []
    # Phase A (baseline): Feb 1-14 — normal dinner protein (~15-20g)
    for i in range(14):
        d = date(2026, 2, 1) + timedelta(days=i)
        sq = noisy(6.8, 0.7, 4, 10)
        protein = noisy(18, 4, 10, 25)
        compliance = noisy(0.95, 0.05, 0.7, 1.0)
        observations.append((d.isoformat(), "baseline", "sleep_quality", sq, compliance, f"Dinner protein: {protein:.0f}g"))
        observations.append((d.isoformat(), "baseline", "dinner_protein_g", protein, compliance, None))

    # Phase B (intervention): Feb 15-28 — high dinner protein (30-40g)
    for i in range(14):
        d = date(2026, 2, 15) + timedelta(days=i)
        sq = noisy(7.8, 0.6, 5, 10)
        protein = noisy(35, 4, 28, 45)
        compliance = noisy(0.90, 0.08, 0.6, 1.0)
        observations.append((d.isoformat(), "intervention", "sleep_quality", sq, compliance, f"Dinner protein: {protein:.0f}g"))
        observations.append((d.isoformat(), "intervention", "dinner_protein_g", protein, compliance, None))

    return observations


def gen_trial_2_observations():
    """Creatine-Cognition Trial (Mar 1 - ongoing, ABA design)."""
    observations = []
    # Currently in intervention phase (no baseline period yet in this simple demo)
    # Actually: baseline = Mar 1-7, intervention = Mar 8 onward

    # Phase A (baseline): Mar 1-7 — no creatine (well, already taking it, but the "cognition" test baseline)
    for i in range(7):
        d = date(2026, 3, 1) + timedelta(days=i)
        # Reaction time test (ms) — lower is better
        reaction_time = noisy(310, 15, 260, 380)
        # Working memory score (0-20)
        wm_score = noisy(14.5, 1.5, 10, 20)
        compliance = noisy(0.95, 0.04, 0.8, 1.0)
        observations.append((d.isoformat(), "baseline", "reaction_time_ms", reaction_time, compliance, "Dual N-back test"))
        observations.append((d.isoformat(), "baseline", "working_memory_score", wm_score, compliance, "Dual N-back test"))

    # Phase B (intervention): Mar 8 onward — taking creatine 5g daily with cognitive tests
    end = min(date(2026, 3, 31), END_DATE)
    d = date(2026, 3, 8)
    while d <= end:
        reaction_time = noisy(295, 12, 250, 360)  # Slightly better
        wm_score = noisy(15.5, 1.3, 10, 20)  # Slightly better
        compliance = noisy(0.92, 0.06, 0.65, 1.0)
        observations.append((d.isoformat(), "intervention", "reaction_time_ms", reaction_time, compliance, "Dual N-back test"))
        observations.append((d.isoformat(), "intervention", "working_memory_score", wm_score, compliance, "Dual N-back test"))
        d += timedelta(days=1)

    return observations


# ===========================================================================
# INSIGHTS
# ===========================================================================

INSIGHTS = [
    {
        "type": "correlation",
        "modules": ["body_metrics"],
        "description": "Sleep duration correlates positively with next-day HRV (r=0.42, p<0.01). Each additional hour of sleep is associated with +4.2 ms HRV.",
        "stats": {"r": 0.42, "p_value": 0.003, "n": 85, "method": "Pearson", "ci_95": [0.22, 0.58]},
        "effect_size": 0.42,
        "p_value": 0.003,
        "confidence": "high",
        "evidence": 3,
        "actionable": True,
        "trial_candidate": False,
    },
    {
        "type": "pattern",
        "modules": ["diet", "body_metrics"],
        "description": "Protein intake >100g/day associated with 15% better sleep quality scores (7.6 vs 6.6, p=0.02). Effect strongest when protein distributed across meals.",
        "stats": {"mean_high_protein": 7.6, "mean_low_protein": 6.6, "diff": 1.0, "p_value": 0.02, "n_high": 38, "n_low": 47},
        "effect_size": 0.65,
        "p_value": 0.02,
        "confidence": "medium",
        "evidence": 2,
        "actionable": True,
        "trial_candidate": True,
    },
    {
        "type": "trend",
        "modules": ["body_metrics"],
        "description": "Weight decreasing at -0.5 kg/week over last 30 days (p<0.001). Current trajectory: 72.0 kg by Mar 31 from 75.0 kg baseline.",
        "stats": {"slope_kg_per_week": -0.5, "r_squared": 0.78, "p_value": 0.0003, "n": 30, "projected_end": 72.0},
        "effect_size": None,
        "p_value": 0.0003,
        "confidence": "high",
        "evidence": 4,
        "actionable": False,
        "trial_candidate": False,
    },
    {
        "type": "trend",
        "modules": ["body_metrics"],
        "description": "Resting HR showing significant downward trend (-1.2 bpm/week, p<0.001). From 68 bpm baseline to current 58 bpm. Consistent with improving cardiovascular fitness.",
        "stats": {"slope_bpm_per_week": -1.2, "r_squared": 0.82, "p_value": 0.0001, "baseline": 68, "current": 58},
        "effect_size": None,
        "p_value": 0.0001,
        "confidence": "high",
        "evidence": 4,
        "actionable": False,
        "trial_candidate": False,
    },
    {
        "type": "pattern",
        "modules": ["exercise", "body_metrics"],
        "description": "Evening exercise (after 6pm) associated with 0.4h less sleep compared to morning exercise (p=0.04). N=24 evening vs N=38 morning sessions.",
        "stats": {"mean_sleep_evening": 6.9, "mean_sleep_morning": 7.3, "diff": -0.4, "p_value": 0.04, "n_evening": 24, "n_morning": 38},
        "effect_size": 0.38,
        "p_value": 0.04,
        "confidence": "medium",
        "evidence": 2,
        "actionable": True,
        "trial_candidate": True,
    },
    {
        "type": "correlation",
        "modules": ["supplements", "biomarkers"],
        "description": "Vitamin D supplementation period (Feb-Mar) shows 118% increase in serum 25(OH)D (22→48 ng/mL) and concurrent 62% reduction in hs-CRP (2.1→0.8 mg/L).",
        "stats": {"vit_d_baseline": 22, "vit_d_current": 48, "crp_baseline": 2.1, "crp_current": 0.8, "vit_d_change_pct": 118, "crp_change_pct": -62},
        "effect_size": 1.8,
        "p_value": None,
        "confidence": "medium",
        "evidence": 2,
        "actionable": False,
        "trial_candidate": False,
    },
    {
        "type": "pattern",
        "modules": ["exercise", "body_metrics"],
        "description": "Strength training days show +3.2 ms HRV the following morning vs rest days (p=0.03). Effect mediated by sleep quality improvement on training days.",
        "stats": {"hrv_after_training": 48.3, "hrv_rest_day": 45.1, "diff": 3.2, "p_value": 0.03, "n": 75},
        "effect_size": 0.33,
        "p_value": 0.03,
        "confidence": "medium",
        "evidence": 2,
        "actionable": True,
        "trial_candidate": False,
    },
    {
        "type": "anomaly",
        "modules": ["body_metrics"],
        "description": "HRV spike to 72 ms on Feb 22 (2.1 SD above rolling mean). Coincided with rest day after heavy squat session and 8.5h sleep. Recovery supercompensation pattern.",
        "stats": {"value": 72, "rolling_mean": 44, "rolling_std": 8.5, "z_score": 2.1, "date": "2026-02-22"},
        "effect_size": None,
        "p_value": None,
        "confidence": "low",
        "evidence": 1,
        "actionable": False,
        "trial_candidate": False,
    },
]


# ===========================================================================
# MAIN GENERATOR
# ===========================================================================

def parse_args():
    parser = argparse.ArgumentParser(
        description="Seed the Longevity OS demo database with deterministic data",
    )
    parser.add_argument(
        "--skip-reset",
        action="store_true",
        help="Assume the target database has already been reset and initialized",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    print("=" * 70)
    print("TaiYiYuan (太医院) — Demo Data Generator")
    print("=" * 70)
    print("\nResolved runtime paths:")
    for name, value in describe_runtime_paths().items():
        print(f"  {name:12s} {value}")

    # Step 1: Delete existing DB and re-initialize
    if args.skip_reset:
        print("\n1. Skipping database reset (--skip-reset).")
    else:
        print("\n1. Resetting database...")
        result = subprocess.run(
            [sys.executable, str(SETUP_SCRIPT), "--reset", "--confirm"],
            capture_output=True, text=True,
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"Setup failed: {result.stderr}")
            sys.exit(1)

    # Step 2: Connect directly to DB for bulk inserts (faster than using db.py API)
    print("\n2. Connecting to database...")
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")  # Faster for bulk inserts
    conn.row_factory = sqlite3.Row

    now = now_iso()
    counts = {}

    # -----------------------------------------------------------------------
    # DIET
    # -----------------------------------------------------------------------
    print("\n3. Generating diet data (90 days, 3-5 meals/day)...")
    meal_count = 0
    ingredient_count = 0
    total_daily_cals = []

    for d in ALL_DAYS:
        meals = plan_meals(d)
        day_cal = 0

        for meal_type, description, ingredients in meals:
            # Meal timing
            if meal_type == "breakfast":
                hour = random.choice([7, 7, 8, 8, 8, 9]) if not is_weekend(d) else random.choice([8, 9, 9, 10])
            elif meal_type == "lunch":
                hour = random.choice([11, 12, 12, 12, 13])
            elif meal_type == "dinner":
                hour = random.choice([18, 18, 19, 19, 19, 20])
            else:
                hour = random.choice([10, 15, 16, 21])
            minute = random.randint(0, 59)

            timestamp = ts(d, hour, minute)

            total_cal = sum(i.get("calories", 0) or 0 for i in ingredients)
            total_pro = sum(i.get("protein_g", 0) or 0 for i in ingredients)
            total_carb = sum(i.get("carbs_g", 0) or 0 for i in ingredients)
            total_fat = sum(i.get("fat_g", 0) or 0 for i in ingredients)
            total_fiber = sum(i.get("fiber_g", 0) or 0 for i in ingredients)

            cursor = conn.execute(
                """INSERT INTO diet_entries
                   (timestamp, meal_type, description, total_calories, total_protein_g,
                    total_carbs_g, total_fat_g, total_fiber_g, confidence_score, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (timestamp, meal_type, description, round(total_cal, 1), round(total_pro, 1),
                 round(total_carb, 1), round(total_fat, 1), round(total_fiber, 1),
                 round(noisy(0.85, 0.08, 0.5, 1.0), 2), None, now, now),
            )
            entry_id = cursor.lastrowid
            meal_count += 1
            day_cal += total_cal

            # Insert ingredients
            ingredient_cols = [
                "ingredient_name", "normalized_name", "amount_g", "calories",
                "protein_g", "carbs_g", "fat_g", "fiber_g",
                "vitamin_a_mcg", "vitamin_b1_mg", "vitamin_b2_mg", "vitamin_b3_mg",
                "vitamin_b5_mg", "vitamin_b6_mg", "vitamin_b7_mcg", "vitamin_b9_mcg",
                "vitamin_b12_mcg", "vitamin_c_mg", "vitamin_d_mcg", "vitamin_e_mg",
                "vitamin_k_mcg", "calcium_mg", "iron_mg", "magnesium_mg",
                "zinc_mg", "potassium_mg", "sodium_mg",
            ]
            for ing in ingredients:
                placeholders = ", ".join(["?"] * (len(ingredient_cols) + 2))
                col_names = ", ".join(["entry_id"] + ingredient_cols + ["created_at"])
                values = [entry_id] + [ing.get(c) for c in ingredient_cols] + [now]
                conn.execute(
                    f"INSERT INTO diet_ingredients ({col_names}) VALUES ({placeholders})",
                    tuple(values),
                )
                ingredient_count += 1

        total_daily_cals.append(day_cal)

    conn.commit()
    counts["diet_entries"] = meal_count
    counts["diet_ingredients"] = ingredient_count
    avg_cal = sum(total_daily_cals) / len(total_daily_cals)
    print(f"   {meal_count} meals, {ingredient_count} ingredients")
    print(f"   Average daily calories: {avg_cal:.0f} kcal")

    # -----------------------------------------------------------------------
    # EXERCISE
    # -----------------------------------------------------------------------
    print("\n4. Generating exercise data...")
    exercise_count = 0
    detail_count = 0

    for d in ALL_DAYS:
        sessions = plan_exercise_schedule(d)
        for session in sessions:
            hour = random.choice([6, 7, 7, 17, 18, 18]) if phase(d) >= 2 else random.choice([8, 10, 17, 18, 19])
            timestamp = ts(d, hour, random.randint(0, 30))

            cursor = conn.execute(
                """INSERT INTO exercise_entries
                   (timestamp, activity_type, duration_minutes, distance_km,
                    avg_hr, rpe, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (timestamp, session["activity_type"], session["duration_minutes"],
                 session.get("distance_km"), session.get("avg_hr"), session.get("rpe"),
                 session.get("notes"), now, now),
            )
            entry_id = cursor.lastrowid
            exercise_count += 1

            for detail in session.get("details", []):
                conn.execute(
                    """INSERT INTO exercise_details
                       (entry_id, exercise_name, sets, reps, weight_kg, duration_seconds, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (entry_id, detail.get("exercise_name", "unknown"),
                     detail.get("sets"), detail.get("reps"),
                     detail.get("weight_kg"), detail.get("duration_seconds"),
                     detail.get("notes")),
                )
                detail_count += 1

    conn.commit()
    counts["exercise_entries"] = exercise_count
    counts["exercise_details"] = detail_count
    print(f"   {exercise_count} sessions, {detail_count} exercise details")

    # -----------------------------------------------------------------------
    # BODY METRICS
    # -----------------------------------------------------------------------
    print("\n5. Generating body metrics (daily)...")
    metric_count = 0

    for d in ALL_DAYS:
        metrics = gen_body_metrics(d)
        for metric_type, value, unit, context, device in metrics:
            hour = 7 if "morning" in (context or "") else 22
            timestamp = ts(d, hour, random.randint(0, 30))
            conn.execute(
                """INSERT INTO body_metrics
                   (timestamp, metric_type, value, unit, context, device_method, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (timestamp, metric_type, value, unit, context, device, now),
            )
            metric_count += 1

    conn.commit()
    counts["body_metrics"] = metric_count
    print(f"   {metric_count} metric entries")

    # -----------------------------------------------------------------------
    # BIOMARKERS
    # -----------------------------------------------------------------------
    print("\n6. Generating biomarker panels (3 lab draws)...")
    biomarker_count = 0

    for panel_date, markers in gen_biomarker_panels():
        timestamp = ts(panel_date, 8, 0)
        for panel_name, marker_name, value, unit, ref_lo, ref_hi, opt_lo, opt_hi, notes in markers:
            conn.execute(
                """INSERT INTO biomarkers
                   (timestamp, panel_name, marker_name, value, unit,
                    reference_low, reference_high, optimal_low, optimal_high,
                    notes, lab_source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (timestamp, panel_name, marker_name, value, unit,
                 ref_lo, ref_hi, opt_lo, opt_hi, notes,
                 "Quest Diagnostics", now),
            )
            biomarker_count += 1

    conn.commit()
    counts["biomarkers"] = biomarker_count
    print(f"   {biomarker_count} biomarker results across 3 panels")

    # -----------------------------------------------------------------------
    # SUPPLEMENTS
    # -----------------------------------------------------------------------
    print("\n7. Generating supplements...")
    supp_count = 0

    for compound, dosage, unit, freq, timing, start, end, reason, brand in SUPPLEMENTS:
        conn.execute(
            """INSERT INTO supplements
               (compound_name, dosage, dosage_unit, frequency, timing,
                start_date, end_date, reason, brand, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (compound, dosage, unit, freq, timing, start, end, reason, brand, now, now),
        )
        supp_count += 1

    conn.commit()
    counts["supplements"] = supp_count
    print(f"   {supp_count} supplement entries")

    # -----------------------------------------------------------------------
    # TRIALS
    # -----------------------------------------------------------------------
    print("\n8. Generating N-of-1 trials...")

    # Trial 1: Protein-Sleep Quality (completed)
    cursor = conn.execute(
        """INSERT INTO trials
           (name, hypothesis, intervention, primary_outcome_metric,
            secondary_outcomes_json, design, phase_duration_days,
            washout_duration_days, min_observations_per_phase,
            status, literature_evidence_json, start_date, end_date, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "Protein-Sleep Quality Trial",
            "Increasing dinner protein to 30g+ improves next-day sleep quality score by at least 0.5 points",
            "High-protein dinner (30-40g protein) vs normal dinner (15-20g protein)",
            "sleep_quality",
            json.dumps(["dinner_protein_g", "sleep_duration", "hrv"]),
            "ABA",
            14, 0, 10,
            "completed",
            json.dumps([
                {"pmid": "28899668", "title": "Dietary protein and sleep quality", "effect_size": 0.45},
                {"pmid": "31255753", "title": "Protein timing and sleep architecture", "effect_size": 0.38},
            ]),
            "2026-02-01", "2026-02-28", now, now,
        ),
    )
    trial1_id = cursor.lastrowid

    for obs_date, obs_phase, metric, value, compliance, notes in gen_trial_1_observations():
        conn.execute(
            """INSERT INTO trial_observations
               (trial_id, date, phase, metric_name, value, compliance_score, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (trial1_id, obs_date, obs_phase, metric, round(value, 2), round(compliance, 2), notes, now),
        )

    # Trial 2: Creatine-Cognition (active)
    cursor = conn.execute(
        """INSERT INTO trials
           (name, hypothesis, intervention, primary_outcome_metric,
            secondary_outcomes_json, design, phase_duration_days,
            washout_duration_days, min_observations_per_phase,
            status, literature_evidence_json, start_date, end_date, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            "Creatine-Cognition Trial",
            "5g daily creatine monohydrate improves working memory and reaction time within 4 weeks",
            "5g creatine monohydrate daily vs no supplementation",
            "working_memory_score",
            json.dumps(["reaction_time_ms", "sleep_quality"]),
            "ABA",
            14, 7, 7,
            "active",
            json.dumps([
                {"pmid": "29704637", "title": "Creatine supplementation and cognitive function", "effect_size": 0.35},
                {"pmid": "35254272", "title": "Creatine and brain bioenergetics", "effect_size": 0.42},
                {"pmid": "31279955", "title": "Nootropic effects of creatine in young adults", "effect_size": 0.28},
            ]),
            "2026-03-01", None, now, now,
        ),
    )
    trial2_id = cursor.lastrowid

    trial_obs_count = 0
    for obs_date, obs_phase, metric, value, compliance, notes in gen_trial_2_observations():
        conn.execute(
            """INSERT INTO trial_observations
               (trial_id, date, phase, metric_name, value, compliance_score, notes, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (trial2_id, obs_date, obs_phase, metric, round(value, 2), round(compliance, 2), notes, now),
        )
        trial_obs_count += 1

    trial1_obs = len(gen_trial_1_observations())
    conn.commit()
    counts["trials"] = 2
    counts["trial_observations"] = trial1_obs + trial_obs_count
    print(f"   2 trials ({trial1_obs + trial_obs_count} total observations)")
    print(f"   Trial 1: Protein-Sleep Quality (completed, {trial1_obs} observations)")
    print(f"   Trial 2: Creatine-Cognition (active, {trial_obs_count} observations)")

    # -----------------------------------------------------------------------
    # INSIGHTS
    # -----------------------------------------------------------------------
    print("\n9. Generating insights...")
    insight_count = 0

    insight_dates = [
        date(2026, 2, 15),  # First batch after mid-intervention labs
        date(2026, 3, 1),
        date(2026, 3, 10),
        date(2026, 3, 15),
        date(2026, 3, 20),
        date(2026, 3, 25),
        date(2026, 3, 28),
        date(2026, 3, 31),
    ]

    for i, insight in enumerate(INSIGHTS):
        insight_date = insight_dates[i] if i < len(insight_dates) else insight_dates[-1]
        timestamp = ts(insight_date, 22, 0)
        conn.execute(
            """INSERT INTO insights
               (timestamp, insight_type, source_modules_json, description,
                statistical_detail_json, effect_size, p_value,
                confidence_level, evidence_level, actionable, trial_candidate,
                created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                timestamp, insight["type"], json.dumps(insight["modules"]),
                insight["description"], json.dumps(insight["stats"]),
                insight["effect_size"], insight["p_value"],
                insight["confidence"], insight["evidence"],
                1 if insight["actionable"] else 0,
                1 if insight["trial_candidate"] else 0,
                now,
            ),
        )
        insight_count += 1

    conn.commit()
    counts["insights"] = insight_count
    print(f"   {insight_count} insights generated")

    # -----------------------------------------------------------------------
    # MODEL CACHE (pre-computed stats)
    # -----------------------------------------------------------------------
    print("\n10. Generating model cache entries...")
    cache_entries = [
        ("weight", "7d", {"mean": 72.1, "std": 0.3, "min": 71.5, "max": 72.6, "n": 7, "trend_slope": -0.07}),
        ("weight", "30d", {"mean": 72.8, "std": 0.6, "min": 71.5, "max": 74.1, "n": 30, "trend_slope": -0.067}),
        ("weight", "90d", {"mean": 73.5, "std": 1.1, "min": 71.5, "max": 75.3, "n": 90, "trend_slope": -0.037}),
        ("resting_hr", "7d", {"mean": 58, "std": 2.5, "min": 54, "max": 62, "n": 7, "trend_slope": -0.2}),
        ("resting_hr", "30d", {"mean": 60, "std": 3.2, "min": 54, "max": 67, "n": 30, "trend_slope": -0.3}),
        ("resting_hr", "90d", {"mean": 63, "std": 4.5, "min": 54, "max": 72, "n": 90, "trend_slope": -0.12}),
        ("hrv", "7d", {"mean": 54, "std": 5, "min": 45, "max": 63, "n": 7, "trend_slope": 0.5}),
        ("hrv", "30d", {"mean": 50, "std": 6, "min": 38, "max": 63, "n": 30, "trend_slope": 0.4}),
        ("hrv", "90d", {"mean": 45, "std": 8, "min": 22, "max": 72, "n": 90, "trend_slope": 0.22}),
        ("sleep_duration", "7d", {"mean": 7.5, "std": 0.4, "min": 6.8, "max": 8.2, "n": 7, "trend_slope": 0.02}),
        ("sleep_duration", "30d", {"mean": 7.4, "std": 0.5, "min": 6.2, "max": 8.5, "n": 30, "trend_slope": 0.03}),
        ("sleep_duration", "90d", {"mean": 7.1, "std": 0.6, "min": 5.1, "max": 9.0, "n": 90, "trend_slope": 0.012}),
        ("sleep_quality", "30d", {"mean": 7.8, "std": 0.8, "min": 5.5, "max": 9.2, "n": 30, "trend_slope": 0.04}),
    ]

    for metric_name, window, stats in cache_entries:
        conn.execute(
            """INSERT OR REPLACE INTO model_cache
               (metric_name, window_type, computed_at, mean, std, min, max, n, trend_slope, extra_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (metric_name, window, now,
             stats["mean"], stats["std"], stats["min"], stats["max"],
             stats["n"], stats["trend_slope"], None),
        )

    # Model runs
    model_run_dates = [date(2026, 1, 15), date(2026, 2, 1), date(2026, 2, 15),
                       date(2026, 3, 1), date(2026, 3, 15), date(2026, 3, 31)]
    for rd in model_run_dates:
        conn.execute(
            """INSERT INTO model_runs
               (timestamp, run_type, modules_analyzed_json, duration_seconds, insights_generated, notes)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ts(rd, 23, 0), "batch" if rd.day == 1 else "passive",
             json.dumps(["diet", "exercise", "body_metrics", "biomarkers", "supplements"]),
             round(noisy(45, 10, 20, 120), 1),
             random.randint(1, 3),
             f"Automated {'batch' if rd.day == 1 else 'passive'} analysis run"),
        )

    conn.commit()
    counts["model_cache"] = len(cache_entries)
    counts["model_runs"] = len(model_run_dates)
    print(f"   {len(cache_entries)} cache entries, {len(model_run_dates)} model runs")

    # -----------------------------------------------------------------------
    # DONE
    # -----------------------------------------------------------------------
    conn.close()

    db_size = os.path.getsize(str(DB_PATH))

    print("\n" + "=" * 70)
    print("GENERATION COMPLETE")
    print("=" * 70)
    print(f"\nDatabase: {DB_PATH}")
    print(f"Size:     {db_size:,} bytes ({db_size / 1024:.1f} KB)")
    print(f"\nDate range: {START_DATE} to {END_DATE} ({len(ALL_DAYS)} days)")
    print(f"\nRow counts:")
    for table, count in sorted(counts.items()):
        print(f"  {table:25s} {count:>6,}")
    total = sum(counts.values())
    print(f"  {'TOTAL':25s} {total:>6,}")

    print(f"\nStory arc summary:")
    print(f"  Phase 1 (Jan 1-31):  Baseline — avg {sum(total_daily_cals[:31])/31:.0f} cal/day")
    print(f"  Phase 2 (Feb 1-28):  Intervention — avg {sum(total_daily_cals[31:59])/28:.0f} cal/day")
    print(f"  Phase 3 (Mar 1-31):  Full optimization — avg {sum(total_daily_cals[59:])/31:.0f} cal/day")
    print(f"  Overall avg:         {avg_cal:.0f} cal/day")
    print()


if __name__ == "__main__":
    main()
