"""Stage 1 output: writes the shared data product other scripts consume."""
from __future__ import annotations

from pathlib import Path

from .transform import tag_species
from .utils import log_step

PROCESSED = Path(__file__).resolve().parents[1] / "processed_iris.csv"


def publish(df, path=PROCESSED):
    """Write processed_iris.csv — the hand-off point between stages."""
    tagged = tag_species(df)
    tagged.to_csv(path, index=False)
    return log_step("publish", tagged)
