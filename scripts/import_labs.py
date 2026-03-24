#!/usr/bin/env python3
"""
TaiYiYuan (太医院) — Lab report parser and importer.

Parses lab results from text or JSON and inserts into the biomarkers table.

Usage:
    python import_labs.py --file report.txt
    python import_labs.py --interactive          # Paste lab results, Ctrl+D to end
    python import_labs.py --json results.json    # Pre-structured JSON input
"""

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from paths import get_db_path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DB_PATH = str(get_db_path())

# ---------------------------------------------------------------------------
# Marker name aliases: common abbreviations -> canonical names
# ---------------------------------------------------------------------------

MARKER_ALIASES: dict[str, str] = {
    # CBC (Complete Blood Count)
    "wbc": "White Blood Cells",
    "white blood cells": "White Blood Cells",
    "white blood cell count": "White Blood Cells",
    "rbc": "Red Blood Cells",
    "red blood cells": "Red Blood Cells",
    "red blood cell count": "Red Blood Cells",
    "hgb": "Hemoglobin",
    "hb": "Hemoglobin",
    "hemoglobin": "Hemoglobin",
    "haemoglobin": "Hemoglobin",
    "hct": "Hematocrit",
    "hematocrit": "Hematocrit",
    "haematocrit": "Hematocrit",
    "plt": "Platelets",
    "platelet count": "Platelets",
    "platelets": "Platelets",
    "mcv": "Mean Corpuscular Volume",
    "mch": "Mean Corpuscular Hemoglobin",
    "mchc": "Mean Corpuscular Hemoglobin Concentration",
    "rdw": "Red Cell Distribution Width",
    "rdw-cv": "Red Cell Distribution Width",
    "mpv": "Mean Platelet Volume",
    "neutrophils": "Neutrophils",
    "neut": "Neutrophils",
    "neutrophil %": "Neutrophils %",
    "lymphocytes": "Lymphocytes",
    "lymph": "Lymphocytes",
    "lymphocyte %": "Lymphocytes %",
    "monocytes": "Monocytes",
    "mono": "Monocytes",
    "eosinophils": "Eosinophils",
    "eos": "Eosinophils",
    "basophils": "Basophils",
    "baso": "Basophils",

    # CMP (Comprehensive Metabolic Panel)
    "glucose": "Glucose",
    "fasting glucose": "Glucose",
    "blood glucose": "Glucose",
    "bun": "Blood Urea Nitrogen",
    "blood urea nitrogen": "Blood Urea Nitrogen",
    "creatinine": "Creatinine",
    "creat": "Creatinine",
    "egfr": "eGFR",
    "gfr": "eGFR",
    "sodium": "Sodium",
    "na": "Sodium",
    "na+": "Sodium",
    "potassium": "Potassium",
    "k": "Potassium",
    "k+": "Potassium",
    "chloride": "Chloride",
    "cl": "Chloride",
    "cl-": "Chloride",
    "co2": "CO2",
    "carbon dioxide": "CO2",
    "bicarbonate": "CO2",
    "hco3": "CO2",
    "calcium": "Calcium",
    "ca": "Calcium",
    "ca2+": "Calcium",
    "total protein": "Total Protein",
    "protein, total": "Total Protein",
    "albumin": "Albumin",
    "alb": "Albumin",
    "globulin": "Globulin",
    "a/g ratio": "Albumin/Globulin Ratio",
    "ag ratio": "Albumin/Globulin Ratio",
    "bilirubin": "Total Bilirubin",
    "bilirubin, total": "Total Bilirubin",
    "total bilirubin": "Total Bilirubin",
    "direct bilirubin": "Direct Bilirubin",
    "bilirubin, direct": "Direct Bilirubin",
    "alkaline phosphatase": "Alkaline Phosphatase",
    "alk phos": "Alkaline Phosphatase",
    "alp": "Alkaline Phosphatase",
    "ast": "AST",
    "sgot": "AST",
    "aspartate aminotransferase": "AST",
    "alt": "ALT",
    "sgpt": "ALT",
    "alanine aminotransferase": "ALT",
    "ggt": "GGT",
    "gamma-gt": "GGT",
    "gamma glutamyl transferase": "GGT",

    # Lipid Panel
    "total cholesterol": "Total Cholesterol",
    "cholesterol": "Total Cholesterol",
    "cholesterol, total": "Total Cholesterol",
    "ldl": "LDL Cholesterol",
    "ldl-c": "LDL Cholesterol",
    "ldl cholesterol": "LDL Cholesterol",
    "ldl cholesterol, calc": "LDL Cholesterol",
    "hdl": "HDL Cholesterol",
    "hdl-c": "HDL Cholesterol",
    "hdl cholesterol": "HDL Cholesterol",
    "triglycerides": "Triglycerides",
    "trig": "Triglycerides",
    "tg": "Triglycerides",
    "vldl": "VLDL Cholesterol",
    "vldl cholesterol": "VLDL Cholesterol",
    "non-hdl cholesterol": "Non-HDL Cholesterol",
    "tc/hdl ratio": "Total Cholesterol/HDL Ratio",
    "chol/hdl ratio": "Total Cholesterol/HDL Ratio",
    "apob": "Apolipoprotein B",
    "apo b": "Apolipoprotein B",
    "apolipoprotein b": "Apolipoprotein B",
    "apoa1": "Apolipoprotein A1",
    "apo a1": "Apolipoprotein A1",
    "lp(a)": "Lipoprotein(a)",
    "lipoprotein a": "Lipoprotein(a)",
    "lipoprotein(a)": "Lipoprotein(a)",

    # Thyroid
    "tsh": "Thyroid Stimulating Hormone",
    "thyroid stimulating hormone": "Thyroid Stimulating Hormone",
    "free t4": "Free T4",
    "ft4": "Free T4",
    "free thyroxine": "Free T4",
    "free t3": "Free T3",
    "ft3": "Free T3",
    "free triiodothyronine": "Free T3",
    "t4": "Total T4",
    "t4, total": "Total T4",
    "thyroxine": "Total T4",
    "t3": "Total T3",
    "t3, total": "Total T3",
    "triiodothyronine": "Total T3",
    "thyroid peroxidase ab": "Thyroid Peroxidase Antibodies",
    "tpo ab": "Thyroid Peroxidase Antibodies",
    "anti-tpo": "Thyroid Peroxidase Antibodies",

    # Hormones
    "testosterone": "Testosterone",
    "testosterone, total": "Testosterone",
    "total testosterone": "Testosterone",
    "free testosterone": "Free Testosterone",
    "testosterone, free": "Free Testosterone",
    "estradiol": "Estradiol",
    "e2": "Estradiol",
    "dhea-s": "DHEA-S",
    "dhea sulfate": "DHEA-S",
    "cortisol": "Cortisol",
    "cortisol, am": "Cortisol",
    "igf-1": "IGF-1",
    "igf1": "IGF-1",
    "insulin-like growth factor": "IGF-1",
    "shbg": "SHBG",
    "sex hormone binding globulin": "SHBG",
    "fsh": "FSH",
    "follicle stimulating hormone": "FSH",
    "lh": "LH",
    "luteinizing hormone": "LH",
    "prolactin": "Prolactin",
    "growth hormone": "Growth Hormone",
    "gh": "Growth Hormone",

    # Inflammatory
    "crp": "C-Reactive Protein",
    "c-reactive protein": "C-Reactive Protein",
    "hs-crp": "hs-CRP",
    "high sensitivity crp": "hs-CRP",
    "high-sensitivity c-reactive protein": "hs-CRP",
    "esr": "Erythrocyte Sedimentation Rate",
    "sed rate": "Erythrocyte Sedimentation Rate",
    "erythrocyte sedimentation rate": "Erythrocyte Sedimentation Rate",
    "ferritin": "Ferritin",
    "homocysteine": "Homocysteine",
    "hcy": "Homocysteine",
    "fibrinogen": "Fibrinogen",
    "il-6": "Interleukin-6",
    "interleukin 6": "Interleukin-6",
    "tnf-alpha": "TNF-alpha",
    "tnf alpha": "TNF-alpha",

    # Diabetes/Metabolic
    "hba1c": "HbA1c",
    "a1c": "HbA1c",
    "hemoglobin a1c": "HbA1c",
    "glycated hemoglobin": "HbA1c",
    "fasting insulin": "Fasting Insulin",
    "insulin": "Fasting Insulin",
    "insulin, fasting": "Fasting Insulin",
    "homa-ir": "HOMA-IR",
    "homa ir": "HOMA-IR",
    "c-peptide": "C-Peptide",

    # Vitamins & Minerals
    "vitamin d": "Vitamin D",
    "25-oh vitamin d": "Vitamin D",
    "25-hydroxyvitamin d": "Vitamin D",
    "vitamin d, 25-hydroxy": "Vitamin D",
    "vitamin b12": "Vitamin B12",
    "b12": "Vitamin B12",
    "cobalamin": "Vitamin B12",
    "folate": "Folate",
    "folic acid": "Folate",
    "vitamin b9": "Folate",
    "iron": "Iron",
    "iron, serum": "Iron",
    "tibc": "TIBC",
    "total iron binding capacity": "TIBC",
    "transferrin saturation": "Transferrin Saturation",
    "tsat": "Transferrin Saturation",
    "magnesium": "Magnesium",
    "mg": "Magnesium",
    "zinc": "Zinc",
    "zn": "Zinc",
    "selenium": "Selenium",
    "copper": "Copper",

    # Kidney
    "uric acid": "Uric Acid",
    "cystatin c": "Cystatin C",
    "microalbumin": "Microalbumin",
    "urine albumin": "Microalbumin",
    "acr": "Albumin/Creatinine Ratio",
    "albumin/creatinine ratio": "Albumin/Creatinine Ratio",

    # Coagulation
    "pt": "Prothrombin Time",
    "prothrombin time": "Prothrombin Time",
    "inr": "INR",
    "ptt": "Partial Thromboplastin Time",
    "aptt": "Partial Thromboplastin Time",
    "d-dimer": "D-Dimer",

    # Longevity-specific
    "grip strength": "Grip Strength",
    "telomere length": "Telomere Length",
    "dnam age": "DNAm Age",
    "pace of aging": "Pace of Aging",
    "dunedinpace": "DunedinPACE",
    "grimage": "GrimAge",
    "phenoage": "PhenoAge",
}

# ---------------------------------------------------------------------------
# Reference ranges (standard adult ranges)
# Format: canonical_name -> (low, high, unit)
# ---------------------------------------------------------------------------

REFERENCE_RANGES: dict[str, tuple[float | None, float | None, str]] = {
    # CBC
    "White Blood Cells": (3.4, 10.8, "10^3/uL"),
    "Red Blood Cells": (4.14, 5.80, "10^6/uL"),
    "Hemoglobin": (12.6, 17.7, "g/dL"),
    "Hematocrit": (37.5, 51.0, "%"),
    "Platelets": (150, 379, "10^3/uL"),
    "Mean Corpuscular Volume": (79, 97, "fL"),
    "Mean Corpuscular Hemoglobin": (26.6, 33.0, "pg"),
    "Mean Corpuscular Hemoglobin Concentration": (31.5, 35.7, "g/dL"),
    "Red Cell Distribution Width": (11.6, 15.4, "%"),

    # CMP
    "Glucose": (65, 99, "mg/dL"),
    "Blood Urea Nitrogen": (6, 24, "mg/dL"),
    "Creatinine": (0.76, 1.27, "mg/dL"),
    "eGFR": (90, None, "mL/min/1.73m2"),
    "Sodium": (134, 144, "mmol/L"),
    "Potassium": (3.5, 5.2, "mmol/L"),
    "Chloride": (96, 106, "mmol/L"),
    "CO2": (18, 29, "mmol/L"),
    "Calcium": (8.7, 10.2, "mg/dL"),
    "Total Protein": (6.0, 8.5, "g/dL"),
    "Albumin": (3.5, 5.5, "g/dL"),
    "Total Bilirubin": (0.0, 1.2, "mg/dL"),
    "Alkaline Phosphatase": (44, 121, "IU/L"),
    "AST": (0, 40, "IU/L"),
    "ALT": (0, 44, "IU/L"),
    "GGT": (0, 65, "IU/L"),

    # Lipid Panel
    "Total Cholesterol": (100, 199, "mg/dL"),
    "LDL Cholesterol": (0, 99, "mg/dL"),
    "HDL Cholesterol": (39, None, "mg/dL"),
    "Triglycerides": (0, 149, "mg/dL"),
    "Apolipoprotein B": (0, 90, "mg/dL"),
    "Lipoprotein(a)": (0, 30, "nmol/L"),

    # Thyroid
    "Thyroid Stimulating Hormone": (0.45, 4.5, "uIU/mL"),
    "Free T4": (0.82, 1.77, "ng/dL"),
    "Free T3": (2.0, 4.4, "pg/mL"),

    # Inflammatory
    "C-Reactive Protein": (0.0, 3.0, "mg/L"),
    "hs-CRP": (0.0, 1.0, "mg/L"),
    "Erythrocyte Sedimentation Rate": (0, 22, "mm/hr"),
    "Ferritin": (30, 400, "ng/mL"),
    "Homocysteine": (0, 10.4, "umol/L"),

    # Diabetes/Metabolic
    "HbA1c": (4.0, 5.6, "%"),
    "Fasting Insulin": (2.6, 24.9, "uIU/mL"),
    "HOMA-IR": (0, 1.0, ""),

    # Hormones
    "Testosterone": (264, 916, "ng/dL"),
    "DHEA-S": (138, 475, "ug/dL"),
    "Cortisol": (6.2, 19.4, "ug/dL"),
    "IGF-1": (101, 307, "ng/mL"),
    "Estradiol": (7.6, 42.6, "pg/mL"),

    # Vitamins
    "Vitamin D": (30, 100, "ng/mL"),
    "Vitamin B12": (232, 1245, "pg/mL"),
    "Folate": (2.7, 17.0, "ng/mL"),
    "Iron": (38, 169, "ug/dL"),

    # Kidney
    "Uric Acid": (3.7, 8.6, "mg/dL"),
    "Cystatin C": (0.53, 0.95, "mg/L"),
}

# ---------------------------------------------------------------------------
# Panel detection heuristics
# ---------------------------------------------------------------------------

PANEL_MARKERS: dict[str, list[str]] = {
    "CBC": [
        "White Blood Cells", "Red Blood Cells", "Hemoglobin", "Hematocrit",
        "Platelets", "Mean Corpuscular Volume",
    ],
    "CMP": [
        "Glucose", "Blood Urea Nitrogen", "Creatinine", "Sodium",
        "Potassium", "Chloride", "CO2", "Calcium", "Albumin",
        "AST", "ALT", "Total Bilirubin", "Alkaline Phosphatase",
    ],
    "Lipid Panel": [
        "Total Cholesterol", "LDL Cholesterol", "HDL Cholesterol", "Triglycerides",
    ],
    "Thyroid Panel": [
        "Thyroid Stimulating Hormone", "Free T4", "Free T3",
    ],
    "Metabolic": [
        "HbA1c", "Fasting Insulin", "HOMA-IR",
    ],
    "Iron Panel": [
        "Iron", "Ferritin", "TIBC", "Transferrin Saturation",
    ],
}


def _detect_panel(marker_name: str) -> str | None:
    """Guess which panel a marker belongs to."""
    for panel, markers in PANEL_MARKERS.items():
        if marker_name in markers:
            return panel
    return None


# ---------------------------------------------------------------------------
# Text parsing
# ---------------------------------------------------------------------------

# Regex patterns for common lab report formats
# Pattern 1: "Marker Name    value    unit    ref: low-high"
# Pattern 2: "Marker Name: value unit (ref: low - high)"
# Pattern 3: "Marker Name  value  unit  low  high"
# Pattern 4: "Marker Name  value unit"

_PATTERNS = [
    # "Marker: 5.2 mg/dL (ref: 3.5 - 5.5)" or "Marker: 5.2 mg/dL (3.5-5.5)"
    re.compile(
        r"^(?P<name>.+?)\s*[:=]\s*(?P<value>[\d.]+)\s*(?P<unit>[a-zA-Z%/^0-9.*]+(?:/[a-zA-Z0-9.*^]+)?)\s*"
        r"(?:\(?\s*(?:ref(?:erence)?[:=\s]*)?(?P<low>[\d.]+)\s*[-–]\s*(?P<high>[\d.]+)\s*\)?)?",
        re.IGNORECASE,
    ),
    # "Marker Name    5.2    mg/dL    3.5    5.5" (tab/space separated columns)
    re.compile(
        r"^(?P<name>[A-Za-z][\w\s,/()-]+?)\s{2,}(?P<value>[\d.]+)\s+(?P<unit>[a-zA-Z%/^0-9.*]+(?:/[a-zA-Z0-9.*^]+)?)"
        r"(?:\s+(?P<low>[\d.]+)\s+[-–]?\s*(?P<high>[\d.]+))?",
        re.IGNORECASE,
    ),
    # "Marker Name    5.2 mg/dL" (minimal)
    re.compile(
        r"^(?P<name>[A-Za-z][\w\s,/()-]+?)\s{2,}(?P<value>[\d.]+)\s*(?P<unit>[a-zA-Z%/^0-9.*]+(?:/[a-zA-Z0-9.*^]+)?)",
        re.IGNORECASE,
    ),
]


def _normalize_marker_name(raw: str) -> str:
    """Map raw marker name to canonical name via aliases."""
    cleaned = raw.strip().rstrip(":")
    key = cleaned.lower().strip()
    if key in MARKER_ALIASES:
        return MARKER_ALIASES[key]
    # Return cleaned original if no alias found
    return cleaned


def _parse_line(line: str) -> dict | None:
    """
    Try to parse a single line of lab results.
    Returns dict with keys: marker, value, unit, ref_low, ref_high
    or None if unparsable.
    """
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("---"):
        return None

    for pattern in _PATTERNS:
        m = pattern.match(line)
        if m:
            name_raw = m.group("name").strip()
            # Skip lines where "name" is just numbers or too short
            if not any(c.isalpha() for c in name_raw):
                continue
            if len(name_raw) < 2:
                continue

            try:
                value = float(m.group("value"))
            except (ValueError, TypeError):
                continue

            unit = m.group("unit") if m.group("unit") else ""

            ref_low = None
            ref_high = None
            try:
                if m.group("low"):
                    ref_low = float(m.group("low"))
                if m.group("high"):
                    ref_high = float(m.group("high"))
            except (ValueError, TypeError, IndexError):
                pass

            canonical = _normalize_marker_name(name_raw)

            # Fill in reference ranges from our dictionary if not in the text
            if ref_low is None and ref_high is None and canonical in REFERENCE_RANGES:
                ref_low, ref_high, default_unit = REFERENCE_RANGES[canonical]
                if not unit:
                    unit = default_unit

            return {
                "marker": canonical,
                "value": value,
                "unit": unit,
                "ref_low": ref_low,
                "ref_high": ref_high,
            }

    return None


def parse_text(text: str) -> tuple[list[dict], list[str]]:
    """
    Parse multi-line lab report text.
    Returns (parsed_results, unparsed_lines).
    """
    parsed = []
    unparsed = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        result = _parse_line(stripped)
        if result:
            parsed.append(result)
        else:
            # Only track as unparsed if it looks like it might be data
            # (skip obvious headers, separators, blank lines)
            if (
                stripped
                and not stripped.startswith("=")
                and not stripped.startswith("-" * 3)
                and not stripped.upper() == stripped  # Skip all-caps headers
                and len(stripped) > 3
            ):
                unparsed.append(stripped)

    return parsed, unparsed


def parse_json_input(data: list[dict]) -> list[dict]:
    """
    Normalize JSON input into our standard format.
    Expected: [{panel, marker, value, unit, ref_low, ref_high}, ...]
    """
    results = []
    for item in data:
        marker_raw = item.get("marker", "")
        canonical = _normalize_marker_name(marker_raw)

        try:
            value = float(item["value"])
        except (ValueError, TypeError, KeyError):
            continue

        unit = item.get("unit", "")
        ref_low = item.get("ref_low")
        ref_high = item.get("ref_high")

        if ref_low is not None:
            try:
                ref_low = float(ref_low)
            except (ValueError, TypeError):
                ref_low = None
        if ref_high is not None:
            try:
                ref_high = float(ref_high)
            except (ValueError, TypeError):
                ref_high = None

        # Fill from known ranges if missing
        if ref_low is None and ref_high is None and canonical in REFERENCE_RANGES:
            ref_low, ref_high, default_unit = REFERENCE_RANGES[canonical]
            if not unit:
                unit = default_unit

        results.append({
            "marker": canonical,
            "value": value,
            "unit": unit,
            "ref_low": ref_low,
            "ref_high": ref_high,
            "panel": item.get("panel", _detect_panel(canonical)),
        })

    return results


# ---------------------------------------------------------------------------
# Database insertion
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_results(
    results: list[dict],
    timestamp: str | None = None,
    lab_source: str | None = None,
) -> int:
    """Insert parsed lab results into the biomarkers table. Returns count inserted."""
    if not results:
        return 0

    if timestamp is None:
        timestamp = _now_iso()

    if not os.path.exists(DB_PATH):
        print(f"ERROR: Database not found: {DB_PATH}")
        print("Run 'python setup.py' first.")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    now = _now_iso()
    inserted = 0

    try:
        for r in results:
            panel = r.get("panel") or _detect_panel(r["marker"])
            conn.execute(
                """INSERT INTO biomarkers
                   (timestamp, panel_name, marker_name, value, unit,
                    reference_low, reference_high, lab_source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    timestamp,
                    panel,
                    r["marker"],
                    r["value"],
                    r["unit"],
                    r["ref_low"],
                    r["ref_high"],
                    lab_source,
                    now,
                ),
            )
            inserted += 1
        conn.commit()
    except sqlite3.Error as e:
        print(f"ERROR: Database insert failed: {e}")
        conn.rollback()
    finally:
        conn.close()

    return inserted


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_file(path: str, timestamp: str | None, lab_source: str | None):
    """Parse lab results from a text file."""
    p = Path(path)
    if not p.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)

    text = p.read_text(encoding="utf-8")
    parsed, unparsed = parse_text(text)

    print(f"TaiYiYuan lab import: {p.name}")
    print("=" * 60)
    print(f"Lines read:    {len(text.splitlines())}")
    print(f"Markers parsed: {len(parsed)}")
    print(f"Unparsed lines: {len(unparsed)}")

    if parsed:
        print("\nParsed markers:")
        for r in parsed:
            flag = ""
            if r["ref_low"] is not None and r["value"] < r["ref_low"]:
                flag = " [LOW]"
            elif r["ref_high"] is not None and r["value"] > r["ref_high"]:
                flag = " [HIGH]"
            ref = ""
            if r["ref_low"] is not None or r["ref_high"] is not None:
                ref = f"  (ref: {r['ref_low'] or '?'} - {r['ref_high'] or '?'})"
            print(f"  {r['marker']:40s} {r['value']:>8.2f} {r['unit']:<15s}{ref}{flag}")

        inserted = insert_results(parsed, timestamp=timestamp, lab_source=lab_source)
        print(f"\nInserted {inserted} biomarker(s) into database.")
    else:
        print("\nNo markers could be parsed from this file.")

    if unparsed:
        print(f"\nUnparsed lines ({len(unparsed)}):")
        for line in unparsed[:20]:
            print(f"  ? {line}")
        if len(unparsed) > 20:
            print(f"  ... and {len(unparsed) - 20} more")


def cmd_interactive(timestamp: str | None, lab_source: str | None):
    """Read lab results from stdin (interactive paste)."""
    print("TaiYiYuan lab import (interactive mode)")
    print("Paste lab results below, then press Ctrl+D (EOF) to process:")
    print("-" * 60)

    try:
        text = sys.stdin.read()
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)

    if not text.strip():
        print("No input received.")
        sys.exit(0)

    parsed, unparsed = parse_text(text)

    print("\n" + "=" * 60)
    print(f"Markers parsed: {len(parsed)}")

    if parsed:
        print("\nParsed markers:")
        for r in parsed:
            flag = ""
            if r["ref_low"] is not None and r["value"] < r["ref_low"]:
                flag = " [LOW]"
            elif r["ref_high"] is not None and r["value"] > r["ref_high"]:
                flag = " [HIGH]"
            print(f"  {r['marker']:40s} {r['value']:>8.2f} {r['unit']}")

        inserted = insert_results(parsed, timestamp=timestamp, lab_source=lab_source)
        print(f"\nInserted {inserted} biomarker(s) into database.")

    if unparsed:
        print(f"\nUnparsed lines ({len(unparsed)}):")
        for line in unparsed[:10]:
            print(f"  ? {line}")


def cmd_json(path: str, timestamp: str | None, lab_source: str | None):
    """Import pre-structured JSON lab results."""
    p = Path(path)
    if not p.exists():
        print(f"ERROR: File not found: {path}")
        sys.exit(1)

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"ERROR: Invalid JSON: {e}")
        sys.exit(1)

    if not isinstance(data, list):
        print("ERROR: JSON input must be an array of objects.")
        print('Expected: [{"marker": "...", "value": ..., "unit": "..."}, ...]')
        sys.exit(1)

    results = parse_json_input(data)

    print(f"TaiYiYuan lab import (JSON): {p.name}")
    print("=" * 60)
    print(f"Records in file: {len(data)}")
    print(f"Markers parsed:  {len(results)}")

    if results:
        print("\nMarkers:")
        for r in results:
            panel_str = f"[{r.get('panel', '?')}] " if r.get("panel") else ""
            print(f"  {panel_str}{r['marker']:35s} {r['value']:>8.2f} {r['unit']}")

        inserted = insert_results(results, timestamp=timestamp, lab_source=lab_source)
        print(f"\nInserted {inserted} biomarker(s) into database.")
    else:
        print("\nNo valid markers found in JSON input.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="TaiYiYuan lab report parser and importer"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--file", metavar="PATH",
        help="Path to lab report text file"
    )
    group.add_argument(
        "--interactive", action="store_true",
        help="Paste lab results interactively (Ctrl+D to end)"
    )
    group.add_argument(
        "--json", metavar="PATH",
        help="Path to pre-structured JSON file"
    )
    parser.add_argument(
        "--date", metavar="YYYY-MM-DD",
        help="Date of the lab test (default: today)"
    )
    parser.add_argument(
        "--lab-source", metavar="NAME",
        help="Lab source (e.g., 'Quest Diagnostics', 'Inside Tracker')"
    )
    args = parser.parse_args()

    # Parse date into ISO timestamp
    timestamp = None
    if args.date:
        try:
            dt = datetime.strptime(args.date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            timestamp = dt.isoformat()
        except ValueError:
            print(f"ERROR: Invalid date format: {args.date}. Use YYYY-MM-DD.")
            sys.exit(1)

    if args.file:
        cmd_file(args.file, timestamp, args.lab_source)
    elif args.interactive:
        cmd_interactive(timestamp, args.lab_source)
    elif args.json:
        cmd_json(args.json, timestamp, args.lab_source)


if __name__ == "__main__":
    main()
