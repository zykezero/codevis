# Normalisation: get the raw frame into a shape the transforms can trust.

suppressPackageStartupMessages({
  library(dplyr)
  library(janitor)
  library(tidyr)
})

#' Lowercase, strip, and snake_case the column headers.
standardize_names <- function(df) {
  df <- df %>% clean_names()
  log_step("standardize_names", df)
}

#' Drop rows with blanks in any measurement column.
drop_missing <- function(df) {
  df <- df %>% drop_na(all_of(NUMERIC))
  log_step("drop_missing", df)
}

#' Compose the two cleaning moves. Calls two functions in this same file.
clean_dataset <- function(df) {
  df %>%
    standardize_names() %>%
    require_columns(c(NUMERIC, "species")) %>%
    drop_missing()
}
