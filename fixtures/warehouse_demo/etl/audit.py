"""A third consumer. Coupled to extract.py only through the data, never by a call."""
from __future__ import annotations

import pandas as pd

CHECK = "SELECT id, species, petal_ratio, bad_petal_ratio FROM qa_flags WHERE bad_petal_ratio = 1"


def failing_rows(conn):
    """Reads qa_flags — the view SQL builds in 05_qa_checks.sql."""
    return pd.read_sql(CHECK, conn)


def summarize(conn):
    rows = failing_rows(conn)
    return {"failing": len(rows), "species": rows["species"].unique().tolist()}
