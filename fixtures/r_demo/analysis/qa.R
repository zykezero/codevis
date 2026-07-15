# Stage 2b — a SECOND, independent consumer of processed_iris.csv.
# report.R and qa.R never call each other. They are coupled ONLY through the
# columns of the data product — exactly the connection the web view must surface.

suppressPackageStartupMessages({
  library(dplyr)
  library(readr)
})

PROCESSED <- "processed_iris.csv"

load_processed <- function(path = PROCESSED) {
  read_csv(path, show_col_types = FALSE)
}

#' Also reads petal_ratio — the shared column that couples qa to report.
check_ratios <- function(df = NULL) {
  if (is.null(df)) df <- load_processed()
  bad <- df %>% filter(petal_ratio <= 0)
  list(rows = nrow(df), bad_petal_ratio = nrow(bad))
}

check_species <- function(df = NULL) {
  if (is.null(df)) df <- load_processed()
  df %>% count(species)
}
