"""Shared helpers. Deliberately referenced from every other module.

This is the 'far removed, referenced multiple times over' case from the project
brief: a change here should light up call sites in clean/transform/stats/pipeline.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field


class MissingColumns(Exception):
    """Raised when a frame does not carry the columns a step requires."""


def require_columns(df, columns):
    """Assert that `df` carries every name in `columns`."""
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise MissingColumns(f"missing columns: {missing}")
    return df


def log_step(name, df):
    """Print a one-line trace of a pipeline step and pass the frame through."""
    print(f"[step] {name:<22} rows={len(df):>3} cols={len(df.columns)}")
    return df


@dataclass
class StepRecorder:
    """A toy stand-in for the future trace store (design notes D9)."""

    steps: list = field(default_factory=list)

    def record(self, name, df):
        self.steps.append({"name": name, "rows": len(df), "at": time.time()})
        return log_step(name, df)

    def names(self):
        return [s["name"] for s in self.steps]


def make_recorder():
    """Factory — return value's type must be *inferred*, not read off an annotation."""
    return StepRecorder()
