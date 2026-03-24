#!/usr/bin/env python3
"""
太医院 (Tai Yi Yuan) — Dashboard Server

Stdlib-only HTTP server that serves the dashboard and provides API endpoints
for health data visualization.

Usage:
    python server.py              # Start on port 8420
    python server.py --port 9000  # Custom port
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paths import get_db_path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DATABASE = get_db_path()
DASHBOARD_HTML = Path(__file__).parent / "dashboard.html"
DEFAULT_PORT = 8420


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db() -> sqlite3.Connection:
    """Open a connection with row_factory so we get dicts."""
    if not DATABASE.exists():
        raise FileNotFoundError(
            f"Database not found at {DATABASE}. "
            "Run the taiyiyuan skill to initialize it first."
        )
    conn = sqlite3.connect(str(DATABASE))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]


def safe_float(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------
class TaiYiYuanHandler(BaseHTTPRequestHandler):
    """Routes requests to the dashboard HTML or API endpoints."""

    # Suppress default request logging for cleaner output
    def log_message(self, fmt, *args):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {args[0]}" if args else f"[{ts}] {fmt}")

    # ----- Routing -----------------------------------------------------------

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        routes = {
            "": self._serve_dashboard,
            "/api/daily-summary": self._handle_daily_summary,
            "/api/nutrition": self._handle_nutrition,
            "/api/metrics": self._handle_metrics,
            "/api/exercises": self._handle_exercises,
            "/api/supplements": self._handle_supplements,
            "/api/trials": self._handle_trials,
            "/api/insights": self._handle_insights,
            "/api/biomarkers": self._handle_biomarkers,
        }

        handler = routes.get(path)
        if handler:
            try:
                handler(params)
            except FileNotFoundError as e:
                self._json_error(503, str(e))
            except Exception as e:
                self._json_error(500, f"Internal error: {e}")
        elif path.startswith("/docs/"):
            self._serve_static(path)
        else:
            self._json_error(404, f"Not found: {self.path}")

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(200)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    # ----- Response helpers --------------------------------------------------

    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_error(self, status, message):
        self._send_json({"error": message}, status)

    def _serve_static(self, path):
        """Serve static files from project root (docs/, etc.)."""
        project_root = Path(__file__).parent.parent
        file_path = project_root / path.lstrip("/")
        if not file_path.exists() or not file_path.is_file():
            self._json_error(404, f"Not found: {path}")
            return
        # Prevent directory traversal
        try:
            file_path.resolve().relative_to(project_root.resolve())
        except ValueError:
            self._json_error(403, "Forbidden")
            return
        content_types = {
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".css": "text/css",
            ".js": "application/javascript",
        }
        ct = content_types.get(file_path.suffix, "application/octet-stream")
        body = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _serve_dashboard(self, _params):
        if not DASHBOARD_HTML.exists():
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"dashboard.html not found")
            return
        body = DASHBOARD_HTML.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ----- API handlers ------------------------------------------------------

    def _handle_daily_summary(self, params):
        """
        GET /api/daily-summary?date=YYYY-MM-DD

        Returns today's totals plus 7-day averages for key metrics.
        """
        date = params.get("date", today_str())
        conn = get_db()
        try:
            # Diet totals for the day
            row = conn.execute(
                """
                SELECT
                    COALESCE(SUM(total_calories), 0) AS calories,
                    COALESCE(SUM(total_protein_g), 0) AS protein_g,
                    COALESCE(SUM(total_carbs_g), 0) AS carbs_g,
                    COALESCE(SUM(total_fat_g), 0) AS fat_g
                FROM diet_entries
                WHERE date(timestamp) = ?
                """,
                (date,),
            ).fetchone()

            calories = dict(row)["calories"] if row else 0
            protein_g = dict(row)["protein_g"] if row else 0

            # 7-day averages for diet
            avg_row = conn.execute(
                """
                SELECT
                    AVG(daily_cal) AS avg_cal,
                    AVG(daily_pro) AS avg_pro
                FROM (
                    SELECT
                        date(timestamp) AS d,
                        SUM(total_calories) AS daily_cal,
                        SUM(total_protein_g) AS daily_pro
                    FROM diet_entries
                    WHERE date(timestamp) BETWEEN date(?, '-6 days') AND ?
                    GROUP BY d
                )
                """,
                (date, date),
            ).fetchone()
            avg_cal = dict(avg_row)["avg_cal"] if avg_row else None
            avg_pro = dict(avg_row)["avg_pro"] if avg_row else None

            # Exercise minutes for the day
            ex_row = conn.execute(
                """
                SELECT COALESCE(SUM(duration_minutes), 0) AS total_min
                FROM exercise_entries
                WHERE date(timestamp) = ?
                """,
                (date,),
            ).fetchone()
            exercise_min = dict(ex_row)["total_min"] if ex_row else 0

            # 7-day average exercise
            ex_avg = conn.execute(
                """
                SELECT AVG(daily_min) AS avg_min FROM (
                    SELECT date(timestamp) AS d, SUM(duration_minutes) AS daily_min
                    FROM exercise_entries
                    WHERE date(timestamp) BETWEEN date(?, '-6 days') AND ?
                    GROUP BY d
                )
                """,
                (date, date),
            ).fetchone()
            avg_ex = dict(ex_avg)["avg_min"] if ex_avg else None

            # Sleep hours (from body_metrics where metric_type = 'sleep_hours')
            sleep_row = conn.execute(
                """
                SELECT value FROM body_metrics
                WHERE metric_type = 'sleep_hours' AND date(timestamp) = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (date,),
            ).fetchone()
            sleep_hours = dict(sleep_row)["value"] if sleep_row else None

            # 7-day average sleep
            sleep_avg = conn.execute(
                """
                SELECT AVG(value) AS avg_sleep FROM body_metrics
                WHERE metric_type = 'sleep_hours'
                AND date(timestamp) BETWEEN date(?, '-6 days') AND ?
                """,
                (date, date),
            ).fetchone()
            avg_sleep = dict(sleep_avg)["avg_sleep"] if sleep_avg else None

            self._send_json(
                {
                    "date": date,
                    "calories": calories,
                    "protein_g": protein_g,
                    "calories_7d_avg": safe_float(avg_cal),
                    "protein_7d_avg": safe_float(avg_pro),
                    "exercise_minutes": exercise_min,
                    "exercise_7d_avg": safe_float(avg_ex),
                    "sleep_hours": sleep_hours,
                    "sleep_7d_avg": safe_float(avg_sleep),
                }
            )
        finally:
            conn.close()

    def _handle_nutrition(self, params):
        """
        GET /api/nutrition?start=YYYY-MM-DD&end=YYYY-MM-DD

        Returns daily macro breakdown for the date range.
        """
        start = params.get("start", days_ago(14))
        end = params.get("end", today_str())
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT
                    date(timestamp) AS date,
                    SUM(total_calories) AS calories,
                    SUM(total_protein_g) AS protein_g,
                    SUM(total_carbs_g) AS carbs_g,
                    SUM(total_fat_g) AS fat_g,
                    SUM(total_fiber_g) AS fiber_g
                FROM diet_entries
                WHERE date(timestamp) BETWEEN ? AND ?
                GROUP BY date(timestamp)
                ORDER BY date(timestamp)
                """,
                (start, end),
            ).fetchall()
            self._send_json(rows_to_dicts(rows))
        finally:
            conn.close()

    def _handle_metrics(self, params):
        """
        GET /api/metrics?type=<metric>&start=YYYY-MM-DD&end=YYYY-MM-DD

        Returns time series for a given metric type.
        """
        metric_type = params.get("type", "weight")
        start = params.get("start", days_ago(30))
        end = params.get("end", today_str())
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT timestamp, value, unit, context
                FROM body_metrics
                WHERE metric_type = ?
                AND date(timestamp) BETWEEN ? AND ?
                ORDER BY timestamp
                """,
                (metric_type, start, end),
            ).fetchall()
            self._send_json(rows_to_dicts(rows))
        finally:
            conn.close()

    def _handle_exercises(self, params):
        """
        GET /api/exercises?start=YYYY-MM-DD&end=YYYY-MM-DD

        Returns exercise entries in the range, newest first.
        """
        start = params.get("start", days_ago(14))
        end = params.get("end", today_str())
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT timestamp, activity_type, duration_minutes,
                       distance_km, avg_hr, rpe, notes
                FROM exercise_entries
                WHERE date(timestamp) BETWEEN ? AND ?
                ORDER BY timestamp DESC
                LIMIT 50
                """,
                (start, end),
            ).fetchall()
            self._send_json(rows_to_dicts(rows))
        finally:
            conn.close()

    def _handle_supplements(self, params):
        """
        GET /api/supplements

        Returns currently active supplements (end_date is NULL).
        """
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT compound_name AS name, dosage, dosage_unit,
                       frequency, timing, start_date, reason
                FROM supplements
                WHERE end_date IS NULL
                ORDER BY compound_name
                """
            ).fetchall()
            self._send_json(rows_to_dicts(rows))
        finally:
            conn.close()

    def _handle_trials(self, params):
        """
        GET /api/trials

        Returns all non-abandoned trials with progress calculation.
        """
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT id, name, hypothesis, status, design,
                       phase_duration_days, start_date, end_date
                FROM trials
                WHERE status != 'abandoned'
                ORDER BY
                    CASE status
                        WHEN 'active' THEN 0
                        WHEN 'approved' THEN 1
                        WHEN 'proposed' THEN 2
                        WHEN 'completed' THEN 3
                    END,
                    created_at DESC
                """
            ).fetchall()

            results = []
            for r in rows:
                d = dict(r)
                # Calculate progress percentage for active trials
                progress_pct = 0
                phase = None
                if d["status"] == "active" and d["start_date"]:
                    try:
                        start = datetime.strptime(d["start_date"][:10], "%Y-%m-%d")
                        total_days = d["phase_duration_days"] * 3  # ABA = 3 phases
                        if d["design"] == "crossover":
                            total_days = d["phase_duration_days"] * 4
                        elapsed = (datetime.now() - start).days
                        progress_pct = min(100, max(0, round(elapsed / total_days * 100)))
                        # Determine current phase
                        pd = d["phase_duration_days"]
                        if elapsed < pd:
                            phase = "baseline"
                        elif elapsed < pd * 2:
                            phase = "intervention"
                        else:
                            phase = "washout" if d["design"] == "ABA" else "control"
                    except (ValueError, TypeError, ZeroDivisionError):
                        pass
                elif d["status"] == "completed":
                    progress_pct = 100

                d["progress_pct"] = progress_pct
                d["phase"] = phase
                results.append(d)

            self._send_json(results)
        finally:
            conn.close()

    def _handle_insights(self, params):
        """
        GET /api/insights?days=7

        Returns recent insights from the modeling engine.
        """
        days = int(params.get("days", 7))
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT timestamp, insight_type AS type, description,
                       confidence_level AS confidence, effect_size,
                       actionable, trial_candidate
                FROM insights
                WHERE date(timestamp) >= date('now', ?)
                ORDER BY timestamp DESC
                LIMIT 20
                """,
                (f"-{days} days",),
            ).fetchall()
            self._send_json(rows_to_dicts(rows))
        finally:
            conn.close()

    def _handle_biomarkers(self, params):
        """
        GET /api/biomarkers?marker=<name>&start=YYYY-MM-DD&end=YYYY-MM-DD

        Returns biomarker history with reference ranges.
        """
        marker = params.get("marker")
        if not marker:
            self._json_error(400, "Missing required parameter: marker")
            return
        start = params.get("start", days_ago(365))
        end = params.get("end", today_str())
        conn = get_db()
        try:
            rows = conn.execute(
                """
                SELECT timestamp, marker_name AS marker, value, unit,
                       reference_low AS ref_low, reference_high AS ref_high,
                       optimal_low, optimal_high, panel_name, lab_source
                FROM biomarkers
                WHERE marker_name = ?
                AND date(timestamp) BETWEEN ? AND ?
                ORDER BY timestamp
                """,
                (marker, start, end),
            ).fetchall()
            self._send_json(rows_to_dicts(rows))
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="太医院 Dashboard Server")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"Port (default: {DEFAULT_PORT})")
    args = parser.parse_args()

    # Verify database exists (warn, don't crash — dashboard still serves)
    if not DATABASE.exists():
        print(f"[WARN] Database not found at {DATABASE}")
        print("       API endpoints will return 503 until the database is initialized.")
        print("       Run the taiyiyuan skill to create it: sqlite3 {DATABASE} < schema.sql")
    else:
        print(f"[OK]   Database: {DATABASE}")

    print(f"[OK]   Dashboard: {DASHBOARD_HTML}")

    server = HTTPServer(("127.0.0.1", args.port), TaiYiYuanHandler)
    print(f"\n  太医院 Longevity OS — http://localhost:{args.port}\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[STOP] Server shut down.")
        server.server_close()


if __name__ == "__main__":
    main()
