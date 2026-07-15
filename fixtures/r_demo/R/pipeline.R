# Orchestration — the file that pulls every other file together.

suppressPackageStartupMessages({
  library(dplyr)
})

#' Load -> clean -> ratios -> trim loop -> scale -> aggregate -> summarise -> save.
run_pipeline <- function(in_path, out_path) {
  recorder <- make_recorder()

  raw <- load_raw(in_path)
  cleaned <- clean_dataset(raw) %>% mutate(row_id = row_number())
  recorder$record("cleaned", cleaned)

  featured <- add_ratios(cleaned)
  trimmed <- trim_outliers(featured)
  recorder$record("trimmed", trimmed)

  scaled <- scale_numeric(trimmed)
  aggregated <- aggregate_by_species(scaled)
  recorder$record("aggregated", aggregated)

  ranked <- rank_species(aggregated)
  report <- summarize_frame(ranked)
  log_step("run_pipeline:done", report)

  publish(scaled)          # <- writes processed_iris.csv, consumed by analysis/*
  save_result(ranked, out_path)
  list(
    result = ranked,
    steps = recorder$step_names(),
    trim_history = attr(trimmed, "trim_history")
  )
}
