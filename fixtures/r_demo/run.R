# Entry point: Rscript run.R  (from inside fixtures/r_demo/)

for (f in c("utils.R", "io_layer.R", "clean.R", "transform.R", "stats.R", "publish.R", "pipeline.R")) {
  source(file.path("R", f))
}

DATA <- file.path("..", "data", "iris_raw.csv")
OUT <- "iris_summary.csv"

out <- run_pipeline(DATA, OUT)

cat("\nrecorded steps:", paste(out$steps, collapse = ", "), "\n\n")
print(out$result)

cat("\ntrim loop history (the iterations a user would step through):\n")
print(do.call(rbind, lapply(out$trim_history, as.data.frame)))
