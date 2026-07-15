"""Reading and writing the edges of the pipeline."""
from __future__ import annotations

import pandas as pd

from .utils import log_step, require_columns

RAW_COLUMNS = [" Sepal.Length", "Sepal Width ", "Petal.Length", "PETAL WIDTH", "Species"]


def load_raw(path):
    """Load the messy source CSV."""
    df = pd.read_csv(path)
    require_columns(df, RAW_COLUMNS)
    return log_step("load_raw", df)


def save_result(df, path):
    """Persist the final frame."""
    df.to_csv(path, index=False)
    return log_step("save_result", df)
