"""Summaries computed on top of the transformed frame."""
from __future__ import annotations

from .transform import aggregate_by_species
from .utils import log_step, make_recorder, require_columns


def summarize(df):
    """Descriptive stats for the numeric columns."""
    require_columns(df, ["species"])
    out = df.describe().transpose().reset_index()
    return log_step("summarize", out)


def rank_species(df, by="petal_ratio"):
    """Order species by a derived feature."""
    ranked = df.sort_values(by, ascending=False).reset_index(drop=True)
    return log_step("rank_species", ranked)


def profile(df):
    """Uses an inferred-type object: `rec` has no annotation anywhere."""
    rec = make_recorder()
    rec.record("profile:input", df)
    agg = aggregate_by_species(df)
    rec.record("profile:aggregated", agg)
    return agg, rec.names()
