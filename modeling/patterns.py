"""
太医院 (TaiYiYuan) — Pattern Detection & Correlation Scanning

Scans all metric pairs for significant correlations, detects cross-module
patterns, identifies changepoints, and nominates N-of-1 trial candidates.

Usage:
    python patterns.py scan --days 90
    python patterns.py correlate --metric1 <name> --metric2 <name> --lag 0,1,2,3
    python patterns.py trial_candidates
    python patterns.py changepoints --metric <name> --days 180

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
from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

# ---------------------------------------------------------------------------
# DB import
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
# Cross-module scan categories
# ---------------------------------------------------------------------------
CROSS_MODULE_PAIRS = [
    {
        'name': 'diet_sleep',
        'description': 'Diet → Sleep correlations',
        'metrics1_prefix': 'diet.',
        'metrics2_prefix': 'sleep_',
        'lags': [0, 1],
    },
    {
        'name': 'exercise_sleep',
        'description': 'Exercise → Sleep correlations',
        'metrics1_prefix': 'exercise.',
        'metrics2_prefix': 'sleep_',
        'lags': [0, 1],
    },
    {
        'name': 'diet_body',
        'description': 'Diet → Body metrics correlations',
        'metrics1_prefix': 'diet.',
        'metrics2_prefix': 'body_',
        'lags': [0, 1, 2, 3],
    },
    {
        'name': 'exercise_body',
        'description': 'Exercise → Body metrics correlations',
        'metrics1_prefix': 'exercise.',
        'metrics2_prefix': 'body_',
        'lags': [0, 1, 2],
    },
]
LOCAL_DATE_SQL = "SUBSTR(timestamp, 1, 10)"


def _json_serial(obj):
    """JSON serializer for numpy/pandas types."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj) if np.isfinite(obj) else None
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


class PatternDetector:
    """Pattern detection and correlation scanning for TaiYiYuan."""

    def __init__(self, db: Optional['TaiYiYuanDB'] = None):
        if db is None:
            db = TaiYiYuanDB()
            db._connect()
        self.db = db

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _query_df(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        return pd.read_sql_query(sql, self.db.conn, params=params)

    def _get_all_metric_series(self, days: int) -> dict:
        """Extract all available metric time series from all modules.

        Returns dict mapping metric_name -> pd.Series indexed by date.
        Pulls from diet_entries (daily aggregates), exercise_entries,
        body_metrics, and biomarkers.
        """
        cutoff = (datetime.utcnow() - timedelta(days=days)).date().isoformat()
        series = {}

        # --- Diet: daily aggregates ---
        diet_cols = {
            'diet.calories': 'total_calories',
            'diet.protein': 'total_protein_g',
            'diet.carbs': 'total_carbs_g',
            'diet.fat': 'total_fat_g',
            'diet.fiber': 'total_fiber_g',
        }
        for metric_name, col in diet_cols.items():
            df = self._query_df(
                f"SELECT {LOCAL_DATE_SQL} AS dt, SUM({col}) AS value "
                f"FROM diet_entries WHERE {LOCAL_DATE_SQL} >= ? GROUP BY dt ORDER BY dt",
                (cutoff,),
            )
            if not df.empty and df['value'].notna().sum() >= 5:
                df['dt'] = pd.to_datetime(df['dt']).dt.date
                series[metric_name] = df.set_index('dt')['value'].astype(float)

        # --- Diet: micronutrient daily totals ---
        micro_cols = [
            'vitamin_a_mcg', 'vitamin_c_mg', 'vitamin_d_mcg', 'vitamin_b12_mcg',
            'calcium_mg', 'iron_mg', 'magnesium_mg', 'zinc_mg', 'potassium_mg', 'sodium_mg',
        ]
        for col in micro_cols:
            try:
                df = self._query_df(
                    f"SELECT SUBSTR(de.timestamp, 1, 10) AS dt, SUM(di.{col}) AS value "
                    f"FROM diet_ingredients di JOIN diet_entries de ON di.entry_id = de.id "
                    f"WHERE SUBSTR(de.timestamp, 1, 10) >= ? GROUP BY dt ORDER BY dt",
                    (cutoff,),
                )
                if not df.empty and df['value'].notna().sum() >= 5:
                    df['dt'] = pd.to_datetime(df['dt']).dt.date
                    series[f'diet.{col}'] = df.set_index('dt')['value'].astype(float)
            except Exception:
                pass  # Skip if column not found

        # --- Exercise: daily aggregates ---
        ex_metrics = {
            'exercise.minutes': ('SUM', 'duration_minutes'),
            'exercise.distance': ('SUM', 'distance_km'),
            'exercise.avg_hr': ('AVG', 'avg_hr'),
            'exercise.rpe': ('AVG', 'rpe'),
        }
        for metric_name, (agg, col) in ex_metrics.items():
            df = self._query_df(
                f"SELECT {LOCAL_DATE_SQL} AS dt, {agg}({col}) AS value "
                f"FROM exercise_entries WHERE {LOCAL_DATE_SQL} >= ? GROUP BY dt ORDER BY dt",
                (cutoff,),
            )
            if not df.empty and df['value'].notna().sum() >= 5:
                df['dt'] = pd.to_datetime(df['dt']).dt.date
                series[metric_name] = df.set_index('dt')['value'].astype(float)

        # --- Exercise: daily volume load ---
        vol_df = self._query_df(
            """SELECT SUBSTR(ee.timestamp, 1, 10) AS dt,
                      SUM(ed.sets * ed.reps * ed.weight_kg) AS value
               FROM exercise_details ed
               JOIN exercise_entries ee ON ed.entry_id = ee.id
               WHERE SUBSTR(ee.timestamp, 1, 10) >= ?
               GROUP BY dt ORDER BY dt""",
            (cutoff,),
        )
        if not vol_df.empty and vol_df['value'].notna().sum() >= 5:
            vol_df['dt'] = pd.to_datetime(vol_df['dt']).dt.date
            series['exercise.volume_load'] = vol_df.set_index('dt')['value'].astype(float)

        # --- Body metrics: each metric_type separately ---
        types_df = self._query_df(
            "SELECT DISTINCT metric_type FROM body_metrics WHERE SUBSTR(timestamp, 1, 10) >= ?",
            (cutoff,),
        )
        for _, row in types_df.iterrows():
            mt = row['metric_type']
            df = self._query_df(
                "SELECT SUBSTR(timestamp, 1, 10) AS dt, AVG(value) AS value "
                "FROM body_metrics WHERE metric_type = ? AND SUBSTR(timestamp, 1, 10) >= ? "
                "GROUP BY dt ORDER BY dt",
                (mt, cutoff),
            )
            if not df.empty and df['value'].notna().sum() >= 5:
                df['dt'] = pd.to_datetime(df['dt']).dt.date
                series[mt] = df.set_index('dt')['value'].astype(float)

        # --- Biomarkers (sparse — include if enough points) ---
        markers_df = self._query_df(
            "SELECT DISTINCT marker_name FROM biomarkers WHERE SUBSTR(timestamp, 1, 10) >= ?",
            (cutoff,),
        )
        for _, row in markers_df.iterrows():
            mn = row['marker_name']
            df = self._query_df(
                "SELECT SUBSTR(timestamp, 1, 10) AS dt, value FROM biomarkers "
                "WHERE marker_name = ? AND SUBSTR(timestamp, 1, 10) >= ? ORDER BY dt",
                (mn, cutoff),
            )
            if not df.empty and len(df) >= 3:
                df['dt'] = pd.to_datetime(df['dt']).dt.date
                series[f'biomarker.{mn}'] = df.set_index('dt')['value'].astype(float)

        return series

    def _align_series(self, series1: pd.Series, series2: pd.Series,
                      lag: int = 0) -> tuple:
        """Align two date-indexed series with optional lag.

        When lag > 0, series1 is shifted forward by `lag` days relative to
        series2 (i.e., series1 value from `lag` days ago is paired with
        series2 value today). This tests whether series1 predicts series2
        after a delay.

        Returns (aligned_s1, aligned_s2) with matching indices, NaNs dropped.
        """
        if lag > 0:
            # Shift series1 index forward by lag days
            shifted_index = [d + timedelta(days=lag) if isinstance(d, date) else d
                             for d in series1.index]
            s1 = pd.Series(series1.values, index=shifted_index)
        else:
            s1 = series1.copy()

        # Intersect indices
        common = sorted(set(s1.index) & set(series2.index))
        if len(common) < 5:
            return pd.Series(dtype=float), pd.Series(dtype=float)

        a = s1.loc[common].astype(float)
        b = series2.loc[common].astype(float)

        # Drop pairs with NaN
        mask = a.notna() & b.notna()
        return a[mask], b[mask]

    def _benjamini_hochberg(self, p_values: list, alpha: float = 0.05) -> tuple:
        """Apply Benjamini-Hochberg FDR correction.

        Returns:
            (reject_mask, adjusted_p_values)
            reject_mask: list of bools — True if the corresponding test is significant
            adjusted_p_values: list of BH-adjusted p-values
        """
        n = len(p_values)
        if n == 0:
            return [], []

        # Sort p-values, track original indices
        indexed = sorted(enumerate(p_values), key=lambda x: x[1])
        adjusted = [0.0] * n
        reject = [False] * n

        # Step-up procedure
        prev_adj = 1.0
        for rank_idx in range(n - 1, -1, -1):
            orig_idx, p = indexed[rank_idx]
            rank = rank_idx + 1  # 1-based rank
            adj_p = min(prev_adj, p * n / rank)
            adj_p = min(adj_p, 1.0)
            adjusted[orig_idx] = adj_p
            reject[orig_idx] = adj_p < alpha
            prev_adj = adj_p

        return reject, adjusted

    # ------------------------------------------------------------------
    # Pairwise correlations
    # ------------------------------------------------------------------

    def pairwise_correlations(self, days: int = 90, max_lag: int = 7) -> list:
        """Scan all metric pairs for significant correlations.

        For each pair of metrics across all modules:
        1. Align time series by date
        2. Compute Pearson correlation at lags 0 through max_lag
        3. Apply Benjamini-Hochberg correction
        4. Return only significant correlations (adjusted p < 0.05, |r| > 0.3)
        """
        all_series = self._get_all_metric_series(days)
        metric_names = sorted(all_series.keys())

        if len(metric_names) < 2:
            return []

        # Collect all correlation tests
        raw_results = []
        for m1, m2 in combinations(metric_names, 2):
            s1 = all_series[m1]
            s2 = all_series[m2]
            for lag in range(0, max_lag + 1):
                a, b = self._align_series(s1, s2, lag=lag)
                if len(a) < 8:
                    continue
                r, p = scipy_stats.pearsonr(a.values, b.values)
                if np.isnan(r):
                    continue

                # Cohen's d from r: d = 2r / sqrt(1 - r^2)
                denom = np.sqrt(1 - r**2) if abs(r) < 1.0 else 1e-10
                d = 2 * r / denom

                raw_results.append({
                    'metric1': m1,
                    'metric2': m2,
                    'lag_days': lag,
                    'r': float(r),
                    'p_raw': float(p),
                    'effect_size_d': float(d),
                    'n_observations': len(a),
                    'direction': 'positive' if r > 0 else 'negative',
                })

        if not raw_results:
            return []

        # BH correction
        p_vals = [r['p_raw'] for r in raw_results]
        reject, adjusted = self._benjamini_hochberg(p_vals, alpha=0.05)

        significant = []
        for i, res in enumerate(raw_results):
            res['p_adjusted'] = round(adjusted[i], 6)
            if reject[i] and abs(res['r']) > 0.3:
                res['r'] = round(res['r'], 4)
                res['p_raw'] = round(res['p_raw'], 6)
                res['effect_size_d'] = round(res['effect_size_d'], 4)
                res['description'] = (
                    f"{res['metric1']} is {res['direction']}ly correlated with "
                    f"{res['metric2']}"
                    + (f" (lag {res['lag_days']}d)" if res['lag_days'] > 0 else "")
                    + f" (r={res['r']:.2f}, p_adj={res['p_adjusted']:.4f}, n={res['n_observations']})"
                )
                significant.append(res)

        # Sort by absolute r descending
        significant.sort(key=lambda x: abs(x['r']), reverse=True)
        return significant

    # ------------------------------------------------------------------
    # Cross-module scan
    # ------------------------------------------------------------------

    def cross_module_scan(self, days: int = 90) -> list:
        """Higher-level pattern detection across modules.

        Looks for specific cross-module relationships:
        1. Diet -> Sleep
        2. Exercise -> Sleep
        3. Diet -> Body metrics
        4. Supplement periods -> Biomarkers
        5. Exercise -> Body metrics
        """
        all_series = self._get_all_metric_series(days)
        results = []

        for pair_def in CROSS_MODULE_PAIRS:
            m1_keys = [k for k in all_series if k.startswith(pair_def['metrics1_prefix'])]
            m2_keys = [k for k in all_series if k.startswith(pair_def['metrics2_prefix'])]

            for m1 in m1_keys:
                for m2 in m2_keys:
                    best_r = 0
                    best_result = None
                    for lag in pair_def['lags']:
                        a, b = self._align_series(all_series[m1], all_series[m2], lag=lag)
                        if len(a) < 8:
                            continue
                        r, p = scipy_stats.pearsonr(a.values, b.values)
                        if np.isnan(r):
                            continue
                        if abs(r) > abs(best_r):
                            best_r = r
                            best_result = {
                                'category': pair_def['name'],
                                'description': pair_def['description'],
                                'metric1': m1,
                                'metric2': m2,
                                'lag_days': lag,
                                'r': round(float(r), 4),
                                'p_value': round(float(p), 6),
                                'n_observations': len(a),
                                'direction': 'positive' if r > 0 else 'negative',
                            }
                    if best_result and abs(best_result['r']) > 0.25 and best_result['p_value'] < 0.1:
                        # Evidence level: higher for stronger effects and more data
                        n = best_result['n_observations']
                        abs_r = abs(best_result['r'])
                        if abs_r > 0.6 and n >= 30 and best_result['p_value'] < 0.01:
                            evidence = 'high'
                        elif abs_r > 0.4 and n >= 20 and best_result['p_value'] < 0.05:
                            evidence = 'medium'
                        else:
                            evidence = 'low'
                        best_result['evidence_level'] = evidence
                        results.append(best_result)

        # --- Supplement → Biomarker special analysis ---
        # Compare biomarker values during supplement-on vs supplement-off periods
        suppl_df = self._query_df(
            "SELECT compound_name, start_date, end_date FROM supplements"
        )
        if not suppl_df.empty:
            bio_series = {k: v for k, v in all_series.items() if k.startswith('biomarker.')}
            for _, suppl_row in suppl_df.iterrows():
                compound = suppl_row['compound_name']
                start = suppl_row['start_date']
                end = suppl_row['end_date']
                if pd.isna(start):
                    continue
                start_dt = datetime.fromisoformat(start).date() if 'T' in str(start) else date.fromisoformat(str(start))
                end_dt = (datetime.fromisoformat(end).date() if 'T' in str(end) else date.fromisoformat(str(end))) if pd.notna(end) else datetime.utcnow().date()

                for bio_name, bio_s in bio_series.items():
                    on_vals = bio_s[(bio_s.index >= start_dt) & (bio_s.index <= end_dt)]
                    off_vals = bio_s[(bio_s.index < start_dt) | (bio_s.index > end_dt)]
                    if len(on_vals) >= 2 and len(off_vals) >= 2:
                        try:
                            stat, p = scipy_stats.mannwhitneyu(
                                on_vals.values, off_vals.values, alternative='two-sided',
                            )
                            if p < 0.1:
                                results.append({
                                    'category': 'supplement_biomarker',
                                    'description': f'Supplement → Biomarker: {compound} period vs {bio_name}',
                                    'metric1': f'supplement.{compound}',
                                    'metric2': bio_name,
                                    'lag_days': 0,
                                    'on_mean': round(float(on_vals.mean()), 4),
                                    'off_mean': round(float(off_vals.mean()), 4),
                                    'p_value': round(float(p), 6),
                                    'n_on': len(on_vals),
                                    'n_off': len(off_vals),
                                    'direction': 'higher_on_supplement' if on_vals.mean() > off_vals.mean() else 'lower_on_supplement',
                                    'evidence_level': 'medium' if p < 0.05 else 'low',
                                })
                        except Exception:
                            pass

        results.sort(key=lambda x: x.get('p_value', 1.0))
        return results

    # ------------------------------------------------------------------
    # Changepoint detection
    # ------------------------------------------------------------------

    def detect_changepoints(self, metric_name: str, days: int = 180) -> list:
        """Detect significant level shifts using segmented regression.

        For each candidate changepoint (every observed date), fits a
        two-segment model and compares to a single-segment model using
        an F-test. Returns changepoints significant at p < 0.05.
        """
        # Import engine's metric fetcher
        from engine import ModelingEngine
        eng = ModelingEngine(db=self.db)
        series = eng._get_metric_series(metric_name, days=days).dropna()

        if len(series) < 10:
            return []

        dates = sorted(series.index)
        y = series.loc[dates].values.astype(float)
        x = np.arange(len(y), dtype=float)
        n = len(y)

        # Null model: single linear fit
        slope_null, intercept_null, _, _, _ = scipy_stats.linregress(x, y)
        y_pred_null = intercept_null + slope_null * x
        ss_null = np.sum((y - y_pred_null) ** 2)

        changepoints = []

        # Test each candidate point (require at least 5 obs on each side)
        for cp_idx in range(5, n - 5):
            x1, y1 = x[:cp_idx], y[:cp_idx]
            x2, y2 = x[cp_idx:], y[cp_idx:]

            # Two-segment model
            try:
                res1 = scipy_stats.linregress(x1, y1)
                res2 = scipy_stats.linregress(x2, y2)
            except Exception:
                continue

            pred1 = res1.intercept + res1.slope * x1
            pred2 = res2.intercept + res2.slope * x2
            ss_seg = np.sum((y1 - pred1) ** 2) + np.sum((y2 - pred2) ** 2)

            # F-test: improvement of segmented model (2 extra params)
            df_null = n - 2  # single line: 2 params
            df_seg = n - 4   # two lines: 4 params
            if df_seg <= 0 or ss_seg <= 0:
                continue
            f_stat = ((ss_null - ss_seg) / 2) / (ss_seg / df_seg)
            p_val = 1.0 - scipy_stats.f.cdf(f_stat, 2, df_seg)

            if p_val < 0.05:
                before_mean = float(np.mean(y1))
                after_mean = float(np.mean(y2))
                change = after_mean - before_mean
                changepoints.append({
                    'date': str(dates[cp_idx]),
                    'index': cp_idx,
                    'before_mean': round(before_mean, 4),
                    'after_mean': round(after_mean, 4),
                    'change_magnitude': round(float(change), 4),
                    'pct_change': round(float(change / abs(before_mean) * 100), 2) if before_mean != 0 else 0,
                    'f_statistic': round(float(f_stat), 4),
                    'p_value': round(float(p_val), 6),
                })

        if not changepoints:
            return []

        # Keep only the most significant non-overlapping changepoints
        # (merge points within 7 days, keeping the most significant)
        changepoints.sort(key=lambda c: c['p_value'])
        filtered = []
        used_indices = set()
        for cp in changepoints:
            idx = cp['index']
            if any(abs(idx - u) < 7 for u in used_indices):
                continue
            filtered.append(cp)
            used_indices.add(idx)

        return filtered

    # ------------------------------------------------------------------
    # Trial candidates
    # ------------------------------------------------------------------

    def get_trial_candidates(self, min_effect_size: float = 0.3,
                             max_p: float = 0.05) -> list:
        """Identify patterns strong enough to warrant N-of-1 trials.

        Criteria:
        - Effect size (Cohen's d) > min_effect_size
        - p < max_p after BH correction
        - At least 30 days of baseline data for the outcome metric
        - Pattern involves modifiable behavior
        """
        # Run pairwise correlation scan
        corrs = self.pairwise_correlations(days=90, max_lag=7)

        # Modifiable prefixes — metrics the user can actually change
        modifiable_prefixes = ('diet.', 'exercise.', 'supplement.')

        candidates = []
        for c in corrs:
            if abs(c['effect_size_d']) < min_effect_size:
                continue
            if c['p_adjusted'] > max_p:
                continue

            # At least one metric must be modifiable
            m1_mod = any(c['metric1'].startswith(p) for p in modifiable_prefixes)
            m2_mod = any(c['metric2'].startswith(p) for p in modifiable_prefixes)
            if not (m1_mod or m2_mod):
                continue

            # Determine which is the intervention (modifiable) and which is the outcome
            if m1_mod and not m2_mod:
                intervention_metric = c['metric1']
                outcome_metric = c['metric2']
            elif m2_mod and not m1_mod:
                intervention_metric = c['metric2']
                outcome_metric = c['metric1']
            else:
                # Both modifiable — use the one with more data as outcome
                intervention_metric = c['metric1']
                outcome_metric = c['metric2']

            # Check baseline data availability for outcome
            from engine import ModelingEngine
            eng = ModelingEngine(db=self.db)
            try:
                outcome_series = eng._get_metric_series(outcome_metric, days=90).dropna()
                baseline_days = len(outcome_series)
            except Exception:
                baseline_days = 0

            actionable = baseline_days >= 30

            # Priority score: combine effect size, p-value, and data availability
            priority = (abs(c['effect_size_d']) * 0.4
                        + (1 - c['p_adjusted']) * 0.3
                        + min(baseline_days / 90, 1.0) * 0.3)

            # Generate intervention suggestion
            direction = 'increase' if c['direction'] == 'positive' else 'decrease'
            suggested = f"{direction.capitalize()} {intervention_metric} to affect {outcome_metric}"

            candidates.append({
                'pattern_description': c['description'],
                'intervention_metric': intervention_metric,
                'outcome_metric': outcome_metric,
                'effect_size': round(abs(c['effect_size_d']), 4),
                'p_value': c['p_adjusted'],
                'lag_days': c['lag_days'],
                'baseline_days': baseline_days,
                'actionable': actionable,
                'suggested_intervention': suggested,
                'priority_score': round(float(priority), 4),
                'sufficient_baseline': baseline_days >= 30,
            })

        candidates.sort(key=lambda x: x['priority_score'], reverse=True)
        return candidates

    # ------------------------------------------------------------------
    # Single pair correlation (for CLI)
    # ------------------------------------------------------------------

    def correlate_pair(self, metric1: str, metric2: str,
                       lags: list = None) -> list:
        """Compute correlation between two specific metrics at given lags."""
        if lags is None:
            lags = [0, 1, 2, 3]

        from engine import ModelingEngine
        eng = ModelingEngine(db=self.db)
        s1 = eng._get_metric_series(metric1, days=180).dropna()
        s2 = eng._get_metric_series(metric2, days=180).dropna()

        if s1.empty or s2.empty:
            return [{'error': 'insufficient_data',
                     'message': f'One or both metrics have no data'}]

        results = []
        for lag in lags:
            a, b = self._align_series(s1, s2, lag=lag)
            if len(a) < 5:
                results.append({
                    'lag': lag,
                    'n': len(a),
                    'error': 'insufficient_overlap',
                })
                continue

            r, p = scipy_stats.pearsonr(a.values, b.values)
            # Spearman for robustness
            rho, p_spearman = scipy_stats.spearmanr(a.values, b.values)

            results.append({
                'lag': lag,
                'n': len(a),
                'pearson_r': round(float(r), 4),
                'pearson_p': round(float(p), 6),
                'spearman_rho': round(float(rho), 4),
                'spearman_p': round(float(p_spearman), 6),
                'direction': 'positive' if r > 0 else 'negative',
                'significant_005': bool(p < 0.05),
            })

        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='TaiYiYuan Pattern Detection & Correlation Scanning',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # scan
    p_scan = sub.add_parser('scan', help='Pairwise correlation scan')
    p_scan.add_argument('--days', type=int, default=90, help='Lookback days')
    p_scan.add_argument('--max-lag', type=int, default=7, help='Maximum lag in days')

    # correlate
    p_corr = sub.add_parser('correlate', help='Correlate two specific metrics')
    p_corr.add_argument('--metric1', required=True, help='First metric name')
    p_corr.add_argument('--metric2', required=True, help='Second metric name')
    p_corr.add_argument('--lag', default='0,1,2,3', help='Comma-separated lags')

    # cross_module
    p_cross = sub.add_parser('cross_module', help='Cross-module pattern scan')
    p_cross.add_argument('--days', type=int, default=90, help='Lookback days')

    # changepoints
    p_cp = sub.add_parser('changepoints', help='Changepoint detection')
    p_cp.add_argument('--metric', required=True, help='Metric name')
    p_cp.add_argument('--days', type=int, default=180, help='Lookback days')

    # trial_candidates
    p_tc = sub.add_parser('trial_candidates', help='Identify N-of-1 trial candidates')
    p_tc.add_argument('--min-effect', type=float, default=0.3, help='Minimum effect size (Cohen d)')
    p_tc.add_argument('--max-p', type=float, default=0.05, help='Maximum adjusted p-value')

    args = parser.parse_args()

    try:
        detector = PatternDetector()

        if args.command == 'scan':
            result = detector.pairwise_correlations(days=args.days, max_lag=args.max_lag)
        elif args.command == 'correlate':
            lags = [int(l) for l in args.lag.split(',')]
            result = detector.correlate_pair(args.metric1, args.metric2, lags=lags)
        elif args.command == 'cross_module':
            result = detector.cross_module_scan(days=args.days)
        elif args.command == 'changepoints':
            result = detector.detect_changepoints(args.metric, days=args.days)
        elif args.command == 'trial_candidates':
            result = detector.get_trial_candidates(
                min_effect_size=args.min_effect, max_p=args.max_p,
            )
        else:
            result = {'error': f'Unknown command: {args.command}'}

        print(json.dumps(result, indent=2, default=_json_serial))

    except Exception as e:
        print(json.dumps({'error': str(e), 'type': type(e).__name__}, indent=2))
        sys.exit(1)


if __name__ == '__main__':
    main()
