"""Stage 2b — a SECOND, independent consumer of processed_iris.csv.

report.py and qa.py never call each other. They are coupled ONLY through the
columns of the data product — exactly the connection the web view must surface.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

PROCESSED = Path(__file__).resolve().parents[1] / "processed_iris.csv"


def load_processed(path=PROCESSED):
    return pd.read_csv(path)


def check_ratios(df=None):
    """Also reads petal_ratio — the shared column that couples qa to report."""
    df = load_processed() if df is None else df
    bad = df[df["petal_ratio"] <= 0]
    return {"rows": len(df), "bad_petal_ratio": len(bad)}


def check_species(df=None):
    df = load_processed() if df is None else df
    return df["species"].value_counts().to_dict()
