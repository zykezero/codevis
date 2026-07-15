# The data transformation steps.

suppressPackageStartupMessages({
  library(dplyr)
  library(purrr)
})

#' STEP 1 — derive shape features from the raw measurements.
add_ratios <- function(df) {
  df <- df %>%
    mutate(
      sepal_ratio = sepal_length / sepal_width,
      petal_ratio = petal_length / petal_width
    )
  log_step("add_ratios", df)
}

#' STEP 2 — iteratively trim outliers, one round at a time.
#'
#' THE LOOP FIXTURE. Nested: an outer convergence loop over rounds, an inner
#' loop over species. Every iteration carries state worth stepping through
#' (round, species, max z-score, rows dropped, rows remaining) and the loop
#' terminates on a data-dependent condition rather than a fixed count.
trim_outliers <- function(df, max_rounds = 5, z_thresh = 2.0) {
  history <- list()

  for (round_idx in seq_len(max_rounds)) {
    dropped_this_round <- 0

    for (sp in sort(unique(df$species))) {
      rows <- df %>% filter(species == sp)
      z <- abs(scale(rows$petal_ratio)[, 1])
      worst <- which.max(z)

      if (length(z) > 0 && !is.na(z[worst]) && z[worst] > z_thresh) {
        drop_id <- rows$row_id[worst]
        df <- df %>% filter(row_id != drop_id)
        dropped_this_round <- dropped_this_round + 1
      }

      history[[length(history) + 1]] <- list(
        round = round_idx,
        species = sp,
        max_z = if (length(z) > 0) max(z, na.rm = TRUE) else NA_real_,
        dropped = dropped_this_round,
        remaining = nrow(df)
      )
    }

    if (dropped_this_round == 0) break
  }

  attr(df, "trim_history") <- history
  log_step("trim_outliers", df)
}

#' STEP 3 — z-score the measurement columns.
scale_numeric <- function(df, columns = NULL) {
  if (is.null(columns)) columns <- NUMERIC
  require_columns(df, columns)
  df <- df %>% mutate(across(all_of(columns), ~ as.numeric(scale(.x))))
  log_step("scale_numeric", df)
}

#' STEP 4 — collapse to one row per species.
aggregate_by_species <- function(df) {
  grouped <- df %>%
    group_by(species) %>%
    summarise(across(where(is.numeric), mean), .groups = "drop") %>%
    select(-any_of("row_id"))
  log_step("aggregate_by_species", grouped)
}

#' Higher-order: functions passed as values — the hard case for static resolution.
apply_steps <- function(df, steps) {
  reduce(steps, function(acc, fn) fn(acc), .init = df)
}

default_steps <- function() {
  list(clean_dataset, add_ratios, trim_outliers, scale_numeric, aggregate_by_species)
}

#' A regex applied per-row — the fixture for the stretch-goal regex inspector.
#' Every call has a clean in/out pair worth capturing: the input string, the
#' pattern, whether it matched, and the groups it produced.
SPECIES_TAG <- "^([a-z]+)?_?(setosa|versicolor|virginica)$"

parse_species_tag <- function(value) {
  m <- regmatches(tolower(trimws(value)), regexec(SPECIES_TAG, tolower(trimws(value))))[[1]]
  if (length(m) == 0) {
    return(list(genus = NA_character_, name = value, matched = FALSE))
  }
  list(genus = if (nzchar(m[2])) m[2] else NA_character_, name = m[3], matched = TRUE)
}

#' Apply the regex across the frame.
tag_species <- function(df) {
  parsed <- lapply(df$species, parse_species_tag)
  df$species_matched <- vapply(parsed, function(p) p$matched, logical(1))
  log_step("tag_species", df)
}
