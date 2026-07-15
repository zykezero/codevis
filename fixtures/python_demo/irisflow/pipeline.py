"""Orchestration — the module that pulls every other module together."""
from __future__ import annotations

from .clean import clean_dataset
from .io_layer import load_raw, save_result
from .publish import publish
from .stats import rank_species, summarize
from .transform import add_ratios, aggregate_by_species, scale_numeric, trim_outliers
from .utils import log_step, make_recorder


def run_pipeline(in_path, out_path):
    """Load -> clean -> ratios -> trim loop -> scale -> aggregate -> summarise -> save."""
    recorder = make_recorder()

    raw = load_raw(in_path)
    cleaned = clean_dataset(raw)
    recorder.record("cleaned", cleaned)

    featured = add_ratios(cleaned)
    trimmed = trim_outliers(featured)
    recorder.record("trimmed", trimmed)

    scaled = scale_numeric(trimmed)
    aggregated = aggregate_by_species(scaled)
    recorder.record("aggregated", aggregated)

    ranked = rank_species(aggregated)
    report = summarize(ranked)
    log_step("run_pipeline:done", report)

    publish(scaled)          # <- writes processed_iris.csv, consumed by analysis/*
    save_result(ranked, out_path)
    return ranked, recorder.names(), trimmed.attrs.get("trim_history", [])
