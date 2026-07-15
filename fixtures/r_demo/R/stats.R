# Summaries computed on top of the transformed frame.

suppressPackageStartupMessages({
  library(dplyr)
  library(tidyr)
})

#' Descriptive stats for the numeric columns.
summarize_frame <- function(df) {
  require_columns(df, "species")
  out <- df %>%
    select(where(is.numeric)) %>%
    pivot_longer(everything(), names_to = "column", values_to = "value") %>%
    group_by(column) %>%
    summarise(mean = mean(value), sd = sd(value), .groups = "drop")
  log_step("summarize_frame", out)
}

#' Order species by a derived feature.
rank_species <- function(df, by = "petal_ratio") {
  ranked <- df %>% arrange(desc(.data[[by]]))
  log_step("rank_species", ranked)
}

#' Uses the closure recorder — the object has no declared type anywhere.
profile <- function(df) {
  rec <- make_recorder()
  rec$record("profile:input", df)
  agg <- aggregate_by_species(df)
  rec$record("profile:aggregated", agg)
  list(agg = agg, steps = rec$step_names())
}
