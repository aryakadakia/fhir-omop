#!/usr/bin/env Rscript
#
# Define cohorts in R with Capr, compile them to SQL with CirceR, and generate
# them against the CDM with CohortGenerator.
#
#   Rscript scripts/build_cohorts.R
#
# WHY THIS RATHER THAN ATLAS
#
# ATLAS is the web UI most OHDSI work is done in, but its backend (WebAPI) does
# not support DuckDB -- only PostgreSQL, SQL Server, Oracle, Redshift and
# Snowflake. Running it would mean migrating the CDM and vocabulary to Postgres
# and standing up three Docker containers.
#
# Capr drives the SAME engine ATLAS does (circe-be). The cohort JSON produced
# here is the same JSON ATLAS produces, and CirceR compiles it with the same
# compiler. The difference is that here the JSON and the generated SQL are
# visible artefacts you can read, rather than hidden behind a UI.

suppressPackageStartupMessages({
  library(Capr)
  library(CirceR)
  library(CohortGenerator)
  library(DatabaseConnector)
})

db_path <- normalizePath("data/omop/omop.duckdb", mustWork = TRUE)
out_dir <- "cohorts"
dir.create(out_dir, showWarnings = FALSE)

connectionDetails <- DatabaseConnector::createConnectionDetails(
  dbms = "duckdb", server = db_path
)

cdmSchema     <- "main"
resultsSchema <- "main"

# --------------------------------------------------------------- concept sets
#
# cs() resolves descendants at generation time, which is the point: asking for
# "hypertensive disorder" picks up essential hypertension, pre-eclampsia and
# every other form without enumerating them. Writing the codes out by hand is
# how cohorts silently miss patients.

hypertension <- cs(
  descendants(316866),          # Hypertensive disorder
  name = "Hypertensive disorder (incl. descendants)"
)

t2dm <- cs(
  descendants(201826),          # Type 2 diabetes mellitus
  name = "Type 2 diabetes mellitus (incl. descendants)"
)

# Hydrochlorothiazide rather than an ACE inhibitor: lisinopril has zero
# exposures in this corpus, and a cohort that returns nobody demonstrates
# nothing. Verified against the data before choosing -- 260 people, 275 eras.
# It is also an antihypertensive, so it pairs with the hypertension cohort
# into a shape a real study would have.
hctz <- cs(
  descendants(974166),          # Hydrochlorothiazide
  name = "Hydrochlorothiazide (incl. descendants)"
)

# ------------------------------------------------------------------- cohorts

# 1. Adults with hypertension, requiring a year of prior observation.
#    The observation requirement is what makes this an INCIDENT cohort: without
#    it you cannot distinguish a new diagnosis from one that predates your data.
# age() is an ATTRIBUTE of the criterion, not an attrition rule: it constrains
# which condition occurrences qualify as an entry event, evaluated at the event
# date. Expressing it as attrition would filter people after entry instead,
# which is a different cohort.
hypertension_adults <- cohort(
  entry = entry(
    conditionOccurrence(hypertension, age(gte(18))),
    observationWindow = continuousObservation(priorDays = 365),
    primaryCriteriaLimit = "First"      # first qualifying event = index date
  ),
  exit = exit(endStrategy = observationExit())
)

# 2. Type 2 diabetes, no age restriction -- a deliberately simpler definition
#    so the two can be compared.
t2dm_any <- cohort(
  entry = entry(
    conditionOccurrence(t2dm),
    observationWindow = continuousObservation(priorDays = 365),
    primaryCriteriaLimit = "First"
  ),
  exit = exit(endStrategy = observationExit())
)

# 3. New users of hydrochlorothiazide. The new-user design is the backbone of
#    comparative drug studies: requiring no prior exposure removes prevalent
#    users, whose early adverse events already happened before you started
#    watching.
hctz_new_users <- cohort(
  entry = entry(
    drugExposure(hctz, firstOccurrence()),
    observationWindow = continuousObservation(priorDays = 365),
    primaryCriteriaLimit = "First"
  ),
  exit = exit(endStrategy = observationExit())
)

definitions <- list(
  "hypertension_adults" = hypertension_adults,
  "t2dm_any"            = t2dm_any,
  "hctz_new_users"      = hctz_new_users
)

# ------------------------------------------------- compile: Capr -> JSON -> SQL

cohortsToCreate <- CohortGenerator::createEmptyCohortDefinitionSet()

for (i in seq_along(definitions)) {
  nm <- names(definitions)[i]
  json <- as.json(definitions[[nm]])
  writeLines(json, file.path(out_dir, paste0(nm, ".json")))

  sql <- CirceR::buildCohortQuery(
    CirceR::cohortExpressionFromJson(json),
    options = CirceR::createGenerateOptions(generateStats = FALSE)
  )
  writeLines(sql, file.path(out_dir, paste0(nm, ".sql")))

  cohortsToCreate <- rbind(cohortsToCreate, data.frame(
    cohortId = i, cohortName = nm, sql = sql, json = json,
    stringsAsFactors = FALSE
  ))
  cat(sprintf("  built %-22s json %5d bytes   sql %6d bytes\n",
              nm, nchar(json), nchar(sql)))
}

# ------------------------------------------------------------------- generate

cohortTableNames <- CohortGenerator::getCohortTableNames(cohortTable = "cohort")

CohortGenerator::createCohortTables(
  connectionDetails = connectionDetails,
  cohortDatabaseSchema = resultsSchema,
  cohortTableNames = cohortTableNames
)

CohortGenerator::generateCohortSet(
  connectionDetails = connectionDetails,
  cdmDatabaseSchema = cdmSchema,
  cohortDatabaseSchema = resultsSchema,
  cohortTableNames = cohortTableNames,
  cohortDefinitionSet = cohortsToCreate,
  incremental = FALSE
)

counts <- CohortGenerator::getCohortCounts(
  connectionDetails = connectionDetails,
  cohortDatabaseSchema = resultsSchema,
  cohortTable = cohortTableNames$cohortTable
)

cat("\n================ COHORT COUNTS ================\n")
print(counts)
cat("\nJSON and SQL for each cohort written to", normalizePath(out_dir), "\n")
