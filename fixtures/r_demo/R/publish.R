# Stage 1 output: writes the shared data product other scripts consume.

suppressPackageStartupMessages({
  library(readr)
})

PROCESSED <- "processed_iris.csv"

#' Write processed_iris.csv — the hand-off point between stages.
publish <- function(df, path = PROCESSED) {
  tagged <- tag_species(df)
  write_csv(tagged, path)
  log_step("publish", tagged)
}
