"""The data transformation steps."""
from __future__ import annotations

import re

from . import utils as u  # aliased module import — a resolution edge case
from .clean import NUMERIC, clean_dataset
from .utils import log_step, require_columns

SPECIES_TAG = re.compile(r"^(?P<genus>[a-z]+)?_?(?P<name>setosa|versicolor|virginica)$")


def add_ratios(df):
    """STEP 1 — derive shape features from the raw measurements."""
    df = df.copy()
    df["sepal_ratio"] = df["sepal_length"] / df["sepal_width"]
    df["petal_ratio"] = df["petal_length"] / df["petal_width"]
    return log_step("add_ratios", df)


def trim_outliers(df, max_rounds=5, z_thresh=2.0):
    """STEP 2 — iteratively trim outliers, one round at a time.

    THE LOOP FIXTURE. Nested: an outer convergence loop over rounds, an inner
    loop over species. Every iteration carries state worth stepping through
    (round, species, max z-score, rows dropped, rows remaining) and the loop
    terminates on a data-dependent condition rather than a fixed count.
    """
    history = []

    for round_idx in range(1, max_rounds + 1):
        dropped_this_round = 0

        for sp in sorted(df["species"].unique()):
            rows = df[df["species"] == sp]
            z = ((rows["petal_ratio"] - rows["petal_ratio"].mean()) / rows["petal_ratio"].std()).abs()

            if len(z) and z.max() > z_thresh:
                drop_id = z.idxmax()
                df = df.drop(index=drop_id)
                dropped_this_round += 1

            history.append(
                {
                    "round": round_idx,
                    "species": sp,
                    "max_z": float(z.max()) if len(z) else None,
                    "dropped": dropped_this_round,
                    "remaining": len(df),
                }
            )

        if dropped_this_round == 0:
            break

    df = df.reset_index(drop=True)
    df.attrs["trim_history"] = history
    return log_step("trim_outliers", df)


def scale_numeric(df, columns=None):
    """STEP 3 — z-score the measurement columns."""
    columns = columns or NUMERIC
    require_columns(df, columns)
    df = df.copy()
    for col in columns:
        df[col] = (df[col] - df[col].mean()) / df[col].std()
    return u.log_step("scale_numeric", df)  # called through the module alias


def aggregate_by_species(df):
    """STEP 4 — collapse to one row per species."""
    grouped = df.groupby("species").mean(numeric_only=True).reset_index()
    return log_step("aggregate_by_species", grouped)


def parse_species_tag(value):
    """A regex applied per-row — the fixture for the stretch-goal regex inspector.

    Every call has a clean in/out pair worth capturing: the input string, the
    pattern, whether it matched, and the named groups it produced.
    """
    match = SPECIES_TAG.match(value.strip().lower())
    if not match:
        return {"genus": None, "name": value, "matched": False}
    return {**match.groupdict(), "matched": True}


def tag_species(df):
    """Apply the regex across the frame."""
    df = df.copy()
    parsed = df["species"].map(parse_species_tag)
    df["species_matched"] = [p["matched"] for p in parsed]
    return log_step("tag_species", df)


def apply_steps(df, steps):
    """Higher-order: functions passed as values — the hard case for static resolution."""
    for fn in steps:
        df = fn(df)
    return df


def default_steps():
    return [clean_dataset, add_ratios, trim_outliers, scale_numeric, aggregate_by_species]
