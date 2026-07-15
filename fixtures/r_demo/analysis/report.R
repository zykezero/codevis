# Stage 2a — a DOWNSTREAM consumer of processed_iris.csv.
# Never sources the pipeline. Its only connection to it is the data product.

suppressPackageStartupMessages({
  library(dplyr)
  library(readr)
})

PROCESSED <- "processed_iris.csv"

load_processed <- function(path = PROCESSED) {
  read_csv(path, show_col_types = FALSE)
}

#' Reads the petal_ratio and species columns.
petal_report <- function(df = NULL) {
  if (is.null(df)) df <- load_processed()
  df %>%
    arrange(desc(petal_ratio)) %>%
    select(species, petal_ratio, sepal_ratio)
}

widest_sepals <- function(df = NULL) {
  if (is.null(df)) df <- load_processed()
  df %>%
    slice_max(sepal_width, n = 5) %>%
    select(species, sepal_width)
}
