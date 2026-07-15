"""Entry point: python run.py"""
from pathlib import Path

import pandas as pd

from irisflow.pipeline import run_pipeline

DATA = Path(__file__).resolve().parents[1] / "data" / "iris_raw.csv"
OUT = Path(__file__).resolve().parent / "iris_summary.csv"

if __name__ == "__main__":
    result, steps, trim_history = run_pipeline(DATA, OUT)

    print("\nrecorded steps:", steps)
    print(result.to_string(index=False))

    print("\ntrim loop history (the iterations a user would step through):")
    print(pd.DataFrame(trim_history).to_string(index=False))
