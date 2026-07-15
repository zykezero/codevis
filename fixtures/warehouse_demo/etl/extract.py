"""Python that reads the warehouse — the cross-language handoff.

Nothing here imports the SQL. The ONLY connection is `processed_iris`, and it has
to become the same node as the table created in sql/03_features.sql.
"""
from __future__ import annotations

import pandas as pd

FEATURES_SQL = """
    SELECT species, petal_ratio, sepal_ratio, sepal_width
    FROM processed_iris
    WHERE petal_ratio > 0
"""


def load_features(conn):
    """Reads processed_iris — the table SQL builds."""
    return pd.read_sql(FEATURES_SQL, conn)


def species_report(conn):
    """A second consumer, this time of the species_summary VIEW."""
    return pd.read_sql(
        "SELECT species, mean_petal_ratio, n FROM species_summary ORDER BY n DESC",
        conn,
    )


def flag_outliers(conn):
    """Writes back into the warehouse — Python as a producer of a SQL table."""
    df = load_features(conn)
    bad = df[df["petal_ratio"] > 10]
    bad.to_sql("outlier_flags", conn, if_exists="replace", index=False)
    return bad
