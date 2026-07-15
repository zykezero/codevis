# Reading and writing the edges of the pipeline.

suppressPackageStartupMessages({
  library(readr)
})

RAW_COLUMNS <- c("Sepal.Length", "Sepal Width", "Petal.Length", "PETAL WIDTH", "Species")

#' Load the messy source CSV.
load_raw <- function(path) {
  df <- read_csv(path, show_col_types = FALSE, name_repair = "minimal")
  names(df) <- trimws(names(df))
  require_columns(df, RAW_COLUMNS)
  log_step("load_raw", df)
}

#' Persist the final frame.
save_result <- function(df, path) {
  write_csv(df, path)
  log_step("save_result", df)
}
