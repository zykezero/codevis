"""Stage 2a — a DOWNSTREAM consumer of processed_iris.csv.

Never imports the pipeline. Its only connection to it is the data product.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

PROCESSED = Path(__file__).resolve().parents[1] / "processed_iris.csv"


def load_processed(path=PROCESSED):
    return pd.read_csv(path)


def petal_report(df=None):
    """Reads the petal_ratio and species columns."""
    df = load_processed() if df is None else df
    ranked = df.sort_values("petal_ratio", ascending=False)
    return ranked[["species", "petal_ratio", "sepal_ratio"]]


def widest_sepals(df=None):
    df = load_processed() if df is None else df
    return df.nlargest(5, "sepal_width")[["species", "sepal_width"]]
