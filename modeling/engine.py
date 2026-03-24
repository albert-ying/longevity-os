"""
太医院 (TaiYiYuan) — Modeling Engine
Core statistical analysis engine for personal longevity tracking.

Usage:
    python engine.py rolling_stats --metric <name> --windows 7,30,90
    python engine.py anomaly_detect --metric <name> --threshold 2.0
    python engine.py trend --metric <name> --days 30
    python engine.py periodicity --metric <name> --days 90
    python engine.py nutrient_summary --days 7
    python engine.py exercise_summary --days 7
    python engine.py daily_digest --date 2026-03-12
    python engine.py weekly_report --start 2026-03-03 --end 2026-03-09

All CLI output is JSON to stdout.
"""

import sys
import os
import json
import argparse
import sqlite3
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

# ---------------------------------------------------------------------------
# DB import — use the project db module if available, fallback to direct conn
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if _DATA_DIR not in sys.path:
    sys.path.insert(0, _DATA_DIR)

from paths import get_db_path


DB_PATH = str(get_db_path())

try:
    from db import TaiYiYuanDB
except ImportError:
    class TaiYiYuanDB:
        """Minimal fallback when data/db.py is not yet available."""
        def __init__(self, db_path: str = DB_PATH):
            self.db_path = db_path
            self.conn = sqlite3.connect(db_path)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")


# ---------------------------------------------------------------------------
# RDA reference values (male, 19-50, approximate)
# ---------------------------------------------------------------------------
RDA_REFERENCE = {
    'vitamin_a_mcg': 900, 'vitamin_b1_mg': 1.2, 'vitamin_b2_mg': 1.3,
    'vitamin_b3_mg': 16, 'vitamin_b5_mg': 5, 'vitamin_b6_mg': 1.3,
    'vitamin_b7_mcg': 30, 'vitamin_b9_mcg': 400, 'vitamin_b12_mcg': 2.4,
    'vitamin_c_mg': 90, 'vitamin_d_mcg': 15, 'vitamin_e_mg': 15,
    'vitamin_k_mcg': 120, 'calcium_mg': 1000, 'iron_mg': 8,
    'magnesium_mg': 420, 'zinc_mg': 11, 'potassium_mg': 3400, 'sodium_mg': 2300,
}

DAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
LOCAL_DATE_SQL = "SUBSTR(timestamp, 1, 10)"


def _to_date(s: str) -> date:
    """Parse a date string (YYYY-MM-DD or ISO 8601 datetime) to date."""
    return datetime.fromisoformat(s.replace('Z', '+00:00')).date() if 'T' in s else date.fromisoformat(s)


def _json_serial(obj):
    """JSON serializer for objects not serializable by default json code."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj) if np.isfinite(obj) else None
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, pd.Timestamp):
        return obj.isoformat()
    raise TypeError(f"Type {type(obj)} not serializable")


class ModelingEngine:
    """Core statistical analysis engine for TaiYiYuan."""

    def __init__(self, db: Optional['TaiYiYuanDB'] = None):
        if db is None:
            db = TaiYiYuanDB()
            db._connect()
        self.db = db

    # ------------------------------------------------------------------
    # Helpers — data fetching
    # ------------------------------------------------------------------

    def _query_df(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        """Execute a SQL query and return a DataFrame."""
        return pd.read_sql_query(sql, self.db.conn, params=params)

    def _get_metric_series(self, metric_name: str, days: Optional[int] = None) -> pd.Series:
        """Fetch a body_metrics time series as a pd.Series indexed by date.

        If metric_name contains a dot (e.g. 'diet.calories'), route to the
        appropriate module table instead of body_metrics.
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat() if days else '1900-01-01'

        if metric_name.startswith('diet.'):
            col = metric_name.split('.', 1)[1]
            col_map = {
                'calories': 'total_calories', 'protein': 'total_protein_g',
                'carbs': 'total_carbs_g', 'fat': 'total_fat_g', 'fiber': 'total_fiber_g',
            }
            db_col = col_map.get(col, col)
            sql = f"""
                SELECT {LOCAL_DATE_SQL} AS dt, SUM({db_col}) AS value
                FROM diet_entries
                WHERE {LOCAL_DATE_SQL} >= ?
                GROUP BY {LOCAL_DATE_SQL}
                ORDER BY dt
            """
        elif metric_name.startswith('exercise.'):
            col = metric_name.split('.', 1)[1]
            col_map = {'minutes': 'duration_minutes', 'distance': 'distance_km',
                       'hr': 'avg_hr', 'rpe': 'rpe'}
            db_col = col_map.get(col, col)
            agg = 'SUM' if col in ('minutes', 'distance') else 'AVG'
            sql = f"""
                SELECT {LOCAL_DATE_SQL} AS dt, {agg}({db_col}) AS value
                FROM exercise_entries
                WHERE {LOCAL_DATE_SQL} >= ?
                GROUP BY {LOCAL_DATE_SQL}
                ORDER BY dt
            """
        elif metric_name.startswith('biomarker.'):
            marker = metric_name.split('.', 1)[1]
            sql = """
                SELECT SUBSTR(timestamp, 1, 10) AS dt, value
                FROM biomarkers
                WHERE marker_name = ? AND SUBSTR(timestamp, 1, 10) >= ?
                ORDER BY dt
            """
            df = self._query_df(sql, (marker, cutoff))
            if df.empty:
                return pd.Series(dtype=float)
            df['dt'] = pd.to_datetime(df['dt']).dt.date
            return df.set_index('dt')['value'].astype(float)
        else:
            # Default: body_metrics table
            sql = """
                SELECT SUBSTR(timestamp, 1, 10) AS dt, AVG(value) AS value
                FROM body_metrics
                WHERE metric_type = ? AND SUBSTR(timestamp, 1, 10) >= ?
                GROUP BY SUBSTR(timestamp, 1, 10)
                ORDER BY dt
            """
            df = self._query_df(sql, (metric_name, cutoff))
            if df.empty:
                return pd.Series(dtype=float)
            df['dt'] = pd.to_datetime(df['dt']).dt.date
            return df.set_index('dt')['value'].astype(float)

        # Execute for diet/exercise paths
        df = self._query_df(sql, (cutoff,))
        if df.empty:
            return pd.Series(dtype=float)
        df['dt'] = pd.to_datetime(df['dt']).dt.date
        return df.set_index('dt')['value'].astype(float)

    # ------------------------------------------------------------------
    # rolling_stats
    # ------------------------------------------------------------------

    def rolling_stats(self, metric_name: str, windows: list[int] = None) -> dict:
        """Compute rolling statistics for a metric across multiple windows.

        Returns: {window: {mean, std, min, max, n, trend_slope, last_value, pct_change_vs_mean}}
        Saves results to model_cache table.
        """
        if windows is None:
            windows = [7, 30, 90]

        max_window = max(windows)
        series = self._get_metric_series(metric_name, days=max_window + 30)
        if series.empty:
            return {w: None for w in windows}

        results = {}
        now = datetime.utcnow().isoformat()

        for w in windows:
            tail = series.tail(w)
            if tail.empty:
                results[w] = None
                continue

            vals = tail.dropna()
            n = len(vals)
            if n == 0:
                results[w] = None
                continue

            mean_val = float(vals.mean())
            std_val = float(vals.std()) if n > 1 else 0.0
            last_val = float(vals.iloc[-1])

            # Trend slope via simple linear regression on ordinal day index
            slope = 0.0
            if n >= 3:
                x = np.arange(n, dtype=float)
                y = vals.values.astype(float)
                slope_res = scipy_stats.linregress(x, y)
                slope = float(slope_res.slope)

            pct_change = ((last_val - mean_val) / mean_val * 100) if mean_val != 0 else 0.0

            results[w] = {
                'mean': round(mean_val, 4),
                'std': round(std_val, 4),
                'min': round(float(vals.min()), 4),
                'max': round(float(vals.max()), 4),
                'n': n,
                'trend_slope': round(slope, 6),
                'last_value': round(last_val, 4),
                'pct_change_vs_mean': round(pct_change, 2),
            }

            # Persist to model_cache
            window_type = f'{w}d'
            try:
                self.db.conn.execute(
                    """INSERT INTO model_cache
                       (metric_name, window_type, computed_at, mean, std, min, max, n, trend_slope, extra_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(metric_name, window_type) DO UPDATE SET
                         computed_at=excluded.computed_at, mean=excluded.mean, std=excluded.std,
                         min=excluded.min, max=excluded.max, n=excluded.n,
                         trend_slope=excluded.trend_slope, extra_json=excluded.extra_json
                    """,
                    (metric_name, window_type, now,
                     results[w]['mean'], results[w]['std'], results[w]['min'], results[w]['max'],
                     results[w]['n'], results[w]['trend_slope'],
                     json.dumps({'last_value': last_val, 'pct_change_vs_mean': pct_change})),
                )
                self.db.conn.commit()
            except Exception:
                pass  # Cache write is best-effort

        return results

    # ------------------------------------------------------------------
    # anomaly detection
    # ------------------------------------------------------------------

    def detect_anomalies(self, metric_name: str, threshold: float = 2.0,
                         days: int = 90) -> list[dict]:
        """Find values > threshold SDs from rolling mean.

        Uses a 30-day rolling window for the reference distribution.
        Returns: [{date, value, mean, std, z_score, direction}]
        """
        series = self._get_metric_series(metric_name, days=days)
        if len(series) < 7:
            return []

        # Build a full date-indexed series (no reindex gaps)
        idx = pd.date_range(min(series.index), max(series.index), freq='D')
        full = series.reindex([d.date() for d in idx])

        rolling_mean = full.rolling(window=30, min_periods=7, center=False).mean()
        rolling_std = full.rolling(window=30, min_periods=7, center=False).std()

        anomalies = []
        for dt in full.index:
            val = full.get(dt)
            rm = rolling_mean.get(dt)
            rs = rolling_std.get(dt)
            if pd.isna(val) or pd.isna(rm) or pd.isna(rs) or rs == 0:
                continue
            z = (val - rm) / rs
            if abs(z) >= threshold:
                anomalies.append({
                    'date': str(dt),
                    'value': round(float(val), 4),
                    'mean': round(float(rm), 4),
                    'std': round(float(rs), 4),
                    'z_score': round(float(z), 2),
                    'direction': 'high' if z > 0 else 'low',
                })

        return anomalies

    # ------------------------------------------------------------------
    # trend analysis
    # ------------------------------------------------------------------

    def trend_analysis(self, metric_name: str, days: int = 30) -> dict:
        """Linear regression over time window.

        Returns: {slope, intercept, r_squared, p_value, direction, pct_change,
                  n_observations, period_start, period_end}
        Direction is 'stable' if p > 0.05.
        """
        series = self._get_metric_series(metric_name, days=days).dropna()
        if len(series) < 3:
            return {
                'error': 'insufficient_data',
                'n_observations': len(series),
                'message': f'Need at least 3 observations, have {len(series)}',
            }

        dates = sorted(series.index)
        day0 = dates[0]
        x = np.array([(d - day0).days for d in dates], dtype=float)
        y = series.loc[dates].values.astype(float)

        result = scipy_stats.linregress(x, y)
        slope, intercept, r_value, p_value, se = result

        if p_value > 0.05:
            direction = 'stable'
        elif slope > 0:
            direction = 'increasing'
        else:
            direction = 'decreasing'

        # Percent change over the period
        y_start = intercept
        y_end = intercept + slope * x[-1]
        pct_change = ((y_end - y_start) / abs(y_start) * 100) if y_start != 0 else 0.0

        return {
            'slope': round(float(slope), 6),
            'intercept': round(float(intercept), 4),
            'r_squared': round(float(r_value ** 2), 4),
            'p_value': round(float(p_value), 6),
            'direction': direction,
            'pct_change': round(float(pct_change), 2),
            'n_observations': len(y),
            'period_start': str(dates[0]),
            'period_end': str(dates[-1]),
        }

    # ------------------------------------------------------------------
    # periodicity detection
    # ------------------------------------------------------------------

    def periodicity_detection(self, metric_name: str, days: int = 90) -> dict:
        """Detect day-of-week and monthly patterns using one-way ANOVA."""
        series = self._get_metric_series(metric_name, days=days).dropna()
        result = {'day_of_week': None, 'monthly': None}

        if len(series) < 14:
            return result

        # --- Day-of-week analysis ---
        df = pd.DataFrame({'value': series})
        df['date'] = pd.to_datetime(pd.Series(series.index))
        df['dow'] = df['date'].dt.dayofweek  # 0=Mon .. 6=Sun

        groups_dow = [g['value'].values for _, g in df.groupby('dow') if len(g) >= 2]
        if len(groups_dow) >= 2:
            f_stat, p_val = scipy_stats.f_oneway(*groups_dow)
            means_by_day = {}
            for dow_idx, name in enumerate(DAY_NAMES):
                subset = df[df['dow'] == dow_idx]['value']
                means_by_day[name] = round(float(subset.mean()), 4) if len(subset) > 0 else None
            result['day_of_week'] = {
                'pattern_found': bool(p_val < 0.05),
                'anova_p': round(float(p_val), 6),
                'f_statistic': round(float(f_stat), 4),
                'means_by_day': means_by_day,
            }

        # --- Monthly analysis ---
        df['month'] = df['date'].dt.month
        groups_month = [g['value'].values for _, g in df.groupby('month') if len(g) >= 2]
        if len(groups_month) >= 2:
            f_stat_m, p_val_m = scipy_stats.f_oneway(*groups_month)
            means_by_month = {}
            for _, g in df.groupby('month'):
                month_name = g['date'].iloc[0].strftime('%b')
                means_by_month[month_name] = round(float(g['value'].mean()), 4)
            result['monthly'] = {
                'pattern_found': bool(p_val_m < 0.05),
                'anova_p': round(float(p_val_m), 6),
                'f_statistic': round(float(f_stat_m), 4),
                'means_by_month': means_by_month,
            }

        return result

    # ------------------------------------------------------------------
    # nutrient summary
    # ------------------------------------------------------------------

    def nutrient_summary(self, days: int = 7) -> dict:
        """Summarize nutrition over a period."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()

        # Daily totals from diet_entries
        entries_df = self._query_df(
            """SELECT SUBSTR(timestamp, 1, 10) AS dt, meal_type,
                      total_calories, total_protein_g, total_carbs_g,
                      total_fat_g, total_fiber_g
               FROM diet_entries WHERE SUBSTR(timestamp, 1, 10) >= ? ORDER BY dt""",
            (cutoff,),
        )

        if entries_df.empty:
            return {'error': 'no_data', 'message': f'No diet entries in the last {days} days'}

        n_days = entries_df['dt'].nunique()

        avg_daily_cal = float(entries_df.groupby('dt')['total_calories'].sum().mean())
        avg_macros = {
            'protein_g': round(float(entries_df.groupby('dt')['total_protein_g'].sum().mean()), 1),
            'carbs_g': round(float(entries_df.groupby('dt')['total_carbs_g'].sum().mean()), 1),
            'fat_g': round(float(entries_df.groupby('dt')['total_fat_g'].sum().mean()), 1),
            'fiber_g': round(float(entries_df.groupby('dt')['total_fiber_g'].sum().mean()), 1),
        }

        meal_dist = entries_df['meal_type'].value_counts().to_dict()

        # Micronutrient adequacy from ingredients
        micro_cols = list(RDA_REFERENCE.keys())
        placeholders = ', '.join([f'SUM({c}) AS {c}' for c in micro_cols])
        micro_sql = f"""
            SELECT SUBSTR(de.timestamp, 1, 10) AS dt, {placeholders}
            FROM diet_ingredients di
            JOIN diet_entries de ON di.entry_id = de.id
            WHERE SUBSTR(de.timestamp, 1, 10) >= ?
            GROUP BY SUBSTR(de.timestamp, 1, 10)
        """
        micro_df = self._query_df(micro_sql, (cutoff,))

        nutrient_adequacy = {}
        if not micro_df.empty:
            for col, rda in RDA_REFERENCE.items():
                avg_intake = micro_df[col].mean()
                if pd.notna(avg_intake) and avg_intake > 0:
                    nutrient_adequacy[col] = round(float(avg_intake / rda * 100), 1)

        return {
            'period_days': days,
            'days_with_data': n_days,
            'avg_daily_calories': round(avg_daily_cal, 0),
            'avg_macros': avg_macros,
            'nutrient_adequacy_pct_rda': nutrient_adequacy,
            'meal_distribution': meal_dist,
            'total_meals_logged': len(entries_df),
        }

    # ------------------------------------------------------------------
    # exercise summary
    # ------------------------------------------------------------------

    def exercise_summary(self, days: int = 7) -> dict:
        """Summarize exercise over a period."""
        cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()

        entries_df = self._query_df(
            """SELECT id, SUBSTR(timestamp, 1, 10) AS dt, activity_type,
                      duration_minutes, distance_km, avg_hr, rpe
               FROM exercise_entries WHERE SUBSTR(timestamp, 1, 10) >= ? ORDER BY dt""",
            (cutoff,),
        )

        if entries_df.empty:
            return {'error': 'no_data', 'message': f'No exercise entries in the last {days} days'}

        total_sessions = len(entries_df)
        total_minutes = float(entries_df['duration_minutes'].sum())

        # By type breakdown
        by_type = {}
        for atype, group in entries_df.groupby('activity_type'):
            info = {
                'sessions': len(group),
                'total_minutes': round(float(group['duration_minutes'].sum()), 1),
            }
            if group['distance_km'].notna().any():
                info['total_distance_km'] = round(float(group['distance_km'].sum()), 2)
            if group['avg_hr'].notna().any():
                info['avg_hr'] = round(float(group['avg_hr'].mean()), 0)
            by_type[atype] = info

        # Volume load from exercise_details (sets * reps * weight)
        detail_df = self._query_df(
            """SELECT ed.exercise_name, ed.sets, ed.reps, ed.weight_kg,
                      ee.activity_type
               FROM exercise_details ed
               JOIN exercise_entries ee ON ed.entry_id = ee.id
               WHERE SUBSTR(ee.timestamp, 1, 10) >= ?""",
            (cutoff,),
        )
        if not detail_df.empty:
            detail_df['volume'] = detail_df['sets'] * detail_df['reps'] * detail_df['weight_kg']
            total_volume = float(detail_df['volume'].sum())
            # Muscle group balance (simplified: group by exercise_name)
            muscle_groups = detail_df.groupby('exercise_name')['volume'].sum().to_dict()
            muscle_groups = {k: round(v, 1) for k, v in muscle_groups.items()}
        else:
            total_volume = 0.0
            muscle_groups = {}

        avg_rpe = round(float(entries_df['rpe'].mean()), 1) if entries_df['rpe'].notna().any() else None

        # Weekly trend: sessions per calendar week
        entries_df['date_parsed'] = pd.to_datetime(entries_df['dt'])
        weekly_counts = entries_df.groupby(entries_df['date_parsed'].dt.isocalendar().week).size().to_dict()
        weekly_counts = {f'W{int(k)}': int(v) for k, v in weekly_counts.items()}

        return {
            'period_days': days,
            'total_sessions': total_sessions,
            'total_minutes': round(total_minutes, 1),
            'by_type': by_type,
            'total_volume_load': round(total_volume, 1),
            'muscle_group_balance': muscle_groups,
            'avg_rpe': avg_rpe,
            'weekly_trend': weekly_counts,
        }

    # ------------------------------------------------------------------
    # daily digest
    # ------------------------------------------------------------------

    def daily_digest(self, date_str: str) -> dict:
        """Generate a daily summary across all modules."""
        target = date_str

        # --- Diet ---
        diet_df = self._query_df(
            """SELECT meal_type, total_calories, total_protein_g, total_carbs_g,
                      total_fat_g, total_fiber_g
               FROM diet_entries WHERE SUBSTR(timestamp, 1, 10) = ?""",
            (target,),
        )
        diet_info = None
        if not diet_df.empty:
            diet_info = {
                'meals': len(diet_df),
                'total_calories': round(float(diet_df['total_calories'].sum()), 0),
                'macros': {
                    'protein_g': round(float(diet_df['total_protein_g'].sum()), 1),
                    'carbs_g': round(float(diet_df['total_carbs_g'].sum()), 1),
                    'fat_g': round(float(diet_df['total_fat_g'].sum()), 1),
                    'fiber_g': round(float(diet_df['total_fiber_g'].sum()), 1),
                },
                'by_meal': diet_df.groupby('meal_type')['total_calories'].sum().to_dict(),
            }

        # --- Exercise ---
        ex_df = self._query_df(
            """SELECT activity_type, duration_minutes, distance_km, rpe
               FROM exercise_entries WHERE SUBSTR(timestamp, 1, 10) = ?""",
            (target,),
        )
        exercise_info = None
        if not ex_df.empty:
            exercise_info = {
                'sessions': len(ex_df),
                'total_minutes': round(float(ex_df['duration_minutes'].sum()), 1),
                'activities': ex_df['activity_type'].tolist(),
                'avg_rpe': round(float(ex_df['rpe'].mean()), 1) if ex_df['rpe'].notna().any() else None,
            }

        # --- Body metrics ---
        metrics_df = self._query_df(
            """SELECT metric_type, value, unit, context
               FROM body_metrics WHERE SUBSTR(timestamp, 1, 10) = ?""",
            (target,),
        )
        metrics_info = None
        if not metrics_df.empty:
            entries = []
            for _, row in metrics_df.iterrows():
                entries.append({
                    'metric': row['metric_type'],
                    'value': row['value'],
                    'unit': row['unit'],
                    'context': row['context'],
                })
            metrics_info = {'entries': entries, 'alerts': []}

            # Flag anything outside 2 SD from 30-day rolling mean
            for entry in entries:
                try:
                    anomalies = self.detect_anomalies(entry['metric'], threshold=2.0, days=30)
                    day_anomalies = [a for a in anomalies if a['date'] == target]
                    if day_anomalies:
                        metrics_info['alerts'].extend(day_anomalies)
                except Exception:
                    pass

        # --- Supplements ---
        suppl_df = self._query_df(
            """SELECT compound_name, dosage, dosage_unit, frequency, timing
               FROM supplements
               WHERE start_date <= ? AND (end_date IS NULL OR end_date >= ?)""",
            (target, target),
        )
        suppl_info = None
        if not suppl_df.empty:
            suppl_info = {
                'active': len(suppl_df),
                'compounds': suppl_df['compound_name'].tolist(),
            }

        # --- Active trials ---
        trials_df = self._query_df(
            """SELECT id, name, intervention, status
               FROM trials
               WHERE status IN ('active', 'approved')
                 AND (start_date IS NULL OR start_date <= ?)""",
            (target,),
        )
        trials_info = None
        if not trials_df.empty:
            trials_info = {
                'active': [
                    {'id': int(row['id']), 'name': row['name'],
                     'intervention': row['intervention'], 'status': row['status']}
                    for _, row in trials_df.iterrows()
                ],
            }

        # --- Insights generated today ---
        insights_df = self._query_df(
            "SELECT description, insight_type, confidence_level FROM insights WHERE SUBSTR(timestamp, 1, 10) = ?",
            (target,),
        )
        insights_list = []
        if not insights_df.empty:
            insights_list = [
                {'type': row['insight_type'], 'description': row['description'],
                 'confidence': row['confidence_level']}
                for _, row in insights_df.iterrows()
            ]

        return {
            'date': target,
            'diet': diet_info,
            'exercise': exercise_info,
            'metrics': metrics_info,
            'supplements': suppl_info,
            'trials': trials_info,
            'insights': insights_list,
        }

    # ------------------------------------------------------------------
    # weekly report
    # ------------------------------------------------------------------

    def weekly_report_data(self, start_date: str, end_date: str) -> dict:
        """Generate comprehensive weekly report data."""
        # Nutrient + exercise summaries for the period
        period_days = (_to_date(end_date) - _to_date(start_date)).days + 1

        diet = self.nutrient_summary(days=period_days)
        exercise = self.exercise_summary(days=period_days)

        # Metrics trends for the period
        metric_types_df = self._query_df(
            """SELECT DISTINCT metric_type FROM body_metrics
               WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?""",
            (start_date, end_date),
        )
        metric_trends = {}
        for _, row in metric_types_df.iterrows():
            mt = row['metric_type']
            metric_trends[mt] = self.trend_analysis(mt, days=period_days)

        # Biomarkers logged in the period
        bio_df = self._query_df(
            """SELECT marker_name, value, unit, reference_low, reference_high,
                      optimal_low, optimal_high
               FROM biomarkers WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?""",
            (start_date, end_date),
        )
        biomarkers = []
        if not bio_df.empty:
            for _, row in bio_df.iterrows():
                entry = {
                    'marker': row['marker_name'],
                    'value': row['value'],
                    'unit': row['unit'],
                    'in_reference_range': True,
                    'in_optimal_range': True,
                }
                if pd.notna(row['reference_low']) and pd.notna(row['reference_high']):
                    entry['in_reference_range'] = bool(row['reference_low'] <= row['value'] <= row['reference_high'])
                if pd.notna(row['optimal_low']) and pd.notna(row['optimal_high']):
                    entry['in_optimal_range'] = bool(row['optimal_low'] <= row['value'] <= row['optimal_high'])
                biomarkers.append(entry)

        # Anomalies across all tracked metrics
        all_anomalies = []
        for _, row in metric_types_df.iterrows():
            mt = row['metric_type']
            try:
                anoms = self.detect_anomalies(mt, threshold=2.0, days=period_days + 30)
                period_anoms = [a for a in anoms
                                if start_date <= a['date'] <= end_date]
                all_anomalies.extend([{**a, 'metric': mt} for a in period_anoms])
            except Exception:
                pass

        # Trial updates
        trials_df = self._query_df(
            """SELECT id, name, status, intervention, primary_outcome_metric
               FROM trials WHERE status IN ('active', 'completed')
                 AND ((start_date BETWEEN ? AND ?) OR (end_date BETWEEN ? AND ?)
                      OR (start_date <= ? AND (end_date IS NULL OR end_date >= ?)))""",
            (start_date, end_date, start_date, end_date, start_date, end_date),
        )
        trial_updates = []
        if not trials_df.empty:
            for _, row in trials_df.iterrows():
                obs_count = self._query_df(
                    "SELECT COUNT(*) AS n FROM trial_observations WHERE trial_id = ? AND date BETWEEN ? AND ?",
                    (int(row['id']), start_date, end_date),
                ).iloc[0]['n']
                trial_updates.append({
                    'trial_id': int(row['id']),
                    'name': row['name'],
                    'status': row['status'],
                    'observations_this_week': int(obs_count),
                })

        # Supplement compliance (simplified: active supplements during period)
        suppl_df = self._query_df(
            """SELECT compound_name, dosage, dosage_unit, frequency
               FROM supplements
               WHERE start_date <= ? AND (end_date IS NULL OR end_date >= ?)""",
            (end_date, start_date),
        )
        supplements = {
            'active_count': len(suppl_df),
            'compounds': suppl_df['compound_name'].tolist() if not suppl_df.empty else [],
        }

        return {
            'period': {'start': start_date, 'end': end_date, 'days': period_days},
            'modules': {
                'diet': diet,
                'exercise': exercise,
                'metrics': metric_trends,
                'biomarkers': biomarkers,
                'supplements': supplements,
            },
            'anomalies': all_anomalies,
            'trial_updates': trial_updates,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='TaiYiYuan Modeling Engine',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # rolling_stats
    p_rs = sub.add_parser('rolling_stats', help='Compute rolling statistics')
    p_rs.add_argument('--metric', required=True, help='Metric name')
    p_rs.add_argument('--windows', default='7,30,90', help='Comma-separated window sizes')

    # anomaly_detect
    p_ad = sub.add_parser('anomaly_detect', help='Detect anomalies')
    p_ad.add_argument('--metric', required=True, help='Metric name')
    p_ad.add_argument('--threshold', type=float, default=2.0, help='Z-score threshold')
    p_ad.add_argument('--days', type=int, default=90, help='Lookback days')

    # trend
    p_tr = sub.add_parser('trend', help='Trend analysis')
    p_tr.add_argument('--metric', required=True, help='Metric name')
    p_tr.add_argument('--days', type=int, default=30, help='Analysis window in days')

    # periodicity
    p_per = sub.add_parser('periodicity', help='Periodicity detection')
    p_per.add_argument('--metric', required=True, help='Metric name')
    p_per.add_argument('--days', type=int, default=90, help='Lookback days')

    # nutrient_summary
    p_ns = sub.add_parser('nutrient_summary', help='Nutrition summary')
    p_ns.add_argument('--days', type=int, default=7, help='Period in days')

    # exercise_summary
    p_es = sub.add_parser('exercise_summary', help='Exercise summary')
    p_es.add_argument('--days', type=int, default=7, help='Period in days')

    # daily_digest
    p_dd = sub.add_parser('daily_digest', help='Daily digest')
    p_dd.add_argument('--date', required=True, help='Date (YYYY-MM-DD)')

    # weekly_report
    p_wr = sub.add_parser('weekly_report', help='Weekly report')
    p_wr.add_argument('--start', required=True, help='Start date (YYYY-MM-DD)')
    p_wr.add_argument('--end', required=True, help='End date (YYYY-MM-DD)')

    args = parser.parse_args()

    try:
        engine = ModelingEngine()

        if args.command == 'rolling_stats':
            windows = [int(w) for w in args.windows.split(',')]
            result = engine.rolling_stats(args.metric, windows=windows)
        elif args.command == 'anomaly_detect':
            result = engine.detect_anomalies(args.metric, threshold=args.threshold, days=args.days)
        elif args.command == 'trend':
            result = engine.trend_analysis(args.metric, days=args.days)
        elif args.command == 'periodicity':
            result = engine.periodicity_detection(args.metric, days=args.days)
        elif args.command == 'nutrient_summary':
            result = engine.nutrient_summary(days=args.days)
        elif args.command == 'exercise_summary':
            result = engine.exercise_summary(days=args.days)
        elif args.command == 'daily_digest':
            result = engine.daily_digest(args.date)
        elif args.command == 'weekly_report':
            result = engine.weekly_report_data(args.start, args.end)
        else:
            result = {'error': f'Unknown command: {args.command}'}

        print(json.dumps(result, indent=2, default=_json_serial))

    except Exception as e:
        print(json.dumps({'error': str(e), 'type': type(e).__name__}, indent=2))
        sys.exit(1)


if __name__ == '__main__':
    main()
