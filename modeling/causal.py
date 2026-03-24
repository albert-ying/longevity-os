"""
太医院 (TaiYiYuan) — Causal Inference Engine

Causal analysis methods for N-of-1 trial evaluation. Includes interrupted
time series, a custom Bayesian structural time series (Kalman filter-based),
power analysis, and confounding checks.

No R or CausalImpact dependency — all methods are native Python using
numpy, scipy, and statsmodels.

Usage:
    python causal.py analyze_trial --trial_id <id>
    python causal.py its --metric <name> --intervention_date <date> --pre_days 30 --post_days 30
    python causal.py bsts --metric <name> --intervention_date <date> --pre_days 30 --post_days 30
    python causal.py power --metric <name> --baseline_days 30
    python causal.py confounders --trial_id <id>

All CLI output is JSON to stdout.
"""

import sys
import os
import json
import argparse
import sqlite3
import warnings
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

try:
    import statsmodels.api as sm
except ImportError:
    sm = None

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


def _json_serial(obj):
    """JSON serializer for numpy/pandas types."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        v = float(obj)
        return v if np.isfinite(v) else None
    if isinstance(obj, np.ndarray):
        return [_json_serial(x) for x in obj]
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    raise TypeError(f"Type {type(obj)} not serializable")


def _to_date(s: str) -> date:
    """Parse date string to date object."""
    if 'T' in str(s):
        return datetime.fromisoformat(s.replace('Z', '+00:00')).date()
    return date.fromisoformat(str(s))


LOCAL_DATE_SQL = "SUBSTR(timestamp, 1, 10)"


class CausalAnalyzer:
    """Causal inference methods for N-of-1 trial analysis."""

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

    def _get_metric_series(self, metric_name: str, days: Optional[int] = None) -> pd.Series:
        """Delegate to engine's metric fetcher."""
        _modeling_dir = os.path.dirname(__file__)
        if _modeling_dir not in sys.path:
            sys.path.insert(0, _modeling_dir)
        from engine import ModelingEngine
        eng = ModelingEngine(db=self.db)
        return eng._get_metric_series(metric_name, days=days)

    # ------------------------------------------------------------------
    # Kalman Filter — Local Linear Trend model
    # ------------------------------------------------------------------

    def _kalman_filter(self, y: np.ndarray, sigma_obs: float,
                       sigma_level: float, sigma_trend: float) -> dict:
        """Kalman filter for a local linear trend model.

        State: [level, trend]
        Observation: y_t = level_t + eps_t,   eps ~ N(0, sigma_obs^2)
        Transition:  level_t = level_{t-1} + trend_{t-1} + eta_t
                     trend_t = trend_{t-1} + zeta_t
                     eta ~ N(0, sigma_level^2), zeta ~ N(0, sigma_trend^2)

        Returns dict with:
            filtered_state: (T, 2) array of [level, trend]
            filtered_cov: (T, 2, 2) array of state covariance
            log_likelihood: float
            prediction_errors: (T,) array
            prediction_variances: (T,) array
        """
        T = len(y)
        H = np.array([[1.0, 0.0]])                 # observation matrix (1x2)
        F = np.array([[1.0, 1.0], [0.0, 1.0]])     # transition matrix (2x2)
        Q = np.array([[sigma_level**2, 0.0],
                       [0.0, sigma_trend**2]])       # state noise (2x2)
        R = sigma_obs**2                              # observation noise (scalar)

        # Initialize state: level = first observation, trend = 0
        a = np.array([y[0], 0.0])                   # state mean
        P = np.eye(2) * 1e4                          # large initial uncertainty

        filtered_state = np.zeros((T, 2))
        filtered_cov = np.zeros((T, 2, 2))
        pred_errors = np.zeros(T)
        pred_vars = np.zeros(T)
        log_lik = 0.0

        for t in range(T):
            # --- Predict ---
            if t > 0:
                a = F @ a
                P = F @ P @ F.T + Q

            # --- Update ---
            # Innovation
            v = y[t] - H @ a                   # scalar
            v = float(v)
            S = float(H @ P @ H.T + R)         # innovation variance (scalar)

            pred_errors[t] = v
            pred_vars[t] = S

            # Kalman gain (2x1)
            K = (P @ H.T) / S

            # Update state
            a = a + K.flatten() * v
            P = P - np.outer(K.flatten(), K.flatten()) * S

            filtered_state[t] = a
            filtered_cov[t] = P

            # Log-likelihood contribution
            if S > 0:
                log_lik += -0.5 * (np.log(2 * np.pi * S) + v**2 / S)

        return {
            'filtered_state': filtered_state,
            'filtered_cov': filtered_cov,
            'log_likelihood': log_lik,
            'prediction_errors': pred_errors,
            'prediction_variances': pred_vars,
            'F': F,
            'H': H,
            'Q': Q,
            'R': R,
        }

    def _kalman_smoother(self, filtered: dict) -> dict:
        """Rauch-Tung-Striebel smoother.

        Takes output of _kalman_filter and returns smoothed state estimates.
        """
        states = filtered['filtered_state']
        covs = filtered['filtered_cov']
        F = filtered['F']
        Q = filtered['Q']
        T = len(states)

        smoothed_state = np.zeros_like(states)
        smoothed_cov = np.zeros_like(covs)

        # Initialize with last filtered values
        smoothed_state[-1] = states[-1]
        smoothed_cov[-1] = covs[-1]

        for t in range(T - 2, -1, -1):
            # Predicted state/cov for t+1 given t
            a_pred = F @ states[t]
            P_pred = F @ covs[t] @ F.T + Q

            # Smoother gain
            try:
                L = covs[t] @ F.T @ np.linalg.inv(P_pred)
            except np.linalg.LinAlgError:
                L = covs[t] @ F.T @ np.linalg.pinv(P_pred)

            smoothed_state[t] = states[t] + L @ (smoothed_state[t + 1] - a_pred)
            smoothed_cov[t] = covs[t] + L @ (smoothed_cov[t + 1] - P_pred) @ L.T

        return {
            'smoothed_state': smoothed_state,
            'smoothed_cov': smoothed_cov,
        }

    def _estimate_kalman_params(self, y: np.ndarray) -> tuple:
        """Estimate noise parameters for the local linear trend model.

        Uses a simple heuristic based on the data's variance and first
        differences. More sophisticated approaches (MLE grid search) can
        be added later.
        """
        diff1 = np.diff(y)
        diff2 = np.diff(diff1)

        total_var = np.var(y) if np.var(y) > 0 else 1.0
        diff1_var = np.var(diff1) if len(diff1) > 0 and np.var(diff1) > 0 else total_var * 0.1
        diff2_var = np.var(diff2) if len(diff2) > 0 and np.var(diff2) > 0 else diff1_var * 0.1

        # Observation noise: fraction of total variance
        sigma_obs = np.sqrt(total_var * 0.3)
        # Level noise: related to first differences
        sigma_level = np.sqrt(diff1_var * 0.5)
        # Trend noise: related to second differences (small)
        sigma_trend = np.sqrt(diff2_var * 0.1)

        return sigma_obs, sigma_level, sigma_trend

    # ------------------------------------------------------------------
    # Interrupted Time Series
    # ------------------------------------------------------------------

    def interrupted_time_series(self, metric_name: str, intervention_date: str,
                                pre_days: int = 30, post_days: int = 30) -> dict:
        """Interrupted time series analysis.

        Model: Y = b0 + b1*time + b2*intervention + b3*time_after + e

        where:
            time: days from start of pre-period
            intervention: 0 before, 1 after intervention_date
            time_after: 0 before, days since intervention after
        """
        if sm is None:
            return {'error': 'statsmodels not installed',
                    'message': 'pip install statsmodels'}

        int_date = _to_date(intervention_date)
        start = int_date - timedelta(days=pre_days)
        end = int_date + timedelta(days=post_days)
        total_days = (end - start).days + 1

        series = self._get_metric_series(metric_name, days=total_days + 30)
        if series.empty:
            return {'error': 'no_data', 'n_pre': 0, 'n_post': 0}

        # Filter to the analysis window
        mask = [(d >= start and d <= end) for d in series.index]
        series = series[mask].dropna()
        if len(series) < 6:
            return {'error': 'insufficient_data', 'n_observations': len(series)}

        dates = sorted(series.index)
        y = series.loc[dates].values.astype(float)

        # Construct regressors
        time_arr = np.array([(d - start).days for d in dates], dtype=float)
        intervention = np.array([1.0 if d >= int_date else 0.0 for d in dates])
        time_after = np.array([
            float((d - int_date).days) if d >= int_date else 0.0 for d in dates
        ])

        X = np.column_stack([time_arr, intervention, time_after])
        X = sm.add_constant(X)

        try:
            model = sm.OLS(y, X).fit()
        except Exception as e:
            return {'error': f'OLS fit failed: {e}'}

        params = model.params
        bse = model.bse
        pvals = model.pvalues
        conf = model.conf_int(alpha=0.05)

        n_pre = int(np.sum(intervention == 0))
        n_post = int(np.sum(intervention == 1))

        # Pre-trend: slope = b1
        # Post-trend: slope = b1 + b3
        pre_slope = float(params[1])
        post_slope = float(params[1] + params[3])

        result = {
            'level_change': {
                'estimate': round(float(params[2]), 4),
                'se': round(float(bse[2]), 4),
                'p_value': round(float(pvals[2]), 6),
                'ci_95': [round(float(conf[2][0]), 4), round(float(conf[2][1]), 4)],
            },
            'slope_change': {
                'estimate': round(float(params[3]), 6),
                'se': round(float(bse[3]), 6),
                'p_value': round(float(pvals[3]), 6),
                'ci_95': [round(float(conf[3][0]), 6), round(float(conf[3][1]), 6)],
            },
            'pre_trend': {
                'slope': round(pre_slope, 6),
                'intercept': round(float(params[0]), 4),
            },
            'post_trend': {
                'slope': round(post_slope, 6),
                'intercept': round(float(params[0] + params[2]), 4),
            },
            'model_fit': {
                'r_squared': round(float(model.rsquared), 4),
                'adj_r_squared': round(float(model.rsquared_adj), 4),
                'aic': round(float(model.aic), 2),
                'bic': round(float(model.bic), 2),
            },
            'n_pre': n_pre,
            'n_post': n_post,
            'intervention_date': str(int_date),
        }

        return result

    # ------------------------------------------------------------------
    # Bayesian Structural Time Series (simplified)
    # ------------------------------------------------------------------

    def bayesian_structural_time_series(self, metric_name: str,
                                         intervention_date: str,
                                         pre_days: int = 30,
                                         post_days: int = 30) -> dict:
        """Simplified BSTS using Kalman filter on local linear trend model.

        1. Fit model to pre-intervention data (estimate parameters)
        2. Forecast counterfactual for post-intervention period
        3. Compare actual vs counterfactual
        4. Compute posterior probability of causal effect
        """
        int_date = _to_date(intervention_date)
        start = int_date - timedelta(days=pre_days)
        end = int_date + timedelta(days=post_days)

        series = self._get_metric_series(metric_name, days=(pre_days + post_days) + 30)
        if series.empty:
            return {'error': 'no_data'}

        # Split into pre and post
        pre_mask = [(d >= start and d < int_date) for d in series.index]
        post_mask = [(d >= int_date and d <= end) for d in series.index]
        pre_series = series[pre_mask].dropna()
        post_series = series[post_mask].dropna()

        if len(pre_series) < 7:
            return {'error': 'insufficient_pre_data', 'n_pre': len(pre_series)}
        if len(post_series) < 3:
            return {'error': 'insufficient_post_data', 'n_post': len(post_series)}

        y_pre = pre_series.values.astype(float)
        y_post = post_series.values.astype(float)

        # Estimate parameters from pre-intervention data
        sigma_obs, sigma_level, sigma_trend = self._estimate_kalman_params(y_pre)

        # Run Kalman filter on pre-intervention data
        filtered = self._kalman_filter(y_pre, sigma_obs, sigma_level, sigma_trend)
        smoothed = self._kalman_smoother(filtered)

        # Final state from pre-period
        final_state = smoothed['smoothed_state'][-1]  # [level, trend]
        final_cov = smoothed['smoothed_cov'][-1]

        F = filtered['F']
        Q = filtered['Q']
        H = filtered['H']
        R = filtered['R']

        # Forecast counterfactual for post-period
        n_post = len(y_post)
        cf_mean = np.zeros(n_post)
        cf_var = np.zeros(n_post)

        state = final_state.copy()
        P = final_cov.copy()

        for t in range(n_post):
            # Predict state forward
            state = F @ state
            P = F @ P @ F.T + Q

            # Observation prediction
            cf_mean[t] = float(H @ state)
            cf_var[t] = float(H @ P @ H.T + R)

        cf_std = np.sqrt(np.maximum(cf_var, 1e-10))

        # Pointwise effects: actual - counterfactual
        pointwise_effect = y_post - cf_mean

        # Average and cumulative effects
        avg_effect = float(np.mean(pointwise_effect))
        cum_effect = float(np.sum(pointwise_effect))

        # Standard errors for average/cumulative (propagated from forecast uncertainty)
        avg_se = float(np.sqrt(np.mean(cf_var)) / np.sqrt(n_post))
        cum_se = float(np.sqrt(np.sum(cf_var)))

        # Relative effect
        avg_cf = float(np.mean(cf_mean))
        rel_effect = (avg_effect / abs(avg_cf) * 100) if avg_cf != 0 else 0.0
        rel_se = (avg_se / abs(avg_cf) * 100) if avg_cf != 0 else 0.0

        # Posterior probability of positive/negative effect
        # Using normal approximation
        if avg_se > 0:
            z = avg_effect / avg_se
            prob_positive = float(scipy_stats.norm.cdf(z))
            prob_negative = 1.0 - prob_positive
        else:
            prob_positive = 1.0 if avg_effect > 0 else 0.0
            prob_negative = 1.0 - prob_positive

        # Counterfactual details for plotting
        post_dates = sorted(post_series.index)
        counterfactual = []
        for i in range(n_post):
            counterfactual.append({
                'date': str(post_dates[i]),
                'predicted': round(float(cf_mean[i]), 4),
                'actual': round(float(y_post[i]), 4),
                'pointwise_effect': round(float(pointwise_effect[i]), 4),
                'ci_95': [round(float(cf_mean[i] - 1.96 * cf_std[i]), 4),
                          round(float(cf_mean[i] + 1.96 * cf_std[i]), 4)],
            })

        # Model diagnostics on pre-period (in-sample)
        pre_pred = smoothed['smoothed_state'][:, 0]  # level = predicted observation
        pre_errors = y_pre - pre_pred
        mape = float(np.mean(np.abs(pre_errors / np.where(y_pre != 0, y_pre, 1.0))) * 100)

        # Coverage: what fraction of post observations fall within counterfactual 95% CI?
        in_ci = sum(1 for i in range(n_post)
                     if cf_mean[i] - 1.96 * cf_std[i] <= y_post[i] <= cf_mean[i] + 1.96 * cf_std[i])
        coverage = in_ci / n_post if n_post > 0 else 0.0

        return {
            'average_effect': {
                'estimate': round(avg_effect, 4),
                'se': round(avg_se, 4),
                'ci_95': [round(avg_effect - 1.96 * avg_se, 4),
                          round(avg_effect + 1.96 * avg_se, 4)],
            },
            'cumulative_effect': {
                'estimate': round(cum_effect, 4),
                'se': round(cum_se, 4),
                'ci_95': [round(cum_effect - 1.96 * cum_se, 4),
                          round(cum_effect + 1.96 * cum_se, 4)],
            },
            'relative_effect_pct': {
                'estimate': round(rel_effect, 2),
                'ci_95': [round(rel_effect - 1.96 * rel_se, 2),
                          round(rel_effect + 1.96 * rel_se, 2)],
            },
            'posterior_prob_positive': round(prob_positive, 4),
            'posterior_prob_negative': round(prob_negative, 4),
            'counterfactual': counterfactual,
            'model_diagnostics': {
                'mape': round(mape, 2),
                'coverage': round(coverage, 4),
                'n_pre': len(y_pre),
                'n_post': n_post,
            },
        }

    # ------------------------------------------------------------------
    # Confounding check
    # ------------------------------------------------------------------

    def confounding_check(self, trial_id: int) -> list:
        """Check if other tracked variables changed significantly during trial.

        For each non-primary metric, compares distribution before vs during
        the trial period using a Mann-Whitney U test.
        """
        trial = self._query_df(
            "SELECT * FROM trials WHERE id = ?", (trial_id,)
        )
        if trial.empty:
            return [{'error': f'Trial {trial_id} not found'}]

        trial_row = trial.iloc[0]
        primary = trial_row['primary_outcome_metric']
        start = trial_row['start_date']
        end = trial_row['end_date']
        intervention = trial_row['intervention']

        if pd.isna(start):
            return [{'error': 'Trial has no start_date'}]
        start_dt = _to_date(start)
        end_dt = _to_date(end) if pd.notna(end) else datetime.utcnow().date()
        trial_days = (end_dt - start_dt).days

        # Get all tracked metric types
        types_df = self._query_df(
            "SELECT DISTINCT metric_type FROM body_metrics"
        )

        confounders = []
        for _, row in types_df.iterrows():
            mt = row['metric_type']
            if mt == primary:
                continue

            # Values during trial
            during = self._query_df(
                "SELECT value FROM body_metrics WHERE metric_type = ? AND SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?",
                (mt, str(start_dt), str(end_dt)),
            )
            # Values in equivalent period before trial
            pre_start = start_dt - timedelta(days=trial_days)
            before = self._query_df(
                "SELECT value FROM body_metrics WHERE metric_type = ? AND SUBSTR(timestamp, 1, 10) BETWEEN ? AND ?",
                (mt, str(pre_start), str(start_dt - timedelta(days=1))),
            )

            if len(during) < 3 or len(before) < 3:
                continue

            d_vals = during['value'].values.astype(float)
            b_vals = before['value'].values.astype(float)

            try:
                stat, p = scipy_stats.mannwhitneyu(b_vals, d_vals, alternative='two-sided')
            except Exception:
                continue

            changed = bool(p < 0.05)
            confounders.append({
                'variable': mt,
                'changed': changed,
                'p_value': round(float(p), 6),
                'before_mean': round(float(np.mean(b_vals)), 4),
                'during_mean': round(float(np.mean(d_vals)), 4),
                'before_n': len(b_vals),
                'during_n': len(d_vals),
                'details': (
                    f'{mt} {"changed significantly" if changed else "did not change"} '
                    f'(before: {np.mean(b_vals):.2f}, during: {np.mean(d_vals):.2f}, p={p:.4f})'
                ),
            })

        # Also check diet and exercise patterns
        for label, sql_before, sql_during in [
            (
                'daily_calories',
                "SELECT SUM(total_calories) AS v FROM diet_entries WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ? GROUP BY SUBSTR(timestamp, 1, 10)",
                "SELECT SUM(total_calories) AS v FROM diet_entries WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ? GROUP BY SUBSTR(timestamp, 1, 10)",
            ),
            (
                'exercise_minutes',
                "SELECT SUM(duration_minutes) AS v FROM exercise_entries WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ? GROUP BY SUBSTR(timestamp, 1, 10)",
                "SELECT SUM(duration_minutes) AS v FROM exercise_entries WHERE SUBSTR(timestamp, 1, 10) BETWEEN ? AND ? GROUP BY SUBSTR(timestamp, 1, 10)",
            ),
        ]:
            pre_start = start_dt - timedelta(days=trial_days)
            b_df = self._query_df(sql_before, (str(pre_start), str(start_dt - timedelta(days=1))))
            d_df = self._query_df(sql_during, (str(start_dt), str(end_dt)))

            if len(b_df) < 3 or len(d_df) < 3:
                continue

            b_vals = b_df['v'].dropna().values.astype(float)
            d_vals = d_df['v'].dropna().values.astype(float)

            if len(b_vals) < 3 or len(d_vals) < 3:
                continue

            try:
                stat, p = scipy_stats.mannwhitneyu(b_vals, d_vals, alternative='two-sided')
            except Exception:
                continue

            changed = bool(p < 0.05)
            confounders.append({
                'variable': label,
                'changed': changed,
                'p_value': round(float(p), 6),
                'before_mean': round(float(np.mean(b_vals)), 4),
                'during_mean': round(float(np.mean(d_vals)), 4),
                'before_n': len(b_vals),
                'during_n': len(d_vals),
                'details': (
                    f'{label} {"changed significantly" if changed else "did not change"} '
                    f'(before: {np.mean(b_vals):.2f}, during: {np.mean(d_vals):.2f}, p={p:.4f})'
                ),
            })

        return confounders

    # ------------------------------------------------------------------
    # Power analysis
    # ------------------------------------------------------------------

    def power_analysis(self, metric_name: str, baseline_days: int = 30,
                       alpha: float = 0.05, power: float = 0.8) -> dict:
        """Estimate minimum detectable effect size given baseline variability.

        Uses within-person SD from baseline data and computes the MDE
        for a paired-design t-test.
        """
        series = self._get_metric_series(metric_name, days=baseline_days + 30)
        if series.empty:
            return {'error': 'no_data', 'metric': metric_name}

        # Use the most recent baseline_days of data
        series = series.tail(baseline_days).dropna()
        n = len(series)

        if n < 5:
            return {
                'error': 'insufficient_data',
                'n_baseline_observations': n,
                'message': f'Need at least 5 observations, have {n}',
            }

        vals = series.values.astype(float)
        baseline_mean = float(np.mean(vals))
        baseline_sd = float(np.std(vals, ddof=1))

        # Within-person SD: use RMSSD (root mean square of successive differences)
        # for a more robust estimate of day-to-day variability
        diffs = np.diff(vals)
        within_sd = float(np.sqrt(np.mean(diffs ** 2) / 2))

        if within_sd == 0:
            within_sd = baseline_sd  # fallback

        # MDE for paired t-test: d = t_crit * sigma / sqrt(n)
        # where t_crit comes from the non-central t distribution
        # Simplified: use the formula MDE_d = (z_alpha + z_beta) / sqrt(n)
        z_alpha = float(scipy_stats.norm.ppf(1 - alpha / 2))
        z_beta = float(scipy_stats.norm.ppf(power))

        # For a given n (phase duration), MDE in Cohen's d units
        mde_d = (z_alpha + z_beta) / np.sqrt(n)
        mde_absolute = mde_d * within_sd
        mde_pct = (mde_absolute / abs(baseline_mean) * 100) if baseline_mean != 0 else 0.0

        # Recommended phase duration to detect a medium effect (d=0.5)
        target_d = 0.5
        recommended_n = int(np.ceil(((z_alpha + z_beta) / target_d) ** 2))

        sufficient = n >= 30

        return {
            'metric': metric_name,
            'baseline_mean': round(baseline_mean, 4),
            'baseline_sd': round(baseline_sd, 4),
            'within_person_sd': round(within_sd, 4),
            'n_baseline_observations': n,
            'mde_cohens_d': round(float(mde_d), 4),
            'mde_absolute': round(float(mde_absolute), 4),
            'mde_pct_change': round(float(mde_pct), 2),
            'recommended_phase_duration': recommended_n,
            'sufficient_baseline': sufficient,
            'alpha': alpha,
            'power': power,
            'message': (
                f'With {n} baseline observations (within-person SD={within_sd:.3f}), '
                f'the minimum detectable effect is {mde_d:.2f} SD ({mde_absolute:.3f} absolute, '
                f'{mde_pct:.1f}%). '
                f'{"Baseline is sufficient (>= 30 days)." if sufficient else f"Need {30 - n} more days of baseline."} '
                f'To detect a medium effect (d=0.5), {recommended_n} observations per phase are recommended.'
            ),
        }

    # ------------------------------------------------------------------
    # Full trial analysis
    # ------------------------------------------------------------------

    def analyze_trial(self, trial_id: int) -> dict:
        """Full analysis of a completed N-of-1 trial.

        Performs descriptive stats, paired testing (t-test or Wilcoxon),
        Cohen's d with CI, ITS, BSTS, compliance-weighted analysis,
        and confounding checks.
        """
        # Load trial metadata
        trial = self._query_df("SELECT * FROM trials WHERE id = ?", (trial_id,))
        if trial.empty:
            return {'error': f'Trial {trial_id} not found'}

        tr = trial.iloc[0]
        primary_metric = tr['primary_outcome_metric']
        design = tr['design']

        # Load observations
        obs = self._query_df(
            """SELECT date, phase, metric_name, value, compliance_score
               FROM trial_observations
               WHERE trial_id = ? AND metric_name = ?
               ORDER BY date""",
            (trial_id, primary_metric),
        )

        if obs.empty:
            return {
                'trial_id': trial_id,
                'trial_name': tr['name'],
                'insufficient_data': True,
                'insufficient_data_reason': 'No observations found for primary outcome metric',
            }

        # Phase-level descriptive stats
        phases = []
        phase_data = {}
        for phase_name, grp in obs.groupby('phase'):
            vals = grp['value'].dropna().values.astype(float)
            compliance = grp['compliance_score'].dropna().values.astype(float)
            phase_info = {
                'name': phase_name,
                'mean': round(float(np.mean(vals)), 4) if len(vals) > 0 else None,
                'sd': round(float(np.std(vals, ddof=1)), 4) if len(vals) > 1 else None,
                'n': len(vals),
                'values': [round(float(v), 4) for v in vals],
                'dates': grp['date'].tolist(),
                'avg_compliance': round(float(np.mean(compliance)), 4) if len(compliance) > 0 else None,
            }
            phases.append(phase_info)
            phase_data[phase_name] = vals

        # Identify baseline and intervention phases
        baseline_vals = phase_data.get('baseline', np.array([]))
        intervention_vals = phase_data.get('intervention', np.array([]))

        # For ABA design, combine both baseline phases
        if design == 'ABA' and 'control' in phase_data:
            baseline_vals = np.concatenate([baseline_vals, phase_data['control']])

        insufficient = len(baseline_vals) < 3 or len(intervention_vals) < 3
        if insufficient:
            return {
                'trial_id': trial_id,
                'trial_name': tr['name'],
                'design': design,
                'phases': phases,
                'insufficient_data': True,
                'insufficient_data_reason': (
                    f'Baseline has {len(baseline_vals)} observations, '
                    f'intervention has {len(intervention_vals)} observations. Need >= 3 each.'
                ),
            }

        # --- Statistical comparison ---
        # Normality check
        normal_baseline = True
        normal_intervention = True
        if len(baseline_vals) >= 8:
            _, p_shapiro_b = scipy_stats.shapiro(baseline_vals)
            normal_baseline = p_shapiro_b > 0.05
        if len(intervention_vals) >= 8:
            _, p_shapiro_i = scipy_stats.shapiro(intervention_vals)
            normal_intervention = p_shapiro_i > 0.05

        both_normal = normal_baseline and normal_intervention

        if both_normal:
            # Independent t-test (unequal var)
            stat, p_val = scipy_stats.ttest_ind(intervention_vals, baseline_vals, equal_var=False)
            test_used = 'Welch t-test'
        else:
            # Mann-Whitney U (independent samples since phases aren't naturally paired)
            stat, p_val = scipy_stats.mannwhitneyu(
                intervention_vals, baseline_vals, alternative='two-sided',
            )
            test_used = 'Mann-Whitney U'

        # Cohen's d with 95% CI (pooled SD)
        n1, n2 = len(intervention_vals), len(baseline_vals)
        m1, m2 = float(np.mean(intervention_vals)), float(np.mean(baseline_vals))
        s1 = float(np.std(intervention_vals, ddof=1)) if n1 > 1 else 0.0
        s2 = float(np.std(baseline_vals, ddof=1)) if n2 > 1 else 0.0

        pooled_sd = np.sqrt(((n1 - 1) * s1**2 + (n2 - 1) * s2**2) / (n1 + n2 - 2))
        cohens_d = (m1 - m2) / pooled_sd if pooled_sd > 0 else 0.0

        # CI for Cohen's d (approximate)
        se_d = np.sqrt((n1 + n2) / (n1 * n2) + cohens_d**2 / (2 * (n1 + n2)))
        ci_low = cohens_d - 1.96 * se_d
        ci_high = cohens_d + 1.96 * se_d

        comparison = {
            'test_used': test_used,
            'statistic': round(float(stat), 4),
            'p_value': round(float(p_val), 6),
            'effect_size_d': round(float(cohens_d), 4),
            'ci_95': [round(float(ci_low), 4), round(float(ci_high), 4)],
            'baseline_mean': round(m2, 4),
            'intervention_mean': round(m1, 4),
            'difference': round(m1 - m2, 4),
            'normality': {
                'baseline_normal': normal_baseline,
                'intervention_normal': normal_intervention,
            },
        }

        # --- ITS analysis (if we have dates) ---
        its_results = None
        if pd.notna(tr['start_date']):
            # Find the intervention start date from observations
            int_obs = obs[obs['phase'] == 'intervention']
            if not int_obs.empty:
                intervention_start = int_obs['date'].min()
                baseline_obs = obs[obs['phase'] == 'baseline']
                pre_days_actual = len(baseline_obs)
                post_days_actual = len(int_obs)
                try:
                    its_results = self.interrupted_time_series(
                        primary_metric, intervention_start,
                        pre_days=max(pre_days_actual, 14),
                        post_days=max(post_days_actual, 14),
                    )
                except Exception as e:
                    its_results = {'error': str(e)}

        # --- BSTS analysis ---
        bsts_results = None
        if pd.notna(tr['start_date']):
            int_obs = obs[obs['phase'] == 'intervention']
            if not int_obs.empty:
                intervention_start = int_obs['date'].min()
                baseline_obs = obs[obs['phase'] == 'baseline']
                try:
                    bsts_results = self.bayesian_structural_time_series(
                        primary_metric, intervention_start,
                        pre_days=max(len(baseline_obs), 14),
                        post_days=max(len(int_obs), 14),
                    )
                except Exception as e:
                    bsts_results = {'error': str(e)}

        # --- Compliance-weighted analysis ---
        obs_with_compliance = obs[obs['compliance_score'].notna()].copy()
        compliance_info = {'overall': None, 'by_phase': []}
        if not obs_with_compliance.empty:
            compliance_info['overall'] = round(
                float(obs_with_compliance['compliance_score'].mean()), 4
            )
            for phase_name, grp in obs_with_compliance.groupby('phase'):
                compliance_info['by_phase'].append({
                    'phase': phase_name,
                    'avg_compliance': round(float(grp['compliance_score'].mean()), 4),
                    'n': len(grp),
                })

        # --- Confounding check ---
        try:
            confounders = self.confounding_check(trial_id)
        except Exception:
            confounders = []

        # --- Conclusion ---
        abs_d = abs(cohens_d)
        if abs_d < 0.2:
            size_interp = 'negligible'
        elif abs_d < 0.5:
            size_interp = 'small'
        elif abs_d < 0.8:
            size_interp = 'medium'
        else:
            size_interp = 'large'

        # Confidence based on multiple signals
        effect_detected = p_val < 0.05
        signals = 0
        if effect_detected:
            signals += 1
        if its_results and not its_results.get('error') and its_results.get('level_change', {}).get('p_value', 1) < 0.05:
            signals += 1
        if bsts_results and not bsts_results.get('error'):
            prob = max(
                bsts_results.get('posterior_prob_positive', 0),
                bsts_results.get('posterior_prob_negative', 0),
            )
            if prob > 0.95:
                signals += 1

        has_confounders = any(c.get('changed', False) for c in confounders)

        if signals >= 2 and not has_confounders:
            confidence = 'high'
        elif signals >= 1 and not has_confounders:
            confidence = 'medium'
        else:
            confidence = 'low'

        # Recommendation
        if effect_detected and abs_d >= 0.5 and confidence in ('medium', 'high'):
            recommendation = 'adopt'
        elif effect_detected and confidence == 'low':
            recommendation = 'extend'
        elif effect_detected and abs_d < 0.5:
            recommendation = 'extend'
        elif not effect_detected and abs_d < 0.2:
            recommendation = 'abandon'
        else:
            recommendation = 'modify'

        # Plain language summary
        direction_word = 'increased' if m1 > m2 else 'decreased'
        plain = (
            f"{tr['intervention']} was associated with a {direction_word} "
            f"{primary_metric} (intervention: {m1:.2f} vs baseline: {m2:.2f}, "
            f"d={cohens_d:.2f}, p={p_val:.4f})."
        )
        if not effect_detected:
            plain = (
                f"No statistically significant effect of {tr['intervention']} on "
                f"{primary_metric} was detected (d={cohens_d:.2f}, p={p_val:.4f})."
            )

        caveats = []
        if has_confounders:
            changed_vars = [c['variable'] for c in confounders if c.get('changed')]
            caveats.append(f'Potential confounders changed during trial: {", ".join(changed_vars)}')
        if n1 < 14 or n2 < 14:
            caveats.append(f'Small sample size (baseline: {n2}, intervention: {n1})')
        if compliance_info['overall'] and compliance_info['overall'] < 0.8:
            caveats.append(f'Low overall compliance ({compliance_info["overall"]:.0%})')

        conclusion = {
            'effect_detected': effect_detected,
            'effect_size_interpretation': size_interp,
            'confidence': confidence,
            'recommendation': recommendation,
            'plain_summary': plain,
            'caveats': caveats,
        }

        return {
            'trial_id': trial_id,
            'trial_name': tr['name'],
            'design': design,
            'phases': phases,
            'comparison': comparison,
            'its_results': its_results,
            'bsts_results': bsts_results,
            'compliance': compliance_info,
            'confounders': confounders,
            'conclusion': conclusion,
            'insufficient_data': False,
            'insufficient_data_reason': None,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='TaiYiYuan Causal Inference Engine',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command', required=True)

    # analyze_trial
    p_at = sub.add_parser('analyze_trial', help='Full N-of-1 trial analysis')
    p_at.add_argument('--trial_id', type=int, required=True, help='Trial ID')

    # its
    p_its = sub.add_parser('its', help='Interrupted time series analysis')
    p_its.add_argument('--metric', required=True, help='Metric name')
    p_its.add_argument('--intervention_date', required=True, help='Intervention date (YYYY-MM-DD)')
    p_its.add_argument('--pre_days', type=int, default=30, help='Pre-intervention days')
    p_its.add_argument('--post_days', type=int, default=30, help='Post-intervention days')

    # bsts
    p_bsts = sub.add_parser('bsts', help='Bayesian structural time series')
    p_bsts.add_argument('--metric', required=True, help='Metric name')
    p_bsts.add_argument('--intervention_date', required=True, help='Intervention date (YYYY-MM-DD)')
    p_bsts.add_argument('--pre_days', type=int, default=30, help='Pre-intervention days')
    p_bsts.add_argument('--post_days', type=int, default=30, help='Post-intervention days')

    # power
    p_pow = sub.add_parser('power', help='Power analysis for a metric')
    p_pow.add_argument('--metric', required=True, help='Metric name')
    p_pow.add_argument('--baseline_days', type=int, default=30, help='Baseline period in days')
    p_pow.add_argument('--alpha', type=float, default=0.05, help='Significance level')
    p_pow.add_argument('--power', type=float, default=0.8, help='Desired power')

    # confounders
    p_conf = sub.add_parser('confounders', help='Confounding check for a trial')
    p_conf.add_argument('--trial_id', type=int, required=True, help='Trial ID')

    args = parser.parse_args()

    try:
        analyzer = CausalAnalyzer()

        if args.command == 'analyze_trial':
            result = analyzer.analyze_trial(args.trial_id)
        elif args.command == 'its':
            result = analyzer.interrupted_time_series(
                args.metric, args.intervention_date,
                pre_days=args.pre_days, post_days=args.post_days,
            )
        elif args.command == 'bsts':
            result = analyzer.bayesian_structural_time_series(
                args.metric, args.intervention_date,
                pre_days=args.pre_days, post_days=args.post_days,
            )
        elif args.command == 'power':
            result = analyzer.power_analysis(
                args.metric, baseline_days=args.baseline_days,
                alpha=args.alpha, power=args.power,
            )
        elif args.command == 'confounders':
            result = analyzer.confounding_check(args.trial_id)
        else:
            result = {'error': f'Unknown command: {args.command}'}

        print(json.dumps(result, indent=2, default=_json_serial))

    except Exception as e:
        print(json.dumps({'error': str(e), 'type': type(e).__name__}, indent=2))
        sys.exit(1)


if __name__ == '__main__':
    main()
