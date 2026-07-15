"""Normalisation: get the raw frame into a shape the transforms can trust."""
from __future__ import annotations

from .utils import log_step, require_columns

NUMERIC = ["sepal_length", "sepal_width", "petal_length", "petal_width"]


def standardize_names(df):
    """Lowercase, strip, and snake_case the column headers."""
    df = df.copy()
    df.columns = [c.strip().lower().replace(".", "_").replace(" ", "_") for c in df.columns]
    return log_step("standardize_names", df)


def drop_missing(df):
    """Drop rows with blanks in any measurement column."""
    df = df.dropna(subset=NUMERIC).reset_index(drop=True)
    return log_step("drop_missing", df)


def clean_dataset(df):
    """Compose the two cleaning moves. Calls two functions in this same module."""
    df = standardize_names(df)
    df = require_columns(df, NUMERIC + ["species"])
    return drop_missing(df)
