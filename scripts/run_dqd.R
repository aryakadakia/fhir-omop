#!/usr/bin/env Rscript
#
# Run the OHDSI Data Quality Dashboard against the CDM this ETL produces.
#
#   Rscript scripts/run_dqd.R [path/to/omop.duckdb]
#
# DQD is the standardised version of src/fhir_omop/dq.py: the same three
# categories (conformance, completeness, plausibility), but roughly 4,000
# checks generated from ~20 check types applied across every table, field and
# concept the CDM defines, rather than the 31 we thought to write by hand.
#
# Results land in results/dqd/ as JSON, and can be browsed with:
#   DataQualityDashboard::viewDqDashboard("results/dqd/<file>.json")

suppressPackageStartupMessages({
  library(DatabaseConnector)
  library(DataQualityDashboard)
})

args <- commandArgs(trailingOnly = TRUE)
db_path <- if (length(args) >= 1) args[1] else "data/omop/omop.duckdb"
db_path <- normalizePath(db_path, mustWork = TRUE)

output_folder <- file.path("results", "dqd")
dir.create(output_folder, recursive = TRUE, showWarnings = FALSE)

cat("CDM      :", db_path, "\n")
cat("output   :", normalizePath(output_folder), "\n\n")

# DuckDB is addressed through DatabaseConnector's DBI path. The file itself is
# the "server"; DuckDB's default schema is `main`.
connectionDetails <- DatabaseConnector::createConnectionDetails(
  dbms   = "duckdb",
  server = db_path
)

cdmDatabaseSchema     <- "main"
resultsDatabaseSchema <- "main"

# Tables this ETL leaves empty. They are CREATED (so DQD can read the schema)
# but checking them produces thousands of vacuously-failing row-count checks
# that drown the signal from the tables that actually hold data.
#
# Left in deliberately: nothing. The first run checks EVERYTHING, because the
# point of the exercise is to see what a complete CDM expects. Narrow this on
# later runs if the noise gets in the way.
tables_to_exclude <- c()

start <- Sys.time()

results <- DataQualityDashboard::executeDqChecks(
  connectionDetails       = connectionDetails,
  cdmDatabaseSchema       = cdmDatabaseSchema,
  resultsDatabaseSchema   = resultsDatabaseSchema,
  cdmSourceName           = "Synthea FHIR R4 -> OMOP CDM 5.4",
  cdmVersion              = "5.4",
  outputFolder            = output_folder,
  outputFile              = "dqd-results.json",
  tablesToExclude         = tables_to_exclude,
  # Results are kept as JSON only. Writing them back into the CDM would put
  # a dqdashboard_results table inside the database we are auditing.
  writeToTable            = FALSE,
  writeToCsv              = TRUE,
  csvFile                 = file.path(output_folder, "dqd-results.csv"),
  verboseMode             = FALSE
)

elapsed <- round(difftime(Sys.time(), start, units = "mins"), 1)

cat("\n================ SUMMARY ================\n")
cat("elapsed        :", elapsed, "minutes\n")
cat("checks run     :", nrow(results$CheckResults), "\n")

passed <- sum(results$CheckResults$failed == 0 & results$CheckResults$isError == 0,
              na.rm = TRUE)
failed <- sum(results$CheckResults$failed == 1, na.rm = TRUE)
errored <- sum(results$CheckResults$isError == 1, na.rm = TRUE)

cat("passed         :", passed, "\n")
cat("failed         :", failed, "\n")
cat("errored        :", errored, "\n\n")

if (failed > 0) {
  cat("failures by category:\n")
  fails <- results$CheckResults[results$CheckResults$failed == 1, ]
  print(table(fails$category))
  cat("\nfailures by check type:\n")
  print(head(sort(table(fails$checkName), decreasing = TRUE), 15))
}

cat("\nview with:\n")
cat('  DataQualityDashboard::viewDqDashboard("',
    file.path(output_folder, "dqd-results.json"), '")\n', sep = "")
