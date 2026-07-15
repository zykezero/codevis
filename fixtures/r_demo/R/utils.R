# Shared helpers. Deliberately referenced from every other file.
# Mirror of irisflow/utils.py.

suppressPackageStartupMessages({
  library(dplyr)
  library(rlang)
})

NUMERIC <- c("sepal_length", "sepal_width", "petal_length", "petal_width")

#' Assert that `df` carries every name in `columns`.
require_columns <- function(df, columns) {
  missing <- setdiff(columns, names(df))
  if (length(missing) > 0) {
    abort(sprintf("missing columns: %s", paste(missing, collapse = ", ")))
  }
  df
}

#' Print a one-line trace of a pipeline step and pass the frame through.
log_step <- function(name, df) {
  cat(sprintf("[step] %-24s rows=%3d cols=%d\n", name, nrow(df), ncol(df)))
  df
}

#' Closure-based recorder: R's answer to the StepRecorder dataclass.
#' A toy stand-in for the future trace store (design notes D9).
make_recorder <- function() {
  steps <- list()
  list(
    record = function(name, df) {
      steps[[length(steps) + 1]] <<- list(name = name, rows = nrow(df))
      log_step(name, df)
    },
    step_names = function() vapply(steps, function(s) s$name, character(1))
  )
}
